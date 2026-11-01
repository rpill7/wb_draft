# Subplan 7 — Review Fixes (before Subplan 6)

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation once decisions D1 and D2 are confirmed  
**Time box:** 120 minutes  
**Dependency:** Subplans 0–5 complete. Source: `plans/PRINCIPAL_ENGINEER_REVIEW.md` (finding numbers below refer to it).  
**Order:** This subplan runs **before** Subplan 6, so the app, README, and sample run show corrected behavior.

## Goal
Fix the confirmed defects from the principal review, with the smallest changes that solve them. The most important fix makes "Verified" mean that each sentence matches the deal it cites. No new frameworks, no new environment variables, no redesign.

## Decisions (confirm before starting)
- **D1 — Direct sector experience (Finding 5).** Repeat activity counts closeness-weighted relevant closed deals (a target-sector deal counts 1.0, an adjacent deal counts its closeness score) instead of a plain count. Recommended: yes.
- **D2 — Coverage guard (Finding 6).** The buyer-type coverage rule promotes only buyers of the missing type that have at least one closed deal in the target sector. If none exist, it falls back to the current rule and records a warning. Recommended: yes.

## Fixes

### Fix 1 — Bind claims to their source rows (Findings 3, 4, 11) — P0
1. Change `guardrails.build_evidence` to keep, alongside the current sets, a map from each evidence item to the IDs that support it:
   - A deal row (from `get_acquirer_deals` or `search_deals`) supports its own `transaction_id`. Its numbers, its `target_company`, its `outcome`, and its `deal_type` belong to that ID.
   - A statistic with `source_ids` (profile stats, comps, ranking signals) supports those IDs. Tag comps statistics as `comp`, and buyer-profile and ranking statistics as `buyer`.
   - A comps row's sample size belongs to the multiple it sits next to (EV/EBITDA n, EV/Revenue n).
2. Add checks for every `fact` and `interpretation` statement:
   - **Numbers:** each number token must come from at least one evidence item whose supporting IDs overlap the statement's `source_ids`. The target block (target EV, size band) is exempt.
   - **Names:** a target company name must belong to one of the cited IDs.
   - **Outcomes:** an outcome word (Withdrawn, Pending, Terminated, Rumored, Closed) must match the outcome of every cited deal it describes. Keep the existing completion-word check.
   - **Sample sizes:** an `n=<count>` must be the sample size stored next to a multiple quoted in the same statement.
   - **Comp IDs:** statements in `acquirer_overview`, `strategic_fit`, and `precedent_activity` must cite at least one `buyer` ID. Comp-only IDs may support `valuation_context` only.
3. Require at least one ID for `interpretation` statements, and apply the valuation checks (closed-only, `n=`) to every non-unknown valuation statement.
4. Messages stay specific and fix-oriented, for example: `precedent_activity[0]: "$299.0M" belongs to MA-2021-0195, but the statement cites MA-2021-0407. Cite MA-2021-0195 or use the figures for MA-2021-0407 ($341.7M, 10.0x).`
5. Tests: add the swapped sentences from the review as failing cases (swapped price and multiple; a closed deal described as Withdrawn; a wrong deal type; "12.4x (n=10)" when 12.4x has n=75; another buyer's comp ID cited as a precedent; an interpretation with no ID). Keep the existing valid Blackstone fixture passing.
6. Update the writer prompt rules only where the new checks need it (for example: "cite the exact deal whose figures you quote").

### Fix 2 — Remove double rounding (Finding 9) — P0
- In `prepare.py`, build display strings from the unrounded value, and round `value` separately (`median_stat`, `extreme_stat`, `_distribution`, sector stats, profiles). Re-run `make data`. Add a test showing 2.045 displays as `2.0x`. Record in `plans/PROGRESS.md` which displayed cells changed.

### Fix 3 — Run-level errors and honest substitution (Findings 7, 8, 20) — P0
- In `engine.run_engine`, call `await llm.supported_parameters()` once before research starts. Treat `RuntimeError` raised by the LLM client and `openai.RateLimitError` that survives retries as run-level errors. `engine.main()` prints one clear line for these.
- In `_research_all`, when a buyer fails, prefer a reserve of the same `acquirer_type` if losing it would drop that type below `MIN_PER_TYPE`. Otherwise take the next reserve.
- After research, renumber ranks 1..N in final order. Set the replacement's `selection_note` to "Replaced <failed buyer> after an error". Keep each failed buyer's error and partial trace in `trace.json`.
- If OpenRouter returns an empty supported-parameter list, send no optional parameters and record that.
- Tests: wrong model name stops the run once with a clear message; same-type substitution; ranks renumbered; failed buyer's trace kept.

### Fix 4 — Ranking: direct precedent and coverage guard (Findings 5, 6, 10) — P1, per D1/D2
- D1: in `ranking.py`, repeat activity uses the sum of closeness over relevant closed deals instead of a count. Keep the same diminishing-returns curve and prior.
- D2: in `apply_type_coverage`, promote only buyers of the missing type with at least one closed target-sector deal. If none exist, fall back to the current rule and add a warning.
- Conviction reasons show scores and bars with 3 decimals.
- Before and after: print the default target's top 15 and record both tables in `plans/PROGRESS.md`. Explain in one paragraph why each name entered or left. Don't adjust weights to reach a preferred list.

### Fix 5 — Quality gate and assumption labelling (Findings 12, 13, 16) — P1
- `quality_metrics` fails when any report is not verified or fewer than `REPORT_COUNT` reports exist.
- `get_sector_overview` returns the closeness reasons under an `assumption` field. The writer prompt says anything from it is an `interpretation`.
- Ranking signals carry `weight_display`, and the sector-fit explanation states the blended value that is actually scored.

### Fix 6 — Sensitivity table (P1)
- Add a `--sensitivity` option to `python -m ma_engine.ranking` that prints, for the given target, the top 10 under: base; equal weights; each signal dropped; adjacency 0.5 and 0.75; prior off; coverage rule off. Show the overlap with base and names in or out. Calculations stay in Python. No new settings in `config.py`. Record the output in `plans/PROGRESS.md` for the README.

### Fix 7 — Small items (P2, only if time remains)
Findings 14 (overview default-target leak), 15 (quarter and ordinal tokens, minus sign), 17 (repeated identical tool calls answered from a per-buyer cache), 18 (create run folder after ranking; avoid name collisions), 19 (Margin Improvement tag when margin profile is "any"). Also: the writer prompt leads the overview with the median deal size and mentions the size range only with its sector.

## After the fixes: first real run (P0)
1. With a key in `.env`, run `uv run python -m ma_engine.engine`, then `make check`.
2. If the final write call fails because of the tools + strict schema combination, send that one call without `tools` and record it.
3. Record in `plans/PROGRESS.md`: cold and warm wall time, tokens, cost, verified count, first-check issue types, conviction mix after the committee, and how many distinct tool sequences the 10 buyers used.
4. If cold time is over 60 s, set `MAX_TOOL_ROUNDS` to 2 and re-measure.
5. Read three reports as a banker would and fix prompt wording if they are generic.

## Acceptance criteria
- All review swapped-sentence cases fail the checker; the valid fixture passes.
- 2.045 displays as `2.0x`; processed files regenerate deterministically.
- A wrong model name produces one clear error, not 15 substitutions.
- Substitutions keep ranks 1..N, honest notes, and the failed buyer's trace.
- Ranking before/after and sensitivity tables recorded; no buyer names in ranking code.
- `make check` fails on unverified or missing reports.
- All tests pass; real output reported.

## Out of scope
New frameworks, databases, Docker, learned sector similarity, weight tuning, new environment variables, UI work (Subplan 6).

## Completion
Build Subplan 7 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md` and review them before starting Subplan 6.
