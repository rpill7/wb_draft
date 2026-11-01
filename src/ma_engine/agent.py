"""Research and write one buyer report with a hand-written tool-calling loop.

The AI chooses MCP tools, reads their results and writes; it never calculates. Every tool result
it receives (plus its ranking entry and the target block shown in its prompt) is the buyer's
evidence for `guardrails.check`. A failed check gets one repair attempt; a report that still
fails is returned with status `not_verified`, never dropped.
"""

import asyncio
import json
import time

from ma_engine import config, guardrails
from ma_engine.models import BuyerReport, ReportDraft

ENGINE_ONLY_TOOLS = {"rank_acquirers"}
KICKOFF = "Research {name} for this target. Reply without tool calls when you have enough evidence."
REPEAT_NOTE = ("You already made this exact call for this buyer; this is the same result again. "
               "Use it, or call a different tool or arguments.")
WRITE_NOW = ("Write the report now in the required JSON format, following every hard rule and "
             "using only the evidence in this conversation.")


class ReportError(RuntimeError):
    """The model returned no report that matches the report format, even after repair."""


def load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def agent_tools(tools: list[dict]) -> list[dict]:
    """The MCP tools the research agent may call (ranking is run by the engine, not the AI)."""
    return [t for t in tools if t["function"]["name"] not in ENGINE_ONLY_TOOLS]


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _selection_instruction(entry: dict) -> str:
    note = entry.get("selection_note")
    if not note:
        return ""
    return (f"\nSelection note from the ranking: {note}\nThe first strategic_fit statement must say "
            "this plainly (a fact citing IDs from supporting_closed_ids): this buyer's place in the "
            "list comes from that rule, not from its score alone.\n")


def research_prompt(entry: dict, target: dict) -> str:
    """The system prompt for one buyer. `target` is the ranking's target block."""
    return load_prompt("research_writer").format(
        target=_json(target), buyer=_json(entry), selection_instruction=_selection_instruction(entry),
        max_rounds=config.MAX_TOOL_ROUNDS, acquirer_key=entry["acquirer_key"],
        sector=target["sector"], size_band=target["size_band"])


def repair_prompt(issues: list[str]) -> str:
    return load_prompt("repair").format(issues="\n".join(f"- {issue}" for issue in issues))


def llm_record(purpose: str, result) -> dict:
    return {"purpose": purpose, "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens, "cost_usd": result.cost_usd,
            "cache_hit": result.cache_hit, "duration_ms": result.duration_ms,
            "tool_calls_requested": len(result.tool_calls),
            "parse_error": result.parse_error, "dropped_parameters": result.dropped_parameters,
            "warnings": result.warnings}


async def _run_tool(mcp, call: dict, allowed: set[str], round_no: int, trace: dict,
                    cache: dict) -> tuple:
    """Run one tool call. An identical call (same name and arguments) made earlier for this
    buyer is answered from `cache` with a note, instead of calling the server again."""
    started = time.perf_counter()
    name, repeated = call["name"], False
    try:
        arguments = json.loads(call["arguments"] or "{}")
        if not isinstance(arguments, dict):
            raise ValueError
    except ValueError:
        arguments = call["arguments"]
        output = {"error": "Arguments must be a JSON object matching the tool's parameters."}
    else:
        if name in allowed:
            key = (name, json.dumps(arguments, sort_keys=True))
            repeated = key in cache
            if not repeated:
                cache[key] = asyncio.ensure_future(mcp.call_tool(name, arguments))
            output = await cache[key]
            if repeated:
                output = {**output, "warnings": [*output.get("warnings", []), REPEAT_NOTE]}
        else:
            output = {"error": f"Unknown tool {name!r}. Available tools: {', '.join(sorted(allowed))}."}
    trace["tool_calls"].append({
        "round": round_no, "tool": name, "arguments": arguments, "repeated": repeated,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "status": "error" if "error" in output else "ok",
        "error": output.get("error"), "result_chars": len(_json(output)),
        "source_id_count": len(output.get("source_ids", [])),
    })
    return arguments, output


async def _research(entry, target, llm, mcp, use_cache, trace, notify) -> tuple[list, list]:
    """The tool loop. Returns the conversation and the successful tool results."""
    tools = agent_tools(mcp.tools)
    allowed = {t["function"]["name"] for t in tools}
    messages = [{"role": "system", "content": research_prompt(entry, target)},
                {"role": "user", "content": KICKOFF.format(name=entry["display_name"])}]
    tool_results, cache = [], {}
    for round_no in range(1, config.MAX_TOOL_ROUNDS + 1):
        result = await llm.chat(messages, tools=tools, use_cache=use_cache)
        trace["llm_calls"].append(llm_record(f"research round {round_no}", result))
        messages.append(result.assistant_message())
        if not result.tool_calls:
            trace["stop_reason"] = "model finished research"
            break
        trace["rounds"] = round_no
        notify(f"{entry['display_name']}: round {round_no}, "
               f"{', '.join(c['name'] for c in result.tool_calls)}")
        outputs = await asyncio.gather(*(_run_tool(mcp, call, allowed, round_no, trace, cache)
                                         for call in result.tool_calls))
        for call, (arguments, output) in zip(result.tool_calls, outputs):
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": _json(output)})
            if "error" not in output:
                tool_results.append({"tool": call["name"], "arguments": arguments, "result": output})
    else:
        trace["stop_reason"] = f"reached the maximum of {config.MAX_TOOL_ROUNDS} tool rounds"
    return messages, tool_results


async def _write(llm, messages, tools, purpose, use_cache, trace):
    result = await llm.chat(messages, tools=tools, tool_choice="none", response_schema=ReportDraft,
                            use_cache=use_cache)
    trace["llm_calls"].append(llm_record(purpose, result))
    return result


async def research_buyer(entry: dict, target: dict, llm, mcp, use_cache: bool = True,
                         progress=None) -> tuple[BuyerReport, list[dict], dict]:
    """Research one buyer and write its checked report.

    `entry` is the buyer's ranking entry and `target` the ranking's target block. Returns the
    report (status set by the guardrails), the evidence tool results and the trace. An exception
    raised here carries the trace collected so far as its `trace` attribute.
    """
    trace = {"acquirer_key": entry["acquirer_key"], "display_name": entry["display_name"],
             "rounds": 0, "stop_reason": None, "tool_calls": [], "llm_calls": []}
    try:
        return await _research_and_write(entry, target, llm, mcp, use_cache,
                                         progress or (lambda event: None), trace)
    except Exception as exc:
        exc.trace = trace
        raise


async def _research_and_write(entry, target, llm, mcp, use_cache, notify, trace) -> tuple:
    started = time.perf_counter()
    messages, tool_results = await _research(entry, target, llm, mcp, use_cache, trace, notify)
    # The target block is in the prompt, so its display strings are quotable evidence too.
    evidence_results = [{"tool": "rank_acquirers", "arguments": {}, "result": {"data": {"target": target}}},
                        *tool_results]
    evidence = guardrails.build_evidence(entry, evidence_results)
    tools = agent_tools(mcp.tools)

    messages.append({"role": "user", "content": WRITE_NOW})
    written = await _write(llm, messages, tools, "write report", use_cache, trace)
    draft = written.parsed
    issues = [written.parse_error] if draft is None else guardrails.check(
        BuyerReport.from_ranking(entry, draft), evidence)
    trace["first_check_issues"] = issues
    trace["repair_attempts"] = 0
    for attempt in range(1, config.MAX_REPAIR_ATTEMPTS + 1):
        if not issues:
            break
        trace["repair_attempts"] = attempt
        notify(f"{entry['display_name']}: repairing {len(issues)} issue(s)")
        messages += [written.assistant_message(), {"role": "user", "content": repair_prompt(issues)}]
        written = await _write(llm, messages, tools, f"repair {attempt}", use_cache, trace)
        if written.parsed is None:
            issues = [written.parse_error]
            continue
        draft = written.parsed
        issues = guardrails.check(BuyerReport.from_ranking(entry, draft), evidence)
    if draft is None:
        raise ReportError(f"{entry['display_name']}: no valid report after "
                          f"{config.MAX_REPAIR_ATTEMPTS} repair attempt(s): {issues[0]}")

    report = guardrails.apply_check(BuyerReport.from_ranking(entry, draft), evidence)
    trace["final_status"] = report.status
    trace["final_issues"] = report.issues
    trace["length_warnings"] = guardrails.length_warnings(report)
    trace["evidence"] = {"tool_results": len(tool_results), "ids": len(evidence.allowed_ids),
                         "numbers": len(evidence.allowed_numbers),
                         "sources": len(evidence.sources)}
    trace["duration_ms"] = round((time.perf_counter() - started) * 1000)
    notify(f"{entry['display_name']}: report {report.status.replace('_', ' ')}")
    return report, evidence_results, trace
