"""Read-only saved-run context for follow-ups, with separately cited run metadata."""
import json
import re
from ma_engine.clients.data_client import get_client, slug
from ma_engine.prepare import fmt_money


def saved_context(run, question):
    trace = run.get("trace") or {}
    ranking = run.get("ranking") or {}
    merged = {e.get("step"): e for e in trace.get("events", [])}
    activity = [{k: e.get(k) for k in ("step", "category", "name", "acquirer_key", "status",
                                      "input", "output_summary", "attempts", "error")}
                for e in merged.values()]
    metadata = {
        "run_id": run.get("run_id"), "totals": trace.get("totals"),
        "cost_display": f"${trace['totals']['cost_usd']}" if (trace.get("totals") or {}).get("cost_usd") is not None else None,
        "activity": activity, "weights": ranking.get("weights"),
        "candidate_count": ranking.get("candidate_count"),
        "selection_policy": ranking.get("selection_policy"),
        "workflow": {
            "initial_ranking_and_starting_conviction": "rank_acquirers reads the stored historical transaction data and computes the initial score and starting conviction before the per-buyer LLM research calls.",
            "buyer_research": "get_acquirer_profile and get_acquirer_deals provide additional buyer evidence after initial ranking; report writing and committee review can change final conviction within the allowed bounds.",
            "ranking_step": next((e.get("step") for e in merged.values() if e.get("name") == "rank_acquirers"), None),
            "first_buyer_profile_step": next((e.get("step") for e in merged.values() if e.get("name") == "get_acquirer_profile"), None),
        },
        "methodology": {
            "adjacency": "Relevant sectors and their closeness weights are predefined scoring assumptions, not a dynamic decision triggered by too few direct deals.",
            "valuation": "Closed transactions only. Each multiple excludes rows missing that multiple; sample size n counts populated values for that metric. Missing values are not zero and are not imputed. Recalculate in Python to quantify a changed sample, never in the LLM.",
            "cache": "Totals describe the original execution, including cache_hits. Reopening a persisted run is not a new execution and does not increment these totals.",
            "capabilities": "Follow-up chat is read-only. Buyer exclusions and buyer-type filters are unsupported in both Edit target and New Research. Download: open a buyer card, then Download report in the panel header. For all buyers, Open full report then Download report.",
        },
        "coverage_rule": ranking.get("coverage_rule"),
        "strategic_alternatives": ranking.get("strategic_alternatives"),
        "verification": [{"buyer": r.display_name, "rank": r.rank, "status": r.status,
                          "issues": r.issues, "error": r.error} for r in run.get("reports", [])],
    }
    sid = f"ST-RUN-{slug(run.get('run_id') or 'unsaved')}-record"
    record = {"stat_id": sid, "display": "Saved research record", "n": None,
              "source_ids": [], "calculation": "Recorded research metadata, not transaction evidence",
              "filters": {"run_id": run.get("run_id")}, "details": metadata,
              "explanation": json.dumps(metadata, ensure_ascii=False)}
    registry = (run.get("sources") or {}).get("entries") or []
    requested = {int(n) for n in re.findall(r'\[(\d+)\]', question)}
    selected = [e for e in registry if e.get("number") in requested]
    selected_reports = [r for r in run.get("reports", [])
                        if r.display_name.lower() in question.lower()]
    previous = run.get("followups") or []
    if not selected_reports and previous:
        last_question = str(previous[-1].get("question") or "").lower()
        referenced = [r for r in run.get("reports", []) if r.display_name.lower() in last_question]
        if len(referenced) == 1:
            selected_reports = referenced
    if not selected_reports and re.search(r"unverified|not verified|failed verification", question, re.I):
        selected_reports = [r for r in run.get("reports", []) if r.status != "verified"]
    reports = [r.model_dump(mode="json") for r in selected_reports]
    def source_ids(value):
        if isinstance(value, dict):
            return set(value.get("source_ids") or []) | set().union(*(source_ids(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(source_ids(v) for v in value))
        return set()
    report_ids = {r.display_name: source_ids(r.model_dump(mode="json")) for r in run.get("reports", [])}
    all_report_ids = set().union(*report_ids.values())
    catalog = [{"Number": e["number"], "Source ID": e["id"], "Kind": e.get("kind", "")}
               for e in registry if e.get("id") in all_report_ids]
    metadata["report_source_membership"] = {k: sorted(v) for k,v in report_ids.items()}
    record["explanation"] = json.dumps(metadata, ensure_ascii=False)
    get_client().note_run_record(record)
    prior_calls = []
    for turn in previous[-3:]:
        events = {e.get("step"): e for e in turn.get("events", [])}
        prior_calls.append({"question": turn.get("question"), "calls": [
            {"tool": e.get("name"), "arguments": e.get("input"), "status": e.get("status")}
            for e in events.values() if e.get("category") == "mcp_tool"]})
    history = [{"question": h.get("question"), "status": h.get("status"),
                "statements": h.get("statements")} for h in (run.get("followups") or [])[-6:]]
    from ma_engine.ranking import evidence_review
    reported = {r.acquirer_key for r in run.get("reports", [])}
    entries = [e for e in ranking.get("buyers", []) if e["acquirer_key"] in reported]
    comparison = None
    amounts = list(re.finditer(r"\$([\d,]+(?:\.\d+)?)\s*(million|billion|m|b)\b", question, re.I))
    if "compare" in question.lower() and len(amounts) == 1:
        amount = amounts[0]
        value = float(amount.group(1).replace(",", "")) * (1000 if amount.group(2).lower() in ("b", "billion") else 1)
        comparison = {"ev_mm": {"value": value, "display": fmt_money(value)},
                      "user_quote": amount.group(0), "provenance": "User-supplied comparison value, not the saved target and not a recalculated ranking."}
    context = {"requested_comparison": comparison, "saved_target_ev": (ranking.get("target", {}).get("ev_mm") or {}).get("value"),
               "buyer_evidence_review": evidence_review(entries),
               "reported_buyers": [e["display_name"] for e in entries],
               "prior_tool_parameters": prior_calls, "report_source_catalog": catalog,
               "run_record": {k: v for k, v in record.items() if k != "explanation"},
               "previous_turns_for_reference_only": history,
               "requested_citations": selected, "requested_buyer_reports": reports,
               "source_index": [{"number": e.get("number"), "id": e.get("id")} for e in registry]}
    # Financial evidence remains tied to its original transaction/statistic IDs.
    wanted = {e["id"] for e in selected}
    def collect(value):
        if isinstance(value, dict):
            wanted.update(value.get("source_ids") or [])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(reports)
    # Resolve prior answers through original saved sources, never their prose.
    collect(history)
    evidence_registry = [e for e in registry if e.get("id") in wanted]
    client = get_client()
    deal_ids = {e["id"] for e in evidence_registry if e.get("kind") == "deal"}
    deal_rows = client.deal_rows(client.transactions[client.transactions["transaction_id"].isin(deal_ids)])
    statistics = [e.get("record") for e in evidence_registry
                  if e.get("kind") != "deal" and not e.get("id", "").startswith("ST-RUN-")]
    evidence = {"data": {"run_record": record, "saved_sources": [*statistics, *deal_rows]}}
    context["saved_evidence"] = evidence["data"]["saved_sources"]
    return context, evidence


def check_answer_scope(answer, question, context):
    """Additional completeness/capability checks; all grounding checks still run."""
    text = " ".join(s.text for s in answer.statements)
    issues = []
    required = []
    q = question.lower()
    review = context.get("buyer_evidence_review") or {}
    if re.search(r"\b(?:each|all)\b.*\bbuyer", q):
        required = context.get("reported_buyers", [])
    elif "which" in q and "adjacent" in q and re.search(r"buyer|recommendation", q):
        required = review.get("mainly_adjacent", [])
    elif "weakest evidence" in q:
        required = list(dict.fromkeys(name for key in ("fewest_direct", "fewest_relevant", "fewest_near_size")
                                      for name in review.get(key, [])))
    values = re.findall(r"\$([\d,]+(?:\.\d+)?)\s*(million|billion|m|b)\b", q)
    if "compare" in q and len(values) == 1 and context.get("saved_target_ev") is not None:
        value, unit = values[0]
        requested_ev = float(value.replace(",", "")) * (1000 if unit in ("b", "billion") else 1)
        if requested_ev != context["saved_target_ev"] and not re.search(r"saved|different|requested", text, re.I):
            issues.append("answer scope: explicitly distinguish the saved target EV from the requested comparison value; do not describe the saved EV as if it were the user's requested target.")
    absent = [name for name in required if name.lower() not in text.lower()]
    if absent:
        issues.append("answer scope: omitted requested buyers: " + ", ".join(absent) +
                      ". Cover the full requested set using the supplied evidence, grouping buyers if needed.")
    if re.search(r"\ball\b.*\bsources\b", q) and not answer.include_source_catalog:
        issues.append("answer scope: set include_source_catalog=true to attach the complete saved report sources.")
    if re.search(r"\b(?:remove|exclude|omit)\b", q) and any(name.lower() in q for name in context.get("reported_buyers", [])):
        if not re.search(r"unsupported|not supported|cannot|can't|unable|does not support|doesn't support", text, re.I):
            issues.append("answer capability: explicitly state that removing/excluding a buyer is unsupported; showing the next saved buyer does not modify the shortlist.")
    for s in answer.statements:
        for sentence in re.split(r"[.;]", s.text):
            if re.search(r"new research|new run|edit target", sentence, re.I) and re.search(r"exclu|remov|omit", sentence, re.I):
                if not re.search(r"\bnot\b|unsupported|cannot|can't|doesn't|isn't|neither", sentence, re.I):
                    issues.append("answer capability: buyer exclusions are unsupported in both New Research and Edit target. Do not promise this action.")
    return issues
