"""`run_engine(target)`: the full pipeline for one target.

1. Open one LLM client and one MCP session for the run.
2. Rank buyers with the `rank_acquirers` MCP tool (Python, no AI) and drop excluded buyers, then
   read the model's supported parameters once, so a wrong model id stops the run before research.
   The run folder is created only after both succeed (the MCP server log waits in a hidden file
   in `config.RUNS_DIR` until then), so a run stopped early leaves no folder.
3. Research the top `config.REPORT_COUNT` buyers in parallel. A buyer that fails with an error
   (not a guardrail failure) is replaced by a reserve: one of the same type if losing it breaks
   the buyer-type rule, otherwise the next one. Ranks are renumbered 1..N afterwards, and the
   failed buyer's error and partial trace are kept. Errors that would hit every buyer the same
   way (configuration, authentication, rate limits) stop the run instead.
4. Committee review: one LLM call over all reports may move each conviction by one step; a
   decision is applied only if the conviction still passes the guardrails for that buyer.
5. Save `reports.json`, `reports.md` and `trace.json` in `outputs/runs/<run_id>/`.

Run headless with `uv run python -m ma_engine.engine` (default target).
"""

import asyncio
import itertools
import json
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import openai

from ma_engine import config, guardrails
from ma_engine.agent import llm_record, load_prompt, research_buyer
from ma_engine.clients.llm_client import LLMClient, LLMConfigError
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.models import BuyerReport, CommitteeReview, Conviction, Target
from ma_engine.ranking import MIN_PER_TYPE

# Errors that would hit every buyer the same way: stop the run instead of substituting.
# A rate limit reaching this point has already survived the SDK's retries.
FATAL_ERRORS = (LLMConfigError, openai.AuthenticationError, openai.PermissionDeniedError,
                openai.RateLimitError)
MAX_COMMITTEE_FACTS = 6


def _new_run_dir():
    """A new run folder named by the time, with -2, -3... added if that name is taken."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for n in itertools.count(1):
        run_id = stamp if n == 1 else f"{stamp}-{n}"
        try:
            (config.RUNS_DIR / run_id).mkdir()
        except FileExistsError:
            continue
        return run_id, config.RUNS_DIR / run_id


def _pending_log() -> Path:
    """The MCP server log until the run folder exists. Same disk as the runs, so it can be
    renamed into the run folder while the server still writes to it."""
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix=".mcp_server-", suffix=".log", dir=config.RUNS_DIR)
    os.close(handle)
    return Path(path)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _rank(mcp, target: Target, excluded_keys) -> dict:
    result = await mcp.call_tool("rank_acquirers", {
        **target.model_dump(), "limit": config.SHORTLIST_SIZE, "excluded_keys": list(excluded_keys)})
    if "error" in result:
        raise ValueError(f"Ranking failed: {result['error']}")
    return result


def pick_reserve(failed: dict, top: list, reserves: list) -> tuple[dict | None, bool]:
    """Remove and return the reserve to research instead of `failed`, with whether the type rule
    chose it. A reserve of the same type is preferred when losing `failed` leaves fewer than
    MIN_PER_TYPE of its type among the other buyers in `top`."""
    if not reserves:
        return None, False
    kind = failed["acquirer_type"]
    left = sum(e is not None and e is not failed and e["acquirer_type"] == kind for e in top)
    same_type = next((r for r in reserves if r["acquirer_type"] == kind), None)
    by_type = left < MIN_PER_TYPE and same_type is not None
    pick = same_type if by_type else reserves[0]
    reserves.remove(pick)
    return {**pick, "selection_note": f"Replaced {failed['display_name']} after an error"}, by_type


async def _research_all(shortlist, target_block, llm, mcp, use_cache, notify) -> tuple:
    """Reports for the top buyers, replacing any buyer whose research raises a per-buyer error."""
    semaphore = asyncio.Semaphore(config.MAX_PARALLEL_BUYERS)
    top = list(shortlist[:config.REPORT_COUNT])
    reserves = list(shortlist[config.REPORT_COUNT:])
    errors, substitutions = [], []

    async def slot(index):
        while True:
            entry = top[index]
            try:
                async with semaphore:
                    notify(f"Researching {entry['display_name']}")
                    return entry, await research_buyer(entry, target_block, llm, mcp, use_cache,
                                                       notify)
            except FATAL_ERRORS:
                raise
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                errors.append({"acquirer_key": entry["acquirer_key"], "error": message,
                               "partial_trace": getattr(exc, "trace", None)})
                notify(f"{entry['display_name']}: failed ({message})")
                replacement, by_type = pick_reserve(entry, top, reserves)
                top[index] = replacement
                if replacement is None:
                    return entry, None
                substitutions.append({"failed": entry["acquirer_key"],
                                      "replacement": replacement["acquirer_key"],
                                      "replacement_shortlist_rank": replacement["rank"],
                                      "same_type_rule": by_type, "error": message})
                notify(f"Substituting {replacement['display_name']} for {entry['display_name']}")

    tasks = [asyncio.ensure_future(slot(i)) for i in range(len(top))]
    try:
        outcomes = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    return [o for o in outcomes if o[1] is not None], errors, substitutions


# ---------- committee review ----------

def _summary(report: BuyerReport, entry: dict) -> dict:
    draft = report.draft
    facts = [s for s in draft.strategic_fit + draft.precedent_activity + draft.valuation_context
             if s.kind == "fact"][:MAX_COMMITTEE_FACTS]
    return {
        "acquirer_key": report.acquirer_key, "display_name": report.display_name,
        "acquirer_type": report.acquirer_type, "rank": report.rank, "score": report.score,
        "starting_level": report.starting_level,
        "starting_reason": entry["starting_conviction"]["reason"],
        "proposed_level": draft.conviction.level, "proposed_reason": draft.conviction.reason,
        "proposed_reason_ids": draft.conviction.source_ids, "report_status": report.status,
        "strategic_fit": [s.text for s in draft.strategic_fit],
        "key_facts": [{"text": s.text, "source_ids": s.source_ids} for s in facts],
    }


def committee_prompt(reports, entries, ranking_data) -> str:
    target = ranking_data["target"]
    return load_prompt("committee").format(
        target=json.dumps(target, ensure_ascii=False), sector=target["sector"],
        conviction_rule=ranking_data["conviction_rule"],
        summaries=json.dumps([_summary(r, entries[r.acquirer_key]) for r in reports],
                             ensure_ascii=False, indent=1))


def apply_committee(reports: list[BuyerReport], review: CommitteeReview, evidence: dict) -> tuple:
    """Reports with accepted decisions applied, plus a record of what was applied or rejected."""
    by_key = {r.acquirer_key: r for r in reports}
    record = {"applied": [], "rejected": [], "missing": [],
              "similar_pairs": [p.model_dump() for p in review.similar_pairs]}
    decided = set()
    for decision in review.decisions:
        key = decision.acquirer_key
        if key not in by_key or key in decided:
            reason = "unknown acquirer_key" if key not in by_key else "duplicate decision"
            record["rejected"].append({"acquirer_key": key, "issues": [reason]})
            continue
        decided.add(key)
        report = by_key[key]
        conviction = Conviction(level=decision.level, reason=decision.reason,
                                source_ids=decision.source_ids)
        candidate = report.model_copy(update={
            "draft": report.draft.model_copy(update={"conviction": conviction})})
        issues = [i for i in guardrails.check(candidate, evidence[key]) if i.startswith("conviction")]
        if issues:
            record["rejected"].append({"acquirer_key": key, "level": decision.level, "issues": issues})
            continue
        record["applied"].append({"acquirer_key": key, "starting_level": report.starting_level,
                                  "writer_level": report.draft.conviction.level,
                                  "final_level": decision.level})
        by_key[key] = guardrails.apply_check(candidate, evidence[key])
    record["missing"] = sorted(set(by_key) - decided)
    return [by_key[r.acquirer_key] for r in reports], record


async def _committee(reports, entries, evidence, ranking_data, llm, use_cache) -> tuple:
    messages = [{"role": "user", "content": committee_prompt(reports, entries, ranking_data)}]
    result = await llm.chat(messages, response_schema=CommitteeReview, use_cache=use_cache)
    if result.parsed is None:
        return reports, {"llm": llm_record("committee", result), "error": "Committee output was "
                         f"invalid, so the writers' levels were kept: {result.parse_error}"}
    reports, record = apply_committee(reports, result.parsed, evidence)
    return reports, {"llm": llm_record("committee", result), "error": None, **record}


# ---------- saving ----------

def _write_json(path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")


def save_run(run_dir, run_id: str, target: Target, reports: list[BuyerReport], trace: dict) -> None:
    _write_json(run_dir / "reports.json", {"run_id": run_id, "target": target.model_dump(),
                                           "reports": [r.model_dump() for r in reports]})
    (run_dir / "reports.md").write_text(guardrails.render_run_markdown(reports), encoding="utf-8")
    _write_json(run_dir / "trace.json", trace)


# ---------- pipeline ----------

async def run_engine(target: Target | dict, use_cache: bool = True, excluded_keys=(),
                     progress=None) -> dict:
    """Rank, research, review and save one run. `progress(event)` receives live step messages."""
    started = time.perf_counter()
    target = target if isinstance(target, Target) else Target.model_validate(target)
    notify = progress or (lambda event: None)
    llm = LLMClient()
    trace = {"run_id": None, "model": llm.model, "target": target.model_dump(),
             "use_cache": use_cache, "excluded_keys": list(excluded_keys)}
    log = _pending_log()
    mcp = MCPClient(log_file=log)
    try:
        async with mcp:
            notify("Ranking buyers")
            ranking_started = time.perf_counter()
            ranking = (await _rank(mcp, target, excluded_keys))["data"]
            trace["ranking_ms"] = _ms(ranking_started)
            shortlist = ranking["buyers"]
            await llm.supported_parameters()
            run_id, run_dir = _new_run_dir()
            trace["run_id"] = run_id
            log.rename(run_dir / "mcp_server.log")
            notify(f"Ranked {ranking['candidate_count']} candidates; researching the top "
                   f"{min(config.REPORT_COUNT, len(shortlist))}")

            research_started = time.perf_counter()
            outcomes, errors, substitutions = await _research_all(
                shortlist, ranking["target"], llm, mcp, use_cache, notify)
            trace["research_ms"] = _ms(research_started)
            outcomes.sort(key=lambda o: o[0]["rank"])
            entries = {e["acquirer_key"]: e for e, _ in outcomes}
            reports = [result[0].model_copy(update={"rank": rank})
                       for rank, (_, result) in enumerate(outcomes, 1)]
            evidence = {e["acquirer_key"]: guardrails.build_evidence(e, result[1])
                        for e, result in outcomes}

            committee = {"skipped": "no reports"}
            if reports:
                notify("Committee review")
                committee_started = time.perf_counter()
                reports, committee = await _committee(reports, entries, evidence, ranking, llm,
                                                      use_cache)
                committee["duration_ms"] = _ms(committee_started)
    finally:
        log.unlink(missing_ok=True)

    trace.update({
        "wall_time_s": round(time.perf_counter() - started, 2),
        "llm_totals": llm.totals,
        "mcp": {"sessions_opened": mcp.sessions_opened,
                "tools": [t["function"]["name"] for t in mcp.tools]},
        "ranking": {"candidate_count": ranking["candidate_count"],
                    "shortlist": [b["acquirer_key"] for b in shortlist],
                    "promoted_keys": ranking["promoted_keys"],
                    "displaced_keys": ranking["displaced_keys"],
                    "reserve_added_keys": ranking["reserve_added_keys"]},
        "errors": errors, "substitutions": substitutions, "committee": committee,
        "buyers": [result[2] for _, result in outcomes],
        "quality": guardrails.quality_metrics(reports),
    })
    save_run(run_dir, run_id, target, reports, trace)
    notify(f"Saved run to {run_dir}")
    return {"run_id": run_id, "run_dir": str(run_dir), "target": target, "ranking": ranking,
            "reports": reports, "trace": trace}


def print_summary(run: dict) -> None:
    trace, reports = run["trace"], run["reports"]
    totals = trace["llm_totals"]
    verified = sum(r.status == "verified" for r in reports)
    mix = Counter(r.draft.conviction.level for r in reports)
    print(f"\nRun {run['run_id']} with {trace['model']}")
    print(f"Wall time: {trace['wall_time_s']} s (ranking {trace['ranking_ms']} ms, research "
          f"{trace['research_ms']} ms)")
    print(f"LLM calls: {totals['calls']} ({totals['cache_hits']} from cache); tokens in "
          f"{totals['prompt_tokens']}, out {totals['completion_tokens']}; cost "
          f"${totals['cost_usd']:.4f}")
    print(f"Reports: {len(reports)}, verified {verified}; conviction "
          + ", ".join(f"{level} {mix.get(level, 0)}" for level in ("High", "Medium", "Low")))
    if trace["substitutions"]:
        print(f"Substitutions: {trace['substitutions']}")
    print(f"Run folder: {run['run_dir']}")


def main() -> None:
    try:
        run = asyncio.run(run_engine(Target.default(), progress=print))
    except (RuntimeError, *FATAL_ERRORS) as exc:
        print(f"Run stopped: {type(exc).__name__}: {' '.join(str(exc).split())}", file=sys.stderr)
        raise SystemExit(1)
    print_summary(run)


if __name__ == "__main__":
    main()
