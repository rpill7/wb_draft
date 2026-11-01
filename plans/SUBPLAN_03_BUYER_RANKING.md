# Subplan 3 — Buyer Discovery and Ranking

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 30 minutes  
**Dependency:** Subplan 2 complete and verified.

## Goal
Find and rank candidate acquirers for a given target **without hardcoding company names**, using transparent Python scoring over the pre-calculated tables. Expose the ranking as the MCP tool `rank_acquirers`. Produce a shortlist of 15 for the agent; the final output is 10.

This is the only place, besides `prepare.py`, where numbers are calculated, because these numbers depend on the target. No AI is involved.

## Inputs
- Pre-calculated tables from Subplan 1 (through the data client).
- A target profile model in `models.py`: sector, EV in $M, ownership, geography (a region or "Any region"), and a margin profile. The default comes from `config.py`. Accepting a target as input makes the "any target profile" stretch goal work automatically.

## Steps
1. **Candidate pool.** Every acquirer with at least one closed deal in the target sector or in a sector with closeness above the threshold set in Subplan 1. Keep both buyer types. Do not filter by size here.
2. **Signals** (each between 0 and 1, calculated in Python from the profile and the target):
   - **Sector fit:** closed deals weighted by sector closeness to the target sector.
   - **Size fit:** how close the buyer's closed deal sizes are to the target EV, measured as distance on a log scale so $150M and $270M count as similarly close to $200M.
   - **Repeat activity:** number of relevant closed deals, with diminishing returns.
   - **Recency:** more weight for recent relevant deals.
   - **Profile fit:** share of the buyer's relevant closed deals that were private targets, regional, and above the sector median margin.
   - **Rationale fit:** overlap of the buyer's common tags with tags that suit this target (for example Platform Build, Geographic Expansion, Margin Improvement). Keep the tag list small and documented.
   - Non-closed deals do not add to the score. List them separately as weak signals.
3. **Score.** A weighted sum with the weights as a named constant at the top of `ranking.py`, chosen from business reasoning and explained in a short table in `plans/PROGRESS.md` (and later the README). No model training or tuning on 500 synthetic rows.
4. **Weak evidence.** Buyers with only one relevant deal, or with missing multiples, get a warning attached. Never treat missing values as zero.
5. **Starting conviction.** Give each buyer a starting level (High, Medium, Low) using documented rules that combine score and evidence strength. Example: High needs a top score and at least two relevant closed deals near the target size. Look at the real score spread before setting thresholds, and make sure the rules can produce all three levels. The committee step in Subplan 5 may move a level by one step only.
6. **Output per buyer** (raw and display values): rank, `acquirer_key`, display name, buyer type, score, each signal with its weight and contribution, starting conviction with the rule that produced it, supporting closed-deal IDs, weak-signal IDs, warnings. Also return the buyer-type mix of the shortlist.
7. **Deterministic order.** Sort by score, then relevant closed-deal count, then `acquirer_key`. Same data and target give the same output.
8. **MCP tool.** Register `rank_acquirers(target, limit=15)` in `mcp_server/server.py`. The engine calls it directly (not the AI) at the start of each run. Outside MCP clients can call it too.
9. **Tests** (`tests/test_ranking.py`) with small fixtures: a strong direct match beats a weak unrelated one; closed deals count and pending ones do not; adjacent-sector buyers survive when direct coverage is thin; size fit treats over and under the target fairly; the same input gives identical output; no buyer name appears in the ranking code.
10. **Measure** candidate count, time to rank, and buyer-type mix of the top 15 and top 10 on the default target. Record in `plans/PROGRESS.md`.

## Files
`src/ma_engine/ranking.py`, target model in `src/ma_engine/models.py`, tool registration in `src/ma_engine/mcp_server/server.py`, `tests/test_ranking.py`.

## Acceptance criteria
- Ranking runs from processed data only; no hardcoded buyers.
- Every score has a readable breakdown and source IDs.
- All three starting conviction levels are reachable.
- `rank_acquirers` works through the MCP server.
- No LLM needed to run or test this step.

## Out of scope
Agent research, report writing, UI, outside company data, predictive model training.

## Completion
Build Subplan 3 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md` and review them before starting the next subplan.
