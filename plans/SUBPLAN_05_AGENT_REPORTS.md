# Subplan 5 — Agent Research and Report Writing

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 70 minutes  
**Dependency:** Subplan 4 complete and verified.

## Goal
Build the tool-using AI agent that researches each shortlisted buyer through the FastMCP tools and writes a structured report that passes the guardrails from Subplan 4. Then run a committee review across all 10 reports and save the run. The AI chooses tools and writes; it never calculates (see `AGENTS.md`).

## Inputs
- FastMCP server and tools (Subplans 2–3), guardrails and report model (Subplan 4).
- `OPENROUTER_API_KEY` and `LLM_MODEL` from `.env` through `config.py`.

## Steps
1. **`clients/llm_client.py`.** One client per run using the `openai` library with `config.OPENROUTER_BASE_URL`. Responsibilities:
   - Send chat requests with tools or with a JSON schema for structured output. When structured output is requested, ask OpenRouter to route only to providers that support it.
   - Apply temperature and seed from `config.py`, a request timeout, and a small retry for temporary errors (rate limit, timeout, server error). Never retry business errors.
   - **Response cache:** before calling, hash the model, messages, tools, and schema; if `.cache/llm/<hash>.json` exists, return it. A `use_cache` argument (default true) lets the UI's Regenerate button skip it. This is a function argument, not a setting.
   - Record token usage and cost from OpenRouter's usage data for each call.
2. **`clients/mcp_client.py`.** Start the FastMCP server over stdio and open **one session per run**, shared by all buyers. List tools once and convert them to the LLM's tool format. Call tools with a timeout. Close cleanly at the end, including after errors. Confirm concurrent calls on one session work; if they don't, use a lock and record the measured effect.
3. **Prompts** in `src/ma_engine/prompts/`, loaded as text files so the README can show them:
   - **`research_writer.md`:** role (an M&A analyst preparing an MD-ready buyer page); the target profile; the buyer's ranking entry; the guardrail rules in plain words (quote only numbers given by tools, exactly as written; cite IDs for every fact; label facts, interpretations, and unknowns; never describe non-closed deals as completed; dataset only); what each of the six sections must contain; a rule that every paragraph must say something that is true for this buyer only; one short example of a strong, specific paragraph and one example of weak, generic text to avoid, based on the assignment's own examples; guidance on when to call which tool (for example, check valuation comps for the target band, look at adjacent-sector comps when direct comps are few, check platform versus bolt-on history for PE buyers).
   - **`repair.md`:** the original report, the exact checker messages, and the instruction to fix only those problems.
   - **`committee.md`:** see step 7.
4. **Agent loop in `agent.py`.** For one buyer:
   1. Send the research prompt with the MCP tools available.
   2. If the AI asks for tools, call them through the MCP client, add the results, and repeat. Stop after `config` maximum tool rounds.
   3. Ask for the final report as structured output matching the report model (no tools in this last call).
   4. Collect every tool result received as the buyer's **evidence**.
   5. Run `guardrails.check(report, evidence)`. If it fails, send the repair prompt once. If it still fails, set status `not_verified` with the issues. Never drop or quietly pass it.
   6. Return the report, evidence, and a trace (each tool call with arguments, duration, status; each LLM call with tokens, cost, cache hit).
5. **Real decisions.** The agent should choose different tools for different buyers based on what it finds. Tests must show at least two different paths, for example a buyer with thin direct history leading to an adjacent-sector comps call, and a buyer with strong direct history going straight to valuation.
6. **Run the buyers in parallel in `engine.py`.** `run_engine(target, use_cache=True, excluded_keys=())`:
   1. Open the MCP and LLM clients.
   2. Call `rank_acquirers` directly (no AI) and drop any excluded buyers (used by the "not relevant" button in Subplan 6).
   3. Research the top 10 in parallel with `asyncio.gather` and a semaphore set from `config`. If a buyer fails with an error (not a guardrail failure), record it and research the next shortlisted buyer, recording the substitution.
   4. Run the committee review (step 7).
   5. Save `outputs/runs/<run_id>/reports.json`, `reports.md` (rendered by Python), and `trace.json` (all tool and LLM calls, timings, tokens, cost, guardrail results, substitutions).
   6. Return the results to the caller.
7. **Committee review.** One LLM call receives a short summary of all 10 checked reports (buyer, starting conviction, key cited facts). It returns, per buyer, a final conviction level (at most one step from the starting level) with a 1–2 sentence reason citing IDs from that buyer's evidence, plus any pairs of reports making the same argument. Apply its changes, then re-run `guardrails.check` on the affected reports. If the committee output fails the checks, keep the starting conviction and record why.
8. **Timing.** Measure the full run with a cold cache. Target under 60 seconds. If it's over, first reduce tool rounds, then shorten prompts; record what was changed and the measured result in `plans/PROGRESS.md`.
9. **Tests** (`tests/test_agent.py`) with a mocked LLM client and the real MCP tools: tool listing; tool call and result passed back; two different tool paths; tool error handled; invalid tool arguments; stop at maximum rounds; repair succeeds; repair fails and status is `not_verified`; one buyer error triggers a substitution; committee cannot move conviction two steps; cache hit returns the same output without calling the model; sessions are reused, not reopened. Run one real smoke test only if a key is available, and report its time, tokens, and cost.

## Files
`src/ma_engine/clients/llm_client.py`, `src/ma_engine/clients/mcp_client.py`, `src/ma_engine/agent.py`, `src/ma_engine/engine.py`, `src/ma_engine/prompts/research_writer.md`, `src/ma_engine/prompts/repair.md`, `src/ma_engine/prompts/committee.md`, `tests/test_agent.py`.

## Acceptance criteria
- The agent chooses among the real MCP tools and reacts to results.
- Every report passed through the guardrails; failed ones are clearly marked.
- Tool rounds, timeouts, retries, and repairs are capped.
- One shared MCP session and one LLM client per run.
- A full run saves reports and a trace; real timing, tokens, and cost are reported.

## Out of scope
UI, outside data sources, agent frameworks, multi-agent setups beyond the single committee call.

## Completion
Build Subplan 5 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md` and review them before starting the next subplan.
