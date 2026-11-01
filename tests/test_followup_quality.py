"""Quality regressions add requirements; existing citation checks remain mandatory."""
from ma_engine.research.followup_context import check_answer_scope, saved_context
from ma_engine.models import FollowupAnswer, Statement
from ma_engine.data.ranking import evidence_review
from test_activity import buyer_run  # shared saved-run fixture


def answer(text, catalog=False):
    return FollowupAnswer(statements=[Statement(text=text, kind="unknown", source_ids=[])],
                          include_source_catalog=catalog)


def test_missing_buyer_cannot_pass_complete_comparison():
    context = {"reported_buyers": ["First Buyer", "Second Buyer"]}
    assert check_answer_scope(answer("First Buyer has evidence."), "Compare each buyer", context)
    assert not check_answer_scope(answer("First Buyer and Second Buyer."), "Compare each buyer", context)


def test_adjacent_coverage_uses_computed_eligibility_not_hardcoded_names():
    def row(name, relevant, direct, near):
        return {"display_name": name, "relevant_closed_count": {"value": relevant},
                "direct_closed_count": {"value": direct}, "near_size_closed_count": {"value": near}}
    review = evidence_review([row("Direct", 5, 4, 3), row("Adjacent", 9, 1, 2), row("Mixed", 4, 2, 0)])
    assert review["mainly_adjacent"] == ["Adjacent"]
    assert review["fewest_near_size"] == ["Mixed"]
    assert check_answer_scope(answer("Direct."), "Which recommendations rely on adjacent sectors?",
                              {"buyer_evidence_review": review})


def test_exclusion_promise_rejected_but_explicit_limitation_allowed():
    assert check_answer_scope(answer("Use New Research to exclude this buyer."), "Remove a buyer", {})
    assert not check_answer_scope(answer("Buyer exclusions are not supported in New Research or Edit target."),
                                  "Remove a buyer", {})


def test_catalog_is_required_when_all_sources_requested():
    assert check_answer_scope(answer("Use the export."), "List all sources", {})
    assert not check_answer_scope(answer("Sources are listed below.", True), "List all sources", {})


def test_prior_filters_and_original_report_catalog_are_available(buyer_run):
    buyer_run['followups'] = [{'question': 'Show closed sector deals', 'statements': [], 'events': [
        {'step': 1, 'category': 'mcp_tool', 'name': 'search_deals', 'status': 'completed',
         'input': {'sectors': ['Healthcare Services'], 'outcomes': ['Closed'], 'size_band': '$100M–$400M'}}]}]
    ctx, _ = saved_context(buyer_run, 'Include withdrawn as well')
    args = ctx['prior_tool_parameters'][0]['calls'][0]['arguments']
    assert args['outcomes'] == ['Closed'] and args['size_band'] == '$100M–$400M'
    catalog = ctx['report_source_catalog']
    assert catalog and all(row['Source ID'] for row in catalog)
    assert 'report_source_membership' in ctx['run_record']['details']


def test_truncated_research_is_not_mistaken_for_done(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from ma_engine.research import agent
    from ma_engine import config
    from ma_engine.clients.llm_client import LLMResult
    calls = []
    async def respond(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return LLMResult(content='', tool_calls=[], finish_reason='length', truncated=True)
        return LLMResult(content='DONE', tool_calls=[])
    monkeypatch.setattr(agent, 'call_llm', respond)
    messages, trace = [], {}
    asyncio.run(agent._tool_loop(messages, None, SimpleNamespace(tools=[]), False, trace,
                                 agent.Steps(), None, acquirer_key=None, display_name='test'))
    assert len(calls) == 2
    assert trace['stop_reason'] == 'model finished research'
    assert 'truncated' in messages[1]['content']

    async def always_cut(*args, **kwargs):
        calls.append(kwargs)
        return LLMResult(content='', tool_calls=[], finish_reason='length', truncated=True)
    monkeypatch.setattr(agent, 'call_llm', always_cut)
    calls.clear()
    asyncio.run(agent._tool_loop([], None, SimpleNamespace(tools=[]), False, trace,
                                 agent.Steps(), None, acquirer_key=None, display_name='test'))
    assert len(calls) == config.MAX_TOOL_ROUNDS
    assert trace['stop_reason'].startswith('reached the maximum')


def test_citation_guide_only_repeats_supplied_evidence(buyer_run):
    from ma_engine.research import agent
    from ma_engine.validation import guardrails
    ctx, data = saved_context(buyer_run, 'Blackstone report')
    ev = guardrails.build_answer_evidence(buyer_run['ranking']['target'], agent.run_entries(buyer_run),
                                         [{'result': data}])
    guide = agent.citation_guide(ev)
    assert 'Exact citation bindings' in guide
    assert all(sid in guide for sid in ev.stats if not sid.startswith('ST-RUN-'))
    assert 'MA-9999-9999' not in guide


def test_saved_actions_use_records_and_never_call_model(buyer_run):
    import asyncio
    from types import SimpleNamespace
    from ma_engine.research import agent
    class NoModel:
        totals = {}
        async def chat(self, *args, **kwargs):
            raise AssertionError('A saved source listing must not invoke the model')
    result = asyncio.run(agent.answer_followup('Give me a list of all sources used in this report.',
                         buyer_run, NoModel(), SimpleNamespace(tools=[])))
    assert result.status == 'verified' and result.source_catalog
    assert result.totals['llm_calls'] == 0 and result.totals['mcp_tool_calls'] == 0


def test_comparison_cannot_silently_substitute_saved_target():
    context = {'saved_target_ev': 150, 'reported_buyers': []}
    assert check_answer_scope(answer('The target EV is $150.0M.'), "Compare the target's $200M EV with each buyer", context)
    assert not check_answer_scope(answer('The saved target uses a different EV; these are historical buyer sizes.'),
                                   "Compare the target's $200M EV with each buyer", context)


def test_user_comparison_value_has_explicit_provenance_not_arbitrary_number_permission(buyer_run):
    from ma_engine.research import agent
    from ma_engine.validation import guardrails
    ctx, data = saved_context(buyer_run, "Compare the target's $350M EV with each buyer")
    scenario = ctx['requested_comparison']
    assert scenario['ev_mm']['display'] == '$350.0M'
    assert scenario['user_quote'] == '$350M'
    target = {**buyer_run['ranking']['target'], 'requested_comparison': scenario}
    ev = guardrails.build_answer_evidence(target, agent.run_entries(buyer_run), [{'result': data}])
    assert not guardrails.check_statements([Statement(text='The requested comparison value is $350M.', kind='unknown', source_ids=[])], ev)
    assert guardrails.check_statements([Statement(text='The requested comparison value is $987654M.', kind='unknown', source_ids=[])], ev)


def test_long_followup_is_accepted_without_dropping_claims_and_still_checks_sources(buyer_run):
    from ma_engine.research import agent
    from ma_engine.validation import guardrails
    from ma_engine.clients.llm_client import _parse
    import json
    payload = {'statements': [
        {'text': 'The dataset does not establish current acquisition appetite.',
         'kind': 'unknown', 'source_ids': []} for _ in range(10)]}
    parsed, error = _parse(json.dumps(payload), FollowupAnswer)
    assert error is None and len(parsed.statements) == 10
    _, data = saved_context(buyer_run, 'Compare buyers')
    evidence = guardrails.build_answer_evidence(buyer_run['ranking']['target'],
                    agent.run_entries(buyer_run), [{'result': data}])
    assert not guardrails.check_statements(parsed.statements, evidence)
    parsed.statements[-1] = Statement(text='This transaction closed.', kind='fact',
                                      source_ids=['MA-9999-9999'])
    assert guardrails.check_statements(parsed.statements, evidence)
