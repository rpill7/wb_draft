"""`run_engine(target)`: the full pipeline for one target.

1. Open one LLM client and one MCP session for the run. Nothing is sent to `progress` until
   that session is open, so a server that fails to start leaves an empty event list.
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
   Applying the decisions is a calculation step.
5. Save `reports.json`, `reports.md`, `ranking.json` and `trace.json` in
   `outputs/runs/<run_id>/`. `trace["events"]` is the activity list.

`progress(event)` receives the event dicts described in `agent.py` (each has `message`, so the
CLI can print `event["message"]`). Saved runs: `load_run(run_dir)["trace"]["events"]`.

Public functions
----------------
`run_engine(target, use_cache=True, excluded_keys=(), progress=None, request=None) -> dict`
    `{run_id, run_dir, target: Target, ranking, reports, trace}`.
    `ranking` is the `rank_acquirers` data dict (also written to `ranking.json`).
    `trace["events"]` is the activity list. `trace["totals"]` is `wall_time_s`, `llm_calls`,
    `mcp_tool_calls`, `tokens_in`, `tokens_out`, `cost_usd`, `cache_hits`.
    `request` is the user's sentence, stored on `trace["request"]` and on the first
    `run` event's input. It is None when the caller did not pass one.

`load_run(run_dir) -> dict`
    Same keys as `run_engine`. `ranking` is None when the folder has no `ranking.json`.
    `trace` is `{}` when the folder has no `trace.json`. Reports come from
    `guardrails.load_reports`.

`parse_target(text, use_cache=True, progress=None) -> tuple[Target, list[str]]`
    One LLM client, then `agent.parse_request`. Returns `(target, defaulted)`. The UI shows
    that target and then calls `run_engine` itself. Events go to `progress`.

`run_followup(question, run, use_cache=True, progress=None) -> FollowupResult`
    One LLM client and exactly one MCP session. Appends the answer to `followups.json`
    (a list) in the run folder. `run` must include `run_dir` (as `run_engine` and `load_run`
    return).

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
from ma_engine.agent import (Steps, activity_totals, answer_followup, call_llm, close_step,
                             llm_record, load_prompt, open_step, parse_request, research_buyer,
                             tool_decision, tool_output_summary)
from ma_engine.clients.llm_client import scrub_secret
from ma_engine.clients.llm_client import LLMClient, LLMConfigError
from ma_engine.clients.mcp_client import MCPClient
from ma_engine.models import BuyerReport, CommitteeReview, Conviction, FollowupResult, Target
from ma_engine.ranking import MIN_PER_TYPE

# Errors that would hit every buyer the same way: stop the run instead of substituting.
# A rate limit reaching this point has already survived this process's own retries.
FOLLOWUPS_FILE = "followups.json"
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


async def _rank(mcp, target: Target, excluded_keys, steps: Steps, progress) -> dict:
    """Call `rank_acquirers` and record the call. The return value is the tool's own dict."""
    arguments = {**target.model_dump(), "limit": config.SHORTLIST_SIZE,
                 "excluded_keys": list(excluded_keys)}
    opened = open_step(steps, progress, category="mcp_tool", name="rank_acquirers",
                       message="Ranking buyers", input=arguments)
    started = time.perf_counter()
    try:
        result = await mcp.call_tool("rank_acquirers", arguments)
    except Exception as exc:
        error = scrub_secret(f"{type(exc).__name__}: {exc}")
        close_step(progress, opened, status="failed", duration_ms=_ms(started), error=error,
                   message=f"Ranking failed: {error}", decision=f"rank_acquirers failed: {error}")
        raise
    duration = _ms(started)
    failed = "error" in result
    error = result.get("error")
    decision = tool_decision("rank_acquirers", result, False)
    close_step(progress, opened, status="failed" if failed else "completed", duration_ms=duration,
               message=f"Ranking failed: {error}" if failed else f"rank_acquirers finished in {duration} ms",
               output=result, output_summary=tool_output_summary(result),
               error=scrub_secret(error) if isinstance(error, str) else None, decision=decision)
    if failed:
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


async def _research_all(shortlist, target_block, llm, mcp, use_cache, notify, steps) -> tuple:
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
                    return entry, await research_buyer(entry, target_block, llm, mcp, use_cache,
                                                       notify, steps)
            except FATAL_ERRORS:
                raise
            except Exception as exc:
                message = scrub_secret(f"{type(exc).__name__}: {exc}")
                errors.append({"acquirer_key": entry["acquirer_key"], "error": message,
                               "partial_trace": getattr(exc, "trace", None)})
                replacement, by_type = pick_reserve(entry, top, reserves)
                top[index] = replacement
                if replacement is None:
                    _emit_buyer_failed(steps, notify, entry, message)
                    return entry, None
                substitutions.append({"failed": entry["acquirer_key"],
                                      "failed_name": entry["display_name"],
                                      "replacement": replacement["acquirer_key"],
                                      "replacement_name": replacement["display_name"],
                                      "replacement_shortlist_rank": replacement["rank"],
                                      "same_type_rule": by_type, "error": message})
                _emit_substitution(steps, notify, substitutions[-1])

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


async def _committee(reports, entries, evidence, ranking_data, llm, use_cache, steps, progress):
    """One committee model call, then a calculation step that applies the decisions."""
    messages = [{"role": "user", "content": committee_prompt(reports, entries, ranking_data)}]
    result = await call_llm(llm, messages, purpose="committee", steps=steps, progress=progress,
                            use_cache=use_cache, response_schema=CommitteeReview,
                            display_name="Committee", running_message="Committee review")
    opened = open_step(steps, progress, category="calculation", name="apply_committee",
                       message="Applying the committee's decisions")
    if result.parsed is None:
        decision = "Committee output was invalid, so the writers' levels were kept"
        close_step(progress, opened, status="completed", duration_ms=0, message=decision,
                   decision=decision, error=result.parse_error,
                   output_summary={"applied": 0, "rejected": 0})
        return reports, {"llm": llm_record("committee", result), "error": f"{decision}: "
                         f"{result.parse_error}"}
    started = time.perf_counter()
    reports, record = apply_committee(reports, result.parsed, evidence)
    applied, rejected = len(record["applied"]), len(record["rejected"])
    decision = f"Applied {applied} conviction changes; rejected {rejected}"
    close_step(progress, opened, status="completed", duration_ms=_ms(started),
               message=(f"Committee review done: {applied} change(s) applied, {rejected} rejected"),
               decision=decision, output_summary={"applied": applied, "rejected": rejected},
               output={"applied": record["applied"], "rejected": record["rejected"],
                       "missing": record["missing"]})
    return reports, {"llm": llm_record("committee", result), "error": None, **record}


# ---------- progress events ----------

def _emit_substitution(steps: Steps, progress, sub: dict) -> None:
    message = f"Substituting {sub['replacement_name']} for {sub['failed_name']}"
    opened = open_step(steps, progress, category="app", name="substitution", message=message,
                       acquirer_key=sub["replacement"],
                       input={"failed": sub["failed"], "replacement": sub["replacement"],
                              "same_type_rule": sub["same_type_rule"], "error": sub["error"]})
    close_step(progress, opened, status="completed", message=message, duration_ms=0,
               decision=message, output={"failed_name": sub["failed_name"],
                                         "replacement_name": sub["replacement_name"],
                                         "same_type_rule": sub["same_type_rule"]})


def _emit_buyer_failed(steps: Steps, progress, entry: dict, error: str) -> None:
    """A buyer errored and no reserve was left to take the slot."""
    message = f"{entry['display_name']}: failed ({error})"
    opened = open_step(steps, progress, category="app", name="buyer_failed", message=message,
                       acquirer_key=entry["acquirer_key"], input={"error": error})
    close_step(progress, opened, status="failed", message=message, duration_ms=0, error=error,
               decision=f"{entry['display_name']} failed and no reserve was left")


def _emit_ranking(steps: Steps, progress, ranking: dict) -> None:
    """Mark the rank_acquirers result as the Python ranking. The tool call is recorded separately."""
    short = min(config.REPORT_COUNT, len(ranking["buyers"]))
    message = f"Ranked {ranking['candidate_count']} candidates; researching the top {short}"
    opened = open_step(steps, progress, category="calculation", name="ranking",
                       message="Scoring the ranking", input={
                           "candidate_count": ranking["candidate_count"]})
    close_step(progress, opened, status="completed", message=message, duration_ms=0,
               decision=f"Ranked {ranking['candidate_count']} candidates",
               output_summary={"candidates": ranking["candidate_count"],
                               "shortlist": len(ranking["buyers"])},
               output={"candidate_count": ranking["candidate_count"],
                       "shortlist": [b["acquirer_key"] for b in ranking["buyers"]],
                       "promoted_keys": ranking["promoted_keys"],
                       "displaced_keys": ranking["displaced_keys"]})


# ---------- saving ----------

def _write_json(path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")


def save_run(run_dir, run_id: str, target: Target, reports: list[BuyerReport], trace: dict,
             ranking: dict) -> None:
    _write_json(run_dir / "reports.json", {"run_id": run_id, "target": target.model_dump(),
                                           "reports": [r.model_dump() for r in reports]})
    (run_dir / "reports.md").write_text(guardrails.render_run_markdown(reports), encoding="utf-8")
    _write_json(run_dir / "ranking.json", ranking)
    _write_json(run_dir / "trace.json", trace)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_run(run_dir) -> dict:
    """A saved run in the shape `run_engine` returns. `ranking` is None and `trace` is {} for
    folders saved without ranking.json or trace.json."""
    run_dir = Path(run_dir)
    saved, reports = guardrails.load_reports(run_dir)
    return {"run_id": saved["run_id"], "run_dir": str(run_dir),
            "target": Target.model_validate(saved["target"]),
            "ranking": _read_json(run_dir / "ranking.json"), "reports": reports,
            "trace": _read_json(run_dir / "trace.json") or {}}


# ---------- pipeline ----------

async def run_engine(target: Target | dict, use_cache: bool = True, excluded_keys=(),
                     progress=None, request: str | None = None) -> dict:
    """Rank, research, review and save one run. `progress(event)` receives live event dicts.

    `request` is the user's sentence, stored for the screen. It is not interpreted.
    """
    started = time.perf_counter()
    target = target if isinstance(target, Target) else Target.model_validate(target)
    recorded: list[dict] = []

    def notify(event: dict) -> None:
        recorded.append(event)
        if progress is not None:
            progress(event)

    steps = Steps()
    llm = LLMClient()
    trace = {"run_id": None, "model": llm.model, "target": target.model_dump(),
             "use_cache": use_cache, "excluded_keys": list(excluded_keys),
             "request": request, "events": recorded}
    log = _pending_log()
    mcp = MCPClient(log_file=log)
    try:
        async with mcp:
            # No event before this point: a server that fails to start leaves the list empty.
            tool_names = [t["function"]["name"] for t in mcp.tools]
            run_line = (f"Run started: {target.sector}, EV ${target.ev_mm:g}M, "
                        f"{target.geography}, {target.ownership}, {target.margin_profile} margins")
            opened = open_step(steps, notify, category="app", name="run",
                               message="Starting the run",
                               input={**target.model_dump(), "request": request})
            close_step(notify, opened, status="completed", message=run_line, duration_ms=0,
                       decision="Started the run")
            connected = f"MCP server connected ({len(tool_names)} tools)"
            opened = open_step(steps, notify, category="app", name="mcp_connected",
                               message=connected)
            close_step(notify, opened, status="completed", message=connected, duration_ms=0,
                       decision=connected, output={"tools": tool_names},
                       output_summary={"tools": len(tool_names)})

            ranking_started = time.perf_counter()
            ranking = (await _rank(mcp, target, excluded_keys, steps, notify))["data"]
            trace["ranking_ms"] = _ms(ranking_started)
            shortlist = ranking["buyers"]
            _emit_ranking(steps, notify, ranking)
            await llm.supported_parameters()
            run_id, run_dir = _new_run_dir()
            trace["run_id"] = run_id
            log.rename(run_dir / "mcp_server.log")

            research_started = time.perf_counter()
            outcomes, errors, substitutions = await _research_all(
                shortlist, ranking["target"], llm, mcp, use_cache, notify, steps)
            trace["research_ms"] = _ms(research_started)
            outcomes.sort(key=lambda o: o[0]["rank"])
            entries = {e["acquirer_key"]: e for e, _ in outcomes}
            reports = [result[0].model_copy(update={"rank": rank})
                       for rank, (_, result) in enumerate(outcomes, 1)]
            evidence = {e["acquirer_key"]: guardrails.build_evidence(e, result[1])
                        for e, result in outcomes}

            committee = {"skipped": "no reports"}
            if reports:
                committee_started = time.perf_counter()
                reports, committee = await _committee(reports, entries, evidence, ranking, llm,
                                                      use_cache, steps, notify)
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
    opened = open_step(steps, notify, category="app", name="save", message="Saving the run",
                       input={"run_id": run_id})
    save_started = time.perf_counter()
    save_run(run_dir, run_id, target, reports, trace, ranking)
    saved = f"Saved run to {run_dir}"
    close_step(notify, opened, status="completed", message=saved, duration_ms=_ms(save_started),
               decision=f"Saved reports, ranking and trace to {run_dir}",
               output={"run_id": run_id, "run_dir": str(run_dir),
                       "files": ["reports.json", "reports.md", "ranking.json", "trace.json"]})
    trace["wall_time_s"] = round(time.perf_counter() - started, 2)
    trace["totals"] = activity_totals(recorded, llm.totals, trace["wall_time_s"])
    _write_json(run_dir / "trace.json", trace)
    return {"run_id": run_id, "run_dir": str(run_dir), "target": target, "ranking": ranking,
            "reports": reports, "trace": trace}


async def parse_target(text: str, use_cache: bool = True,
                       progress=None) -> tuple[Target, list[str]]:
    """One LLM client around `agent.parse_request`. Returns `(target, defaulted)`.

    Events go to `progress`. Show the target, then call `run_engine` when the user confirms.
    """
    return await parse_request(text, LLMClient(), use_cache=use_cache, progress=progress)


def _append_followup(run_dir: Path, result: FollowupResult) -> None:
    path = run_dir / FOLLOWUPS_FILE
    saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if not isinstance(saved, list):
        raise ValueError(f"{path.name} is not a list of follow-ups.")
    saved.append(result.model_dump(mode="json"))
    _write_json(path, saved)


async def run_followup(question: str, run: dict, use_cache: bool = True,
                       progress=None) -> FollowupResult:
    """Answer one follow-up with one LLM client and exactly one MCP session.

    Appends the answer to `followups.json` in the run folder so a saved run keeps its Q&A.
    """
    run_dir = run.get("run_dir")
    if not run_dir:
        raise ValueError("This run has no folder (run_dir), so the follow-up cannot be saved.")
    llm = LLMClient()
    log = _pending_log()
    mcp = MCPClient(log_file=log)
    try:
        async with mcp:
            result = await answer_followup(question, run, llm, mcp, use_cache, progress)
    finally:
        log.unlink(missing_ok=True)
    _append_followup(Path(run_dir), result)
    return result


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
        run = asyncio.run(run_engine(Target.default(), progress=lambda e: print(e["message"])))
    except (RuntimeError, *FATAL_ERRORS) as exc:
        print(f"Run stopped: {type(exc).__name__}: {' '.join(str(exc).split())}", file=sys.stderr)
        raise SystemExit(1)
    print_summary(run)


if __name__ == "__main__":
    main()
