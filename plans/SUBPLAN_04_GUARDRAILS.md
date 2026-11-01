# Subplan 4 — Guardrails and Citation Checks

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 35 minutes  
**Dependency:** Subplan 3 complete and verified.

## Goal
Define what a valid buyer report is, and build the checker that proves it, **before** any AI writes a report. Guardrails and source citations are the project's first priority. The writer in Subplan 5 is built to pass this checker. No LLM calls in this step.

## Inputs
- Tool result shape from Subplan 2 and ranking output from Subplan 3.
- The processed dataset (to confirm IDs and outcomes, and to get the list of known company names).

## Outputs
- The report model in `models.py`.
- `guardrails.py` with a `check(report, evidence)` function and quality metrics.
- A Python function that turns a checked report into Markdown with citation tags.
- Tests using hand-written good and bad reports.

## Steps
1. **Report model** (Pydantic, kept flat):
   - Buyer identity: `acquirer_key`, display name, buyer type, rank, score.
   - Six sections: `acquirer_overview`, `strategic_fit`, `precedent_activity`, `valuation_context`. Each is a list of **statements**.
   - A statement has `text`, `kind` (`fact`, `interpretation`, `unknown`), and `source_ids` (list of transaction IDs).
   - `risk_flags`: at least two, each with `risk`, `explanation`, `kind`, `source_ids`.
   - `conviction`: `level` (High, Medium, Low), `starting_level` (from ranking), `reason`, `source_ids`.
   - `status` (`verified` or `not_verified`) and `issues` (list of checker messages), filled in by Python, never by the AI.
2. **Evidence.** For each buyer, the evidence is the ranking entry plus every tool result the agent received. Build two sets from it: the allowed source IDs and the allowed display strings (numbers and years).
3. **Checks in `guardrails.check(report, evidence)`:**
   1. **Structure:** all sections present and non-empty; at least two risk flags that are not duplicates; valid conviction level; conviction moves at most one step from the starting level.
   2. **Citations:** every `fact` statement has at least one ID; every ID exists in the dataset; every ID is in that buyer's evidence.
   3. **Numbers:** every number in any text (money, multiples, percentages, counts, years) matches an allowed display string. The checker only sees digits, so the writer prompt (Subplan 5) must require digits instead of number words. Note this limit in the README.
   4. **Outcome wording:** if a statement cites a non-closed deal, its text must not use completion words (acquired, completed, closed, bought, purchased). Keep the word list in `guardrails.py`.
   5. **Valuation:** `valuation_context` facts must cite closed deals only and the text must include a sample size.
   6. **Company names:** any known acquirer or target name (from the dataset) appearing in the text must belong to that buyer's evidence.
   7. **Unknowns:** `unknown` statements must not contain numbers or cite IDs as proof.
   - Return a list of clear, specific messages, for example: `precedent_activity[1]: cites MA-2022-0311, which is Withdrawn, but says "acquired"`. These messages are passed to the AI for repair in Subplan 5, so they must say exactly what to fix.
4. **Rendering.** `render_markdown(report)` builds the one-page report from the statements, adding the citation tags itself (for example `[MA-2021-0142]`), labelling interpretations and unknowns, and showing the status. The shown citations are therefore exactly the checked ones. Also render a "Not verified" banner with the issues when the check failed.
5. **Quality metrics** (used by `make check` in Subplan 6), calculated over a full run:
   - Number of invalid citations (target 0) and reports not verified.
   - Share of fact statements with citations (target 100%).
   - Conviction mix across the 10 reports (should not all be the same).
   - Similarity between reports' strategic-fit text, using simple word-overlap, to catch 10 versions of the same generic text. Flag any pair above a documented threshold.
   - Buyer-type mix in the final 10.
6. **Tests** (`tests/test_guardrails.py`) with hand-written fixture reports: a valid report passes; missing section; one risk flag; fact without an ID; ID not in evidence; made-up ID; wrong number; Withdrawn deal described as acquired; valuation citing a pending deal; a company name not in evidence; conviction moved two steps; rendering shows exactly the checked IDs.

## Files
`src/ma_engine/models.py` (report model), `src/ma_engine/guardrails.py`, `tests/test_guardrails.py`.

## Acceptance criteria
- Every check has at least one passing and one failing test.
- Checker messages are specific enough for an AI to fix the problem.
- Rendering never adds or removes citations.
- Runs without an API key.

## Out of scope
LLM calls, prompts, repair loop, UI.

## Completion
Build Subplan 4 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md` and review them before starting the next subplan.
