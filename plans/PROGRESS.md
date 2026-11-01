# Progress Log

A record of each finished subplan: what was built, what was measured, and what was decided. Each entry uses this format:

```text
## Subplan N — <name> — <date>
Files changed:
Commands to run:
Real results (counts, test output, timings):
Decisions made (and why):
Differences from the plan:
Open issues / needs approval:
```

## Planning — 2026-10-31
- Plans rewritten: guardrails and citations first, all target-independent math pre-calculated in `prepare.py`, FastMCP server as the tool layer, Streamlit as the only UI, OpenRouter with `anthropic/claude-sonnet-5.5`.
- Agent research and report writing merged into Subplan 5; guardrails moved earlier to Subplan 4 so the checker exists before the writer.
- Quick data count (to be confirmed by Subplan 1): 500 rows, 24 columns, unique `transaction_id` already present, only `days_to_close` has blanks (94), outcomes include Rumored (10), 107 acquirers, Healthcare Services 46 deals with 12 in $100M–$400M (10 closed).
- No code written yet. Folders created: `src/ma_engine/{clients,mcp_server,prompts}`, `outputs/sample`.

## Subplan 0 — Project foundation — 2026-10-31
Files changed:
- New content: `pyproject.toml`, `.env.example`, `Makefile`, `src/ma_engine/config.py`, `src/ma_engine/__init__.py` (empty), `.python-version` (`3.12`), `uv.lock` (generated, to be committed).
- `.gitignore`: appended `outputs/runs/` and `.cache/`.

Commands to run:
- `make setup` (runs `uv sync`)
- `uv run python -c "import ma_engine.config as c; print(c.LLM_MODEL, c.RAW_CSV.exists())"`
- `uv run pytest`

Real results:
- `uv sync`: Python 3.12.10, 91 packages resolved. Installed: fastmcp 4.1.4, openai 3.42.0, pandas 3.1.0, pydantic 2.14.2, python-dotenv 1.2.4, streamlit 1.66.1, pytest 9.2.0.
- Config import with no `.env`: printed ` True` (`LLM_MODEL` empty, raw CSV found), exit 0.
- `require_api_key()` with no `.env`: `RuntimeError: OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your OpenRouter key.`
- `uv run pytest`: `collected 0 items ... no tests ran in 0.01s` (exit code 5, expected: no tests yet).
- `make -n` prints the expected command for all six targets.

Decisions made (and why):
- Build backend `hatchling` with `packages = ["src/ma_engine"]`: stable, supports src layout, so `python -m ma_engine.<module>` works after `uv sync`.
- `.python-version` added so uv picks Python 3.12 instead of the system Python 3.9.
- `REQUEST_TIMEOUT_S = 60`, `MAX_RETRIES = 2`: the subplan named these settings without values.
- `DEFAULT_TARGET` is a plain dict for now; the Pydantic target model comes with `models.py`.

Differences from the plan:
- `.python-version` is an extra file not listed in the subplan (needed to pin Python 3.12).
- `.cache` was already ignored by the template; `.cache/` was still appended as the subplan asks.

Open issues / needs approval:
- `make data`, `make mcp`, `make run`, `make check` point at modules that later subplans create; they fail until then.
- `make test` exits with code 5 until tests exist.

## Subplan 1 — Data cleaning and pre-calculation — 2026-10-31
Files changed:
- New: `src/ma_engine/prepare.py`, `tests/test_prepare.py`.
- Written by `make data` into `data/processed/`: `transactions.csv` (500 rows, 24 original + 6 derived columns), `acquirer_profiles.json` (107 profiles), `valuation_comps.csv` (100 rows = 10 sectors x 2 scopes x 5 size bands), `sector_stats.csv` (10 rows), `sector_similarity.csv` (100 ordered pairs), `data_quality.json`.

Commands to run:
- `make data` (runs `uv run python -m ma_engine.prepare`)
- `uv run pytest -q`
- Determinism: `shasum data/processed/* > /tmp/before.txt && make data && shasum data/processed/* | diff /tmp/before.txt - && echo IDENTICAL`

Real results:
- Rows 500, columns 24, no unexpected columns. `transaction_id` unique, none empty. Only blanks: `days_to_close` 94, all on non-closed deals (0 on closed).
- Raw file untouched (sha1 `95a5eda2...` before and after). All 24 original columns in `transactions.csv` are text-identical to the raw file for every row.
- Outcomes: Closed 406, Withdrawn 49, Pending 21, Terminated 14, Rumored 10.
- Buyer types: Financial Sponsor 290, Strategic 210. Every acquirer has a single buyer type.
- Acquirers: 107 distinct (normalization merged none), 73 with 2+ deals, 51 with 3+, 34 single-deal. Top: Blackstone 25; Bain Capital, Nordic Capital, Vista Equity 24; Warburg Pincus, Welsh Carson 23; New Mountain Capital 21; Advent International, TPG Capital 18; KKR 17.
- Deals per sector: Behavioral Health 70, Home Health/Hospice 62, Physician Groups 60, Health IT 50, Dental 46, Healthcare Services 46, Health Insurance 43, Revenue Cycle 43, Medical Devices 42, Pharma/Biotech 38.
- Size bands (lower edge inclusive, upper exclusive): Under $100M 203, $100M–$400M 154, $400M–$1B 77, Over $1B 66. One deal sits exactly on an edge (`MA-2020-0240`, $100.0M, in $100M–$400M).
- Healthcare Services: 46 deals; 12 in $100M–$400M; 10 of those closed. Closed comps with multiples: 10 sector-only and 75 sector-plus-adjacent in that band; across all sizes, 37 sector-only and 228 sector-plus-adjacent. Healthcare Services $100M–$400M sector-only medians: EV/EBITDA 12.1x, EV/Revenue 2.9x, EBITDA margin 15.2% (n=10 each).
- Strong-margin threshold: Healthcare Services median EBITDA margin 15.5% (n=46, all outcomes); 75th percentile 16.8%.
- Invalid numbers: 0. Unexpected category values: 0. Percent columns are on a 0–100 scale (margin 1.7–33.6, growth 2.7–27.7, synergy 2.0–22.0).
- Consistency findings (reported, not fixed): `ebitda_margin_pct` differs from EBITDA ÷ revenue by more than 1 point in 411 rows; `ev_ebitda_multiple` differs from deal size ÷ EBITDA by more than 10% in 403 rows; `ev_revenue_multiple` matches deal size ÷ revenue in all rows. `sub_sector` equals `sector` in all 500 rows.
- `margin_vs_sector`: above 245, below 247, at 8. `is_regional`: 405.
- Valuation comps: 10 of 100 rows carry a thin-sample warning (fewer than 3 deals).
- `uv run pytest -q`: `12 passed in 2.78s`.
- Two consecutive `make data` runs: all six processed files byte-identical (`IDENTICAL`).

Decisions made (and why):
- Raw CSV read as text (`dtype=str`, `keep_default_na=False`) so original columns are written back exactly; numbers are converted in a separate working copy. Bad numbers become blank in calculations and are listed with IDs, never zeroed.
- Formatting helpers `fmt_money` (`$185.0M`, thousands separator above $1,000M), `fmt_multiple` (`13.2x`), `fmt_pct` (`24.1%`), `fmt_number` (`2.5`, for median bidders/days) live in `prepare.py`. Rounding is half-up via `Decimal` so `13.25` shows as `13.3x`, not banker's rounding. Raw values are rounded to 1 decimal (money, days, bidders) or 2 (multiples, percentages); display strings are made from the rounded raw value. Missing values have display `None` (blank in CSV).
- Size bands: `[0,100)`, `[100,400)`, `[400,1000)`, `[1000,∞)` in $M.
- Sector similarity is a hand-written constant `SECTOR_SIMILARITY` in `prepare.py`; adjacency threshold `ADJACENCY_THRESHOLD = 0.6`. Healthcare Services adjacents: Physician Groups 0.8, Behavioral Health 0.75, Home Health/Hospice 0.75, Dental 0.7. Revenue Cycle (0.5) is not adjacent.
- Sector medians, margin thresholds and sponsor share use all deals in the sector (margin and size describe the target, not the price). Valuation comps and buyer-paid multiples use closed deals only. Buyer financing mix, median bidders and median days to close use closed deals only. Top rationale tags use all deals.
- List-valued CSV cells (source IDs, tags, included sectors, top deal types) are JSON lists everywhere.
- Valuation comps carry separate ID lists per metric (`ev_ebitda_source_ids`, `ev_revenue_source_ids`) plus `source_ids` for the whole row, so the two sample sizes are never mixed.
- Duplicate or empty `transaction_id` stops the pipeline with a clear error, because citations depend on it.
- The "focus sector" in `data_quality.json` comes from `DEFAULT_TARGET["sector"]`; per-sector thresholds for every sector are in `sector_stats.csv`.

Differences from the plan:
- None in counts: every planning number (500 rows, 24 columns, 94 blanks, 107 acquirers, 51 with 3+, outcome counts, 46 / 12 / 10 Healthcare Services) is confirmed.
- Added consistency checks (margin and EV/EBITDA versus the other columns) to `data_quality.json`; not in the plan, but they matter for guardrails.

What this means for ranking and tools:
Healthcare Services alone has only 10 closed comps in the $100M–$400M band, so valuation context must show both scopes (10 versus 75 deals) with their sample sizes. The data leans to Financial Sponsors (290 of 500; Physician Groups is 100% sponsor), so ranking should show the buyer-type mix rather than read it as buyer quality. Because stated margins and EV/EBITDA multiples do not match the other columns, tools and the agent must quote the stated columns only and never re-derive a multiple or margin.

Open issues / needs approval:
- `acquirer_profiles.json` is 580 KB because every statistic lists its IDs; fine for loading once, but tools should return only the parts asked for.
- Whether `data/processed/` should be committed is not decided (it is not git-ignored today).

## Subplan 2 — Data client and FastMCP tools — 2026-10-31
Files changed:
- New: `src/ma_engine/clients/data_client.py` (158 lines), `src/ma_engine/mcp_server/tools.py` (326), `src/ma_engine/mcp_server/server.py` (38), `tests/test_tools.py` (178).
- Not created: `src/ma_engine/models.py` (no shared model was needed yet; see decisions).

Commands to run:
- `uv run pytest -q`
- `make mcp` (runs `uv run python -m ma_engine.mcp_server.server`; stdio server, waits for a client).

How to connect an outside MCP client (Claude Desktop, Cursor, another agent). Add this to the client's MCP config (`claude_desktop_config.json`, or `.cursor/mcp.json`), with the repo path changed to where the project lives. Run `make setup` and `make data` first.
```json
{
  "mcpServers": {
    "ma-engine": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/rameshpilli/Developer/WB/wb_draft",
               "python", "-m", "ma_engine.mcp_server.server"]
    }
  }
}
```

Tools (all read-only, all return `{"data", "source_ids", "warnings"}`):
- `get_dataset_overview()`: dataset counts and the allowed values for every other argument.
- `get_sector_overview(sector)`: pre-calculated sector stats and closeness of every other sector.
- `get_acquirer_profile(acquirer_key)`: the pre-calculated buyer profile.
- `get_acquirer_deals(acquirer_key, sectors=None, closed_only=True, limit=10)`: one buyer's deal rows, newest first.
- `get_valuation_comps(sector, scope, size_band)`: the matching `valuation_comps.csv` row (closed deals only).
- `search_deals(sectors=None, size_band=None, outcomes=None, acquirer_type=None, deal_types=None, geography=None, limit=10)`: filtered deal rows.

Real results:
- `uv run pytest -q`: `38 passed in 3.80s` (12 from Subplan 1, 26 new).
- In-memory FastMCP client lists exactly the six tools; `get_valuation_comps` returns median EV/EBITDA `12.1x`; an unknown sector returns `is_error=True` with `Unknown sector 'Hospitals'. Allowed values: ...`.
- Healthcare Services, `$100M–$400M`: sector-only EV/EBITDA median `12.1x` (n=10), EV/Revenue `2.9x`, margin `15.2%`; sector-plus-adjacent EV/EBITDA p25/median/p75 `10.6x` / `12.4x` / `15.2x` (n=75), EV/Revenue median `2.3x`, 5 sectors included.
- Blackstone: 25 deals, 22 closed returned by default; with `closed_only=false` the Withdrawn and Rumored deals appear with the not-closed warning.
- Stdio smoke test from `/tmp` using the exact config above (`fastmcp.Client` with an `mcpServers` dict): listed all six tools and returned `median EV/EBITDA: 12.1x n = 10`.
- `make mcp` started and was still running after 6 seconds waiting for a client (then stopped). With its input closed it exits with code 0, as a stdio server should.

Decisions made (and why):
- One shared `DataClient`, created on first use by `tools.get_client()` (cached) and loaded by the server at start. Tool functions keep only their real arguments, so the same function is registered with FastMCP and tested directly, with no wrapper signatures to keep in sync.
- Invalid values raise `ValueError` listing the allowed values. FastMCP 4.1.4 (default, `mask_error_details` off) sends the message to the client as a tool error; checked in the in-memory test. Unknown acquirer keys suggest up to 5 keys that contain the given text.
- `limit` is checked in Python (whole number 1–25), so the rule holds when tools are called directly too.
- Deal rows quote the stated `ev_ebitda_multiple`, `ev_revenue_multiple` and `ebitda_margin_pct` with `prepare.fmt_*` display strings; nothing is re-derived (Subplan 1 found these columns disagree with revenue/EBITDA/size). Each number is `{"value", "display"}`. `num_bidders` and `days_to_close` display as whole numbers (`68`, not `68.0`); all are whole in the data.
- `get_valuation_comps` accepts `All sizes` as well as the four bands, because the table has those rows; `search_deals` accepts only the four bands.
- `models.py` not created: the tools return plain dicts with the fixed shape and FastMCP builds the schemas from the type hints; a model would add code without adding checks. It can come with the target and report models.
- No `__init__.py` added to `clients/` or `mcp_server/` (Python namespace packages import fine; AGENTS.md forbids empty files).
- Tools are registered with `readOnlyHint: true`.

Differences from the plan:
- Deal rows also include `acquirer_key`, so the agent can go from a `search_deals` row to `get_acquirer_profile` without guessing the key.
- `get_dataset_overview` returns `source_ids: []`: its counts cover all 500 rows, and listing 500 IDs would flood the agent. Its docstring tells the agent to cite IDs from the other tools.
- Small counts (`deal_count`, `sample_size`) get `{"value", "display"}`, but the category counts in `get_dataset_overview` are plain integers taken from `data_quality.json`.

Open issues / needs approval:
- FastMCP prints a full traceback to stderr for every expected argument error (the client sees only the clean message), and a startup banner on stderr. Harmless for stdio; could be quieted later with `show_banner=False` and `ToolError` if the logs get noisy.
- Guardrails (Subplan 4) must treat `get_dataset_overview` counts as uncitable (no IDs) and decide whether bare years and counts need a display string.
- `rank_acquirers` is added to this server in Subplan 3.

## Subplan 3 — Buyer discovery and ranking — 2026-10-31
Files changed:
- New: `src/ma_engine/models.py` (49 lines, `Target` model), `src/ma_engine/ranking.py` (462), `tests/test_ranking.py` (292).
- Changed: `src/ma_engine/clients/data_client.py` (cached `get_client()` moved here from `tools.py`, so `ranking.py` and `tools.py` share one client without a circular import), `src/ma_engine/mcp_server/tools.py` (new `rank_acquirers` wrapper), `src/ma_engine/mcp_server/server.py` (7th tool registered, read-only), `tests/test_tools.py` (in-memory test expects 7 tools).

Commands to run:
- `uv run pytest -q`
- `uv run python -m ma_engine.ranking` (ranks the default target and prints a diagnostic table)

How it works:
- Candidate pool: every buyer with at least one closed deal in the target sector or a pre-calculated adjacent sector (`is_adjacent` in `sector_similarity.csv`, closeness >= 0.6). Both buyer types kept, no size filter. Deals that did not close in those sectors are listed as `weak_signal_ids` and add nothing to the score; a buyer whose only relevant deals did not close is not a candidate.
- Six signals between 0 and 1, from the buyer's relevant closed deals only:
  - sector fit: average sector closeness of the deals;
  - size fit: per deal `1 - |ln(deal size / target EV)| / ln(10)` (floored at 0), so 2x over and 2x under score the same and a deal 10x off earns nothing;
  - repeat activity: `n / (n + 3)` (3 deals = half credit, diminishing returns);
  - profile fit: average of three shares: target ownership matches, regional (or the named region), `margin_vs_sector == "above"` (margin share dropped when `margin_profile="any"`);
  - recency: per deal weight halves every 3 years, counted back from the dataset's last year (2024);
  - rationale fit: share of deals tagged Platform Build, Scale or Market Share.
- Small-sample blend: every per-deal average (all signals except repeat activity) is blended with 2 neutral pseudo-deals at 0.50: `(sum + 2 x 0.5) / (n + 2)`. Each explanation says so.
- Missing values are left out of the share they belong to (never zero). A signal with no usable data would be `None`, add nothing, and carry a warning; none occur in this data.
- Every explanation is a Python template filled with display strings or whole counts, for example `1 of 6 relevant closed deals were within 2x of the target's $200.0M (between $100.0M and $400.0M); median relevant closed deal size $103.9M.`

Weights (`WEIGHTS` in `ranking.py`):

| Signal | Weight | Why |
|---|---|---|
| sector_fit | 0.25 | The most direct evidence that a buyer wants this kind of business. |
| size_fit | 0.20 | Buyers repeat their cheque size; a $200M deal is outside many buyers' usual range. |
| repeat_activity | 0.20 | Several relevant deals are a pattern; one deal is an anecdote. |
| profile_fit | 0.15 | Private, regional, strong-margin targets are what this buyer would actually be bidding for. |
| recency | 0.10 | Recent deals suggest an active programme, but the data covers 10 years so older deals still count. |
| rationale_fit | 0.10 | Tags are coarse labels, so they get the least weight. |

Rationale tags: Platform Build, Scale, Market Share (all real tags; 117, 117 and 95 of 500 deals). Geographic Expansion is left out because it is on 449 of 500 deals and cannot separate buyers (regional fit is already in profile fit); Margin Improvement is left out because the target's margins are already strong.

Starting conviction (revised after review; rule text and a buyer-specific reason with the actual bar values are in every buyer's `starting_conviction`):
- Bars are percentiles of this target's own candidate scores (pandas `quantile`, linear, on scores rounded to 4 places): `HIGH_PERCENTILE = 90`, `MEDIUM_PERCENTILE = 50`.
- High: score >= the 90th-percentile bar and at least `MIN_NEAR_SIZE_DEALS = 2` relevant closed deals within `SIZE_WINDOW = 2`x of the target EV.
- Medium: score >= the median bar and at least 2 relevant closed deals within 2x of the target EV.
- Low: everything else (any buyer with fewer than 2 deals near the target size is Low, whatever its score).
- Default target bars: High 0.6103 (shown `0.61`), Medium 0.5514 (shown `0.55`), over 50 candidates. Example reason: `Score 0.60; 1 relevant closed deal(s) within 2x of the target EV. High bar 0.61 (90th percentile of 50 candidates), Medium bar 0.55 (median); both need 2 deals near the target size.`
- Bars are also returned once at top level as `conviction_thresholds`.

Buyer-type coverage (added after review; banker lists always show both buyer types):
- `MIN_PER_TYPE = 3` within the first `config.REPORT_COUNT` (10) places. If a type has fewer than 3 there, its best-scoring buyers from further down are promoted, and the lowest-scoring buyers of the other type in the top 10 are displaced (never below 3 of their own type). Displaced buyers move to the start of the rest of the list, so they stay in the shortlist when it has room. The shortlist stays `limit` long.
- Order: final top 10 = (top 10 by score minus displaced) + promoted, sorted by score; then the rest by score.
- Every buyer has `rank` (final position) and `score_rank` (position by score alone among non-excluded candidates). Promoted buyers get `selection_note` such as `Included to ensure at least 3 Strategic buyers in the top 10; score rank 16 of 50 candidates.`; displaced buyers get `Moved below the top 10 to make room for Strategic buyers; score rank 8 of 50 candidates.`; everyone else has `selection_note: null`.
- Top level: `coverage_rule` text, `promoted_keys`, `displaced_keys`, `buyer_type_mix` (shortlist) and `buyer_type_mix_top_10`. If the pool cannot supply 3 of a type, nothing is forced and a warning says so.

Real results (default target: Healthcare Services, $200.0M, Private, Any region, strong margin):
- Candidates: 50 (35 Strategic, 15 Financial Sponsor). Time to rank: 220–251 ms.
- Score spread across all 50: min 0.40, p25 0.50, median 0.55, p75 0.59, max 0.67.
- Conviction across all 50: High 5, Medium 12, Low 33. Top 15: High 5, Medium 8, Low 2. Top 10: High 5, Medium 3, Low 2 (all three levels, as expected).
- Coverage: promoted `aspen dental (admi)` (score rank 16) and `universal health services` (score rank 17); displaced `veritas capital` (score rank 9) and `welsh carson` (score rank 8), now ranks 12 and 11.
- Buyer-type mix: top 10 = 7 Financial Sponsor, 3 Strategic; top 15 = 12 Financial Sponsor, 3 Strategic.
- Output size for 15 buyers: 70 KB JSON, 164 distinct source IDs, all present in the dataset.
- Other targets, conviction mix (all candidates / top 15 / top 10):
  - Dental, $80.0M, PE-Backed, Southeast, any margin: 32 candidates; bars 0.56 / 0.51. All: High 2, Medium 7, Low 23. Top 15: 2 / 7 / 6. Top 10: 2 / 5 / 3. Top 10 already had 7 sponsors and 3 strategics, so no promotion.
  - Health IT, $1,500.0M, Public, National, strong margin: 33 candidates; bars 0.51 / 0.47. All 33 Low (top 15 and top 10 all Low). Reason: only Revenue Cycle is adjacent, and 32 of 33 candidates have no relevant closed deal between $750.0M and $3,000.0M (one has 1), so nobody meets the 2-deals-near-size rule. Top 10: 5 / 5 by type, no promotion. Reported, not tuned.
- `uv run pytest -q`: `58 passed in 5.89s` (38 earlier + 20 in `test_ranking.py`).

Top 15 (SR = score rank, Near = relevant closed deals within 2x of $200M):

| Rank | SR | Buyer | Type | Score | Conviction | Near | Note |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Blackstone | Financial Sponsor | 0.67 | High | 4 | |
| 2 | 2 | New Mountain Capital | Financial Sponsor | 0.66 | High | 6 | |
| 3 | 3 | Nordic Capital | Financial Sponsor | 0.64 | High | 6 | |
| 4 | 4 | Vista Equity | Financial Sponsor | 0.62 | High | 6 | |
| 5 | 5 | Bain Capital | Financial Sponsor | 0.61 | High | 4 | |
| 6 | 6 | Francisco Partners | Financial Sponsor | 0.61 | Medium | 2 | |
| 7 | 7 | GTCR | Financial Sponsor | 0.61 | Medium | 3 | |
| 8 | 10 | VNS Health | Strategic | 0.60 | Low | 1 | |
| 9 | 16 | Aspen Dental (ADMI) | Strategic | 0.58 | Medium | 3 | Promoted: at least 3 Strategic in top 10 |
| 10 | 17 | Universal Health Services | Strategic | 0.57 | Low | 1 | Promoted: at least 3 Strategic in top 10 |
| 11 | 8 | Welsh Carson | Financial Sponsor | 0.60 | Medium | 5 | Displaced below top 10 |
| 12 | 9 | Veritas Capital | Financial Sponsor | 0.60 | Medium | 2 | Displaced below top 10 |
| 13 | 11 | General Atlantic | Financial Sponsor | 0.60 | Medium | 2 | |
| 14 | 12 | Advent International | Financial Sponsor | 0.60 | Medium | 4 | |
| 15 | 13 | KKR | Financial Sponsor | 0.59 | Medium | 3 | |

Decisions made (and why):
- Small-sample blend added after the first real run: without it, Ardent Health Services (1 relevant closed deal) ranked #1 at 0.72 and UnitedHealth Group (1 deal) #7, because "1 of 1" counts as a perfect share. With the blend both drop out of the top 15. Prior size 2 was picked as "a couple of deals", not searched for. Kept after review.
- Weights were not changed to improve the buyer-type mix; the mix is handled by the explicit coverage rule instead, so the score stays a pure evidence measure and `score_rank` shows what the score alone says.
- Conviction bars changed from fixed numbers (0.62 / 0.55, set on the default target) to per-target percentiles, after review: fixed bars gave other targets almost no High or Medium.
- Displaced buyers get a note too (not only promoted ones), so the move is visible from both sides.
- `score_rank` is counted after `excluded_keys` are removed, so `rank` and `score_rank` refer to the same list.
- `rank_acquirers` tool takes flat typed arguments (all defaulting to the default target) and builds a `Target` inside, the same style as the other six tools; no Pydantic argument, so no FastMCP-specific schema handling was needed. `limit` uses the same 1–25 check as the other tools; `ranking.rank_acquirers` itself only requires limit >= 1. With `limit` below 10, coverage applies to the first `limit` places.
- Sorting by score uses the score rounded to 4 places, then relevant closed-deal count, then `acquirer_key`.
- `margin_vs_sector` (pre-calculated) compares each past deal with the median of its own sector; the target's "strong" threshold (Healthcare Services median 15.5%, n=46) is echoed in the output but not used to re-classify past deals. Nothing is re-derived.
- `excluded_keys` are normalised like other keys; unknown keys raise a clear error. `candidate_count` and the conviction bars use the whole pool, before exclusion.

Differences from the plan:
- Per-buyer output also has `relevant_closed_count`, `near_size_closed_count` (value + display), `score_rank` and `selection_note`; conviction has a `reason` next to the `rule`.
- Small-sample blend and buyer-type coverage rule are extra (see decisions).

Open issues / needs approval:
- Promotion brings in buyers with weaker evidence: Universal Health Services (score rank 17) is Low conviction with 1 deal near the target size. Reports for promoted buyers should say they are there for buyer-type coverage, using the `selection_note`.
- Percentile bars always give High to roughly the top tenth by score, but the near-size rule can still leave a target with no High or Medium at all (Health IT $1,500M: all Low). That is the honest result for this data, not a bug.
- Score spread is narrow (top 15 within 0.57–0.67), so neighbouring ranks are close calls; the reports should not over-read a rank difference.

## Subplan 4 — Guardrails and citation checks — 2026-10-31
Files changed:
- Changed: `src/ma_engine/models.py` (49 -> 150 lines): `Statement`, `RiskFlag`, `Conviction`, `ReportDraft`, `BuyerReport`.
- New: `src/ma_engine/guardrails.py` (490 lines), `tests/test_guardrails.py` (438 lines, 43 tests).

Commands to run:
- `uv run pytest -q`
- `make check` (runs `uv run python -m ma_engine.guardrails`; reads the newest run in `outputs/runs/`)

Report model:
- `ReportDraft` (the only part the AI writes, sent as structured output in Subplan 5): `acquirer_overview`, `strategic_fit`, `precedent_activity`, `valuation_context` (each `list[Statement]`, max 5), `risk_flags` (`list[RiskFlag]`, max 4), `conviction` (`Conviction`). `Statement` = `text`, `kind` (`fact` | `interpretation` | `unknown`), `source_ids`. `RiskFlag` = `risk`, `explanation`, `kind`, `source_ids`. `Conviction` = `level` (`High` | `Medium` | `Low`), `reason`, `source_ids`. All models `extra="forbid"` (`additionalProperties: false`), no defaults, no unions beyond Literal (enums), no recursion. JSON schema: 3,723 characters, 6 top-level properties, `$defs` = Conviction, RiskFlag, Statement; maxItems 5 / 4. Minimum counts are not in the schema on purpose: the checker reports them as fixable messages.
- `BuyerReport` (assembled by Python): `acquirer_key`, `display_name`, `acquirer_type`, `rank`, `score_rank`, `score` (display string), `selection_note` (optional), `starting_level`, `draft`, `status` (`verified` | `not_verified`), `issues`. `BuyerReport.from_ranking(entry, draft)` starts as `not_verified` with issue "Not checked by guardrails yet."; `guardrails.apply_check(report, evidence)` sets status and issues.

`reports.json` shape (Subplan 5 must write exactly this, in `outputs/runs/<run_id>/`, run_id a sortable timestamp such as `20261031-173000`):
```json
{"run_id": "20261031-173000", "target": {"sector": "...", "ev_mm": 200, "...": "..."},
 "reports": [<BuyerReport.model_dump()>, ...]}
```

Evidence (`build_evidence(ranking_entry, tool_results)`, `tool_results` = `[{"tool", "arguments", "result"}]`): every leaf value of the buyer's ranking entry and of each `result` is read as text (arguments are not evidence). A raw `value` with a `display` beside it (or `x` with `x_display`) is skipped, so only the display string can be quoted (`14.4x` allowed, raw `14.35` not; `0.67` allowed, `0.6735` not). Allowed IDs = every `MA-YYYY-NNNN` in that text; allowed numbers = number tokens of that text; allowed names = known company names found in that text plus the buyer's own name. Blackstone with 5 tool calls: 96 IDs, 181 number tokens, 26 names, built in about 26 ms.

Number tokenizer: money (`$185.0M`, `$1,234.5M`, `$1.2B`), multiples (`12.1x`), percentages (`15.5%`), decimals and integers; ranges give separate tokens. Transaction IDs and known company names (some contain digits: `Dental365`, `R1 RCM`) are removed first, on both sides. Only commas are normalized; `$185M` does not match `$185.0M`. Number words are not detected.

Checks in `check(report, evidence)` (empty list = pass), with a real message for each:
1. Structure: identity and starting level match the ranking entry; four sections non-empty; no empty text; at least 2 risk flags; no duplicate risk (case/space-insensitive); conviction at most one step from the start. `conviction: level Low is 2 steps from the starting level High. Choose one of: Medium, High.`
2. Citations: facts (statements and risk flags) cite at least 1 ID; every ID well-formed, in the dataset and in the evidence; an ID written in the text must be in `source_ids`; the conviction reason cites at least 1 ID. `acquirer_overview[0]: cites MA-2099-9999, which does not exist in the dataset. Remove it; cite only IDs from your tool results.`
3. Numbers: every token in statements, risk/explanation and conviction reason is in the evidence; a same-value display string is suggested. `precedent_activity[1]: number "$54M" does not appear in the tool results for this buyer. Copy numbers exactly as displayed (e.g. "$185.0M", "12.1x", "15.2%"), or remove it. Did you mean "$54.0M"?`
4. Outcome wording: an item citing a non-closed deal may not use `COMPLETION_WORDS` (acquired, acquisition of, completed, closed, bought, purchased; whole word, any case; "acquirer" is fine). `precedent_activity[1]: cites MA-2020-0040 (Withdrawn) but uses completion word "acquired". Put non-closed deals in their own statement and describe them by their outcome (e.g. "was Withdrawn").`
5. Valuation: `valuation_context` facts cite closed deals only and contain `n=<int>` (the integer must be in evidence, via check 3). `valuation_context[1]: valuation fact has no sample size. State it as n=<count>, copying the count from the tool result (e.g. n=10).`
6. Company names (607 known names, longest match first): `strategic_fit[1]: names "Welsh Carson", which is not in the tool results for this buyer. Remove it, or look it up with a tool first.`
7. Unknowns: no numbers and no IDs. `risk_flags[2]: unknown statements must not contain numbers (found 2024). Remove them, or make it a cited fact.`
- `length_warnings(report)`: statement over 60 words, report over 650 words. Warnings only.

Rendering: `render_markdown(report)` gives a header (rank, name, type, score, score rank, final and starting conviction, status), the selection note, a `> **Not verified.**` banner listing the issues, then the six sections in assignment order; tags `[MA-…]` come from `source_ids` only; `*Interpretation:*` and `*Unknown:*` labels. `render_run_markdown(reports)` adds a summary table (rank, buyer, type, conviction, status).

Quality metrics (`quality_metrics(reports)`, printed by `make check`): invalid citations (re-checked against the dataset), not-verified reports, share of facts cited, conviction mix, buyer-type mix, strategic-fit word-set Jaccard (lower case, stopwords, numbers, IDs and the buyer's own name removed), pairs above `SIMILARITY_THRESHOLD = 0.5`. PASS = 0 invalid citations, all facts cited, at least 2 conviction levels, no pair above the threshold. FAIL and "no runs" both exit with code 1.

Real results:
- `uv run pytest -q`: `101 passed in 6.40s` (58 earlier + 43 new). Also passes with `OPENROUTER_API_KEY` empty (no `.env` exists).
- The valid Blackstone fixture (real display strings and IDs from the ranking entry and 5 real tool calls) passes with no issues and no length warnings. Every check has a passing and a failing test.
- `make check` today: `No runs found in .../outputs/runs (need <run_id>/reports.json). Run the engine first (make run).`, exit code 1.

Decisions made (and why):
- Raw values with a display string are not evidence, so "quote the display string exactly" is enforced, not only requested.
- Inline IDs in text must also be in `source_ids`, so the shown tags and the checked IDs can never differ.
- Company names are removed before numbers are read, so `Dental365` is checked as a name, not as the number 365.
- For `unknown` items the unknown check replaces the citation and number checks (one message instead of two).
- "closed" is a completion word even in "never closed"; the message tells the writer to use the outcome word instead. Strict on purpose.
- `make check` exits 1 on FAIL, not only when no run exists, so it can gate a demo.

Differences from the plan:
- Added checks not listed: identity/starting level match, empty text, inline-ID-must-be-cited, and the "did you mean" hint.
- `BuyerReport.score` is the display string (`0.67`), not the number.

Open issues / needs approval:
- Quality PASS ignores not-verified reports (they are printed but do not fail the run), as specified; this could be made a fail condition.
- `get_dataset_overview` counts have no IDs, so a fact using them needs another cited ID; the numbers themselves are still allowed.
- Rendered not-verified reports can show extra IDs inside the issue messages in the banner; the set-equality guarantee holds for verified reports.
- Number words and spelled-out amounts are not detected; Subplan 5's writer prompt must require digits, and the README must note it.

## Subplan 5 — Agent research and report writing — 2026-10-31
Files changed:
- New: `src/ma_engine/clients/llm_client.py` (199 lines), `src/ma_engine/clients/mcp_client.py` (64), `src/ma_engine/agent.py` (177), `src/ma_engine/engine.py` (252), `src/ma_engine/prompts/research_writer.md` (48 lines, 1,124 words before the buyer data is filled in), `prompts/repair.md` (15), `prompts/committee.md` (19), `tests/test_agent.py` (540, 18 tests).
- Changed: `models.py` (+30: `CommitteeDecision`, `SimilarPair`, `CommitteeReview`), `ranking.py` (target block now has `size_band`, the target's band label, so the agent never works it out), `mcp_server/server.py` (`mcp.run(show_banner=False)`).

Commands to run:
- `uv run pytest -q`
- `uv run python -m ma_engine.engine` (headless run of the default target; prints live steps, then time, tokens, cost, verified count, conviction mix and the run folder). No Makefile target added; `make run` stays the Streamlit app (Subplan 6).

How it works:
- `LLMClient` (one per run): `AsyncOpenAI` pointed at OpenRouter. `chat(messages, tools, tool_choice, response_schema, use_cache)` returns an `LLMResult` (content, tool calls, parsed model or parse error, tokens, cost, cache hit, duration). Disk cache `.cache/llm/<sha256>.json` keyed by model, messages, tools, tool_choice, schema, temperature, seed, max_tokens; a cache hit costs 0 and makes no network call; unparseable outputs are not cached. Running totals: calls, API calls, cache hits, tokens, cost.
- `MCPClient` (one per run): starts `python -m ma_engine.mcp_server.server` with this interpreter from the repo root (same as `make mcp`), lists tools once, converts them to chat tools, `call_tool` with a 30 s timeout. Errors (bad arguments, unknown tool, timeout) come back as `{"error": message}`. Server stderr goes to `outputs/runs/<run_id>/mcp_server.log`.
- Agent (`research_buyer`): system prompt = `research_writer.md` filled with the ranking target block and the buyer's ranking entry; up to `MAX_TOOL_ROUNDS` (4) rounds, tool calls in a round run in parallel; `rank_acquirers` is hidden from the agent (engine only). Then one final call with the whole conversation, tools present but `tool_choice="none"`, and `ReportDraft` as strict structured output. Evidence = ranking entry + target block + every successful tool result. `guardrails.check`; if it fails, one repair call (`repair.md` with the exact messages); final status from `guardrails.apply_check`. No valid JSON after repair raises `ReportError` (treated as a buyer error).
- Engine (`run_engine(target, use_cache=True, excluded_keys=(), progress=None)`): LLM client first (fails fast without a key, before anything starts), run folder, one MCP session; `rank_acquirers` via MCP (no AI); top 10 researched in parallel under `Semaphore(MAX_PARALLEL_BUYERS)`; a buyer that raises is replaced by the next shortlisted buyer and recorded (authentication/permission errors stop the run instead). Committee: one structured call (`CommitteeReview`) over short summaries; each decision is applied only if the changed conviction passes the guardrails for that buyer (any `conviction:` issue rejects it); invalid committee output keeps the writers' levels with the reason recorded. Saves `reports.json` (the Subplan 4 shape), `reports.md`, `trace.json` (wall time, ranking/research/committee times, per-buyer tool and LLM calls, tokens, cost, cache hits, first-check issues, repairs, errors, substitutions, committee record, quality metrics, model name).

Real results (mocked LLM, real MCP server and tools):
- `uv run pytest -q`: `119 passed in 14.69s` (101 earlier + 18 new).
- Stdio server: start and tool listing about 1.5 s; one tool call about 60 ms; 10 parallel `get_acquirer_profile` calls on one session 16 ms, all correct. Concurrent calls on one session work, so no lock was added.
- Tests cover: tool listing and chat format; tool results passed back with matching `tool_call_id`; two evidence-driven paths (Nordic Capital, 4 closed Healthcare Services deals: profile + narrow comps, then deals; VNS Health, 0: profile + narrow comps, then sector-plus-adjacent comps + adjacent-sector deals); tool error, unparseable arguments and an engine-only tool all returned to the model, report still verified; stop at 4 rounds; repair succeeds (exact checker message in the repair prompt); repair fails -> `not_verified` with a visible banner; unparseable output -> `ReportError`; committee one-step move applied, two-step move and unknown key rejected; invalid committee output keeps levels; failed buyer substituted by shortlist rank 11; full stdio engine run: 1 MCP client, 1 session, 10 verified reports in rank order, `reports.json` round-trips into `BuyerReport`, `guardrails.main()` reads the run; LLM cache hit returns identical content/tool calls/parsed output with no API call and cost 0; request sends strict schema without `maxItems`, `provider.require_parameters`, and drops `temperature`/`seed` when the model does not list them; missing key raises the clear error.
- `uv run python -m ma_engine.engine` with no `.env`: prints `Error: OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your OpenRouter key.`, exit code 1, no traceback, no run folder created.

Documentation findings (read before writing the clients):
- OpenRouter's model list (`/api/v1/models`, fetched 2026-10-31) gives `anthropic/claude-sonnet-5.5` these supported parameters: include_reasoning, max_completion_tokens, max_tokens, reasoning, reasoning_effort, response_format, stop, structured_outputs, tool_choice, tools, verbosity. **No `temperature` and no `seed`.** With `provider.require_parameters: true`, sending them would leave no provider. So the client reads the model's supported parameters once (first non-cached call) and sends `temperature`, `seed` and `max_tokens` only if listed; the trace records `dropped_parameters`. For this model, repeatability rests on the response cache only.
- OpenRouter structured output (Chat Completions): `response_format: {type: "json_schema", json_schema: {name, strict: true, schema}}`; `provider: {require_parameters: true}` passed through `extra_body`. Usage (tokens and `cost` in credits = USD) is always in `response.usage` on Chat Completions; `usage: {include: true}` is deprecated.
- OpenRouter tool calling: tool results go back as `{"role": "tool", "tool_call_id", "content"}`; the `tools` parameter must be in every request of a tool conversation. That is why the final report call still sends the tools, with `tool_choice: "none"`.
- Anthropic strict JSON schema: supports `$ref`/`$defs`, `enum`, `required`, `additionalProperties: false`; rejects `maxItems`, `minItems` above 1, `minimum`/`maximum`, `minLength`/`maxLength` with a 400. `strict_schema()` moves these into the field description ("At most 5 items."); Pydantic still enforces them after parsing, and a violation goes to the repair step.
- openai SDK 3.42.0: built-in retry (`max_retries=config.MAX_RETRIES`, backoff 0.5 s to 8 s) retries connection errors, timeouts, 408, 409, 429 and 5xx only; used instead of a hand-written retry.
- FastMCP 4.1.4 client: `StdioTransport(command, args, cwd, keep_alive, log_file)`; stdio servers inherit only HOME, LOGNAME, PATH, SHELL, TERM, USER (the server needs nothing else); `call_tool(..., timeout=, raise_on_error=False)` returns `is_error` and the message; `mcp_types.Tool.input_schema` is the argument schema.

Decisions made (and why):
- `rank_acquirers` is hidden from the research agent: its output covers every buyer, which would widen the citable IDs beyond this buyer's evidence; the agent already has its own ranking entry.
- The ranking's target block is added to each buyer's evidence (as a `rank_acquirers` result) because it is in the prompt, so `$200.0M` and the band label can be quoted.
- A committee decision is judged only by `conviction:` messages, so a report that was already not verified can still get a reviewed conviction; its status is then re-set by `apply_check`.
- Errors that hit every buyer equally (authentication, permission) stop the run instead of eating the reserve list.
- Writer prompt gained one rule after the first engine test: "25 deals, 22 of them closed" must cite closed-deal IDs only; citing `deal_count`'s full ID list (which includes Withdrawn deals) fails the completion-word check. The fake writer hit this; a real model would too.

Differences from the plan:
- `temperature`/`seed` are not sent to the configured model (see findings), so "low temperature, fixed seed" does not apply to `claude-sonnet-5.5`; only the cache makes runs repeatable. The README (Subplan 6) must say so.
- `MCPClient` takes an optional `transport` so most agent tests use the in-memory server (fast); stdio is tested separately (listing, concurrency, error results, full engine run).
- `research_buyer` takes an optional `progress` callback (for live steps in Streamlit).
- Timing (step 8) not measured: no API key. The 60-second target is unverified.

Needs the real-key smoke test (`uv run python -m ma_engine.engine`, then `make check`):
- Wall time cold and warm (second run should be all cache hits, near 2–3 s), tokens, cost; the summary line prints them, `trace.json` has per-buyer detail.
- That OpenRouter accepts the final call: tools + `tool_choice: "none"` + strict `response_format` + `require_parameters` on `anthropic/claude-sonnet-5.5`. A 404 "no endpoints" or a 400 here is the most likely first failure.
- Verified count after repair, the most common first-check issues (`trace.json` -> `buyers[].first_check_issues`), conviction mix after the committee, and `make check` similarity pairs.
- If over 60 s: lower `MAX_TOOL_ROUNDS` first, then shorten the prompt (the plan's order).

Open issues / needs approval:
- No prompt caching (Anthropic `cache_control`) is used; the 10 system prompts share their first ~1,100 words, so it could cut cost and time later.
- Trace for a buyer that raised is lost (only the error message is kept, in `errors` and `substitutions`).

### Review notes — Subplan 5
- Confirmed on OpenRouter's live model list: `anthropic/claude-sonnet-5.5` does not accept `temperature` or `seed`, but does accept `reasoning_effort`. Added `REASONING_EFFORT = "low"` to `config.py`; the client sends it when the model lists it as supported (same rule as the other optional parameters). Expected to matter for the 60-second target; to be measured in the real run.
- Repeat runs of the same target are identical through the response cache only. The README must say this plainly.
- Real-key smoke test still pending: `uv run python -m ma_engine.engine`, then `make check`.

## Subplan 7 — Fix 2 (double rounding)

Files changed: `src/ma_engine/prepare.py` (`round_half_up`, `median_stat`, `extreme_stat`, `_distribution`, `build_sector_stats`; `build_acquirer_profiles` is fixed through `median_stat`/`extreme_stat`), `tests/test_prepare.py` (new `test_display_is_rounded_once_from_the_true_value`). `ranking.py`, `tools.py` and `data_client.py` already format from unrounded values; unchanged.

What changed:
- Display strings are now built from the unrounded value; `value` is rounded to its places separately.
- `round_half_up` now reads the float at 12 significant digits before rounding. Without this, removing the 2-decimal pre-round exposed float noise: 23 computed values sat on a rounding edge as e.g. `9.149999999999999` (median of 9.1 and 9.2), which would display `9.1x` instead of the correct `9.2x`. The old pre-round hid this by accident. 12 digits is far below the CSV's 2-decimal precision and far above float noise.

Commands: `uv run python -m ma_engine.prepare` twice, `diff -r` of the two outputs: byte-identical. Old outputs saved to `/tmp/processed_before` and compared cell by cell.

Changed display cells (each checked against exact decimal arithmetic):
| File | Row | Column | Old -> New | True value |
|---|---|---|---|---|
| valuation_comps.csv | Behavioral Health / sector_only / Under $100M | ev_revenue_p25_display | 2.4x -> 2.3x | 2.3475 |
| valuation_comps.csv | Home Health/Hospice / sector_plus_adjacent / $400M–$1B | ev_revenue_p75_display | 2.8x -> 2.7x | 2.745 |
| valuation_comps.csv | Dental / sector_only / $100M–$400M | ev_revenue_p25_display | 2.1x -> 2.0x | 2.045 |
| valuation_comps.csv | Dental / sector_only / $100M–$400M | ev_revenue_p75_display | 3.1x -> 3.0x | 3.0475 |
| valuation_comps.csv | Revenue Cycle / sector_only / $400M–$1B | ev_revenue_p25_display | 3.3x -> 3.2x | 3.2475 |
| valuation_comps.csv | Revenue Cycle / sector_plus_adjacent / All sizes | ev_revenue_p25_display | 4.1x -> 4.0x | 4.045 |
| valuation_comps.csv | Health IT / sector_only / $100M–$400M | ev_revenue_p25_display | 5.5x -> 5.4x | 5.445 |
| valuation_comps.csv | Health IT / sector_plus_adjacent / All sizes | ev_revenue_p25_display | 4.1x -> 4.0x | 4.045 |
| acquirer_profiles.json | refresh mental health | closed_deal_size.median.display | $65.6M -> $65.7M | 65.65 |

Changed stored values (float-noise fix; no display change): valuation_comps `ev_revenue_p75` Healthcare Services / sector_only / $100M–$400M 3.22 -> 3.23 and Physician Groups / sector_plus_adjacent / $100M–$400M 2.76 -> 2.77; `ev_revenue_median` Medical Devices / both scopes / Over $1B 5.07 -> 5.08; `ev_revenue_p25` Pharma/Biotech / both scopes / $100M–$400M 7.48 -> 7.49; acquirer_profiles `boston scientific.closed_ev_revenue_median.value` 5.07 -> 5.08, `warburg pincus.closed_ev_revenue_median.value` 2.51 -> 2.52, `refresh mental health.closed_deal_size.median.value` 65.6 -> 65.7. No change in sector_stats.csv, transactions.csv, sector_similarity.csv or data_quality.json. The default target's Healthcare Services cells shown in the app (median/p25 displays) did not change.

Tests: `uv run pytest -q` -> `2 failed, 118 passed`. Both failures are in `tests/test_agent.py` (`KeyError: 'warnings'` in `clients/llm_client.py`, which another agent edited in parallel for Fix 3 at 19:01); this fix doesn't touch them. `tests/test_prepare.py tests/test_ranking.py tests/test_tools.py`: 59 passed. No fixture needed changing.

## Subplan 7 — Fix 3 (run-level errors and substitution)

Files changed: `src/ma_engine/clients/llm_client.py` (new `LLMConfigError`, `OPTIONAL_PARAMETERS`, `EMPTY_SUPPORTED_WARNING`; `LLMResult.warnings`; `_call`), `src/ma_engine/engine.py` (`FATAL_ERRORS`, new `pick_reserve`, `_research_all`, `run_engine`, `main`), `src/ma_engine/agent.py` (`research_buyer` split into a wrapper plus `_research_and_write`; `llm_record` adds `warnings`), `tests/test_agent.py`.

What changed:
- Run-level errors (Finding 7). `run_engine` calls `llm.supported_parameters()` once after ranking and before research. A missing or unknown `LLM_MODEL` raises `LLMConfigError` (a `RuntimeError` subclass), so a typo stops the run once instead of burning every reserve. `FATAL_ERRORS` is now `LLMConfigError`, `AuthenticationError`, `PermissionDeniedError` and `RateLimitError` (one that reaches the engine has already survived the SDK's retries). When one buyer raises a fatal error, the other buyers' tasks are cancelled and nothing is saved. `engine.main()` prints one line, `Run stopped: <ErrorType>: <message>`, to stderr and exits 1. Other `RuntimeError`s from the client (for example "OpenRouter returned no choices") stay per-buyer, because they can be one provider's failure.
- Honest substitution (Finding 8). `pick_reserve` takes the first reserve of the failed buyer's `acquirer_type` when losing it leaves fewer than `ranking.MIN_PER_TYPE` of that type among the other current top buyers; otherwise the next reserve. The replacement's `selection_note` is set to "Replaced <display_name> after an error" before it is researched, so the writer sees the true reason. After research, reports keep shortlist order and are renumbered 1..N; `score_rank` is unchanged. Each substitution records `replacement_shortlist_rank` (was `replacement_rank`) and `same_type_rule`.
- Failed buyer's trace kept. `research_buyer` attaches the trace collected so far to any exception it raises (`exc.trace`); the engine stores it as `errors[].partial_trace` in `trace.json`.
- Empty supported list (Finding 20). If OpenRouter lists no supported parameters, none of `temperature`, `seed`, `reasoning_effort`, `max_tokens` is sent, `dropped_parameters` lists all four, and the call carries a warning that is written to each `llm_calls[]` entry in the trace.

Data note: with the default target and `SHORTLIST_SIZE = 15`, all 5 reserves (ranks 11–15) are Financial Sponsors, so losing one of the 3 Strategics still falls back to a Financial Sponsor and the run ends with 2 Strategics. The same-type rule only has a Strategic to pick when the shortlist is longer (the first Strategic reserve is `atrium health` at rank 18). The test uses `SHORTLIST_SIZE = 20` to exercise it. Whether the shortlist should always hold a reserve of each type is an open question for Fix 4 or later; not changed here.

Tests added or changed in `tests/test_agent.py`: `test_engine_substitutes_a_failed_buyer` (now also checks ranks 1..10, the replacement note, `score_rank` kept, and the failed buyer's partial trace in `trace.json`), `test_engine_keeps_buyer_type_rule_when_substituting`, `test_pick_reserve_falls_back_to_next_when_no_same_type`, `test_wrong_model_stops_the_run_once` (through `engine.main()`: exit code 1, exactly one stderr line, no chat requests, no substitutions, no saved run), `test_rate_limit_during_research_stops_without_substituting`, `test_llm_empty_supported_list_sends_no_optional_parameters`. `FakeLLM` gained `supported_parameters()` and a configurable `error`, and failing buyers now fail at the write step so a partial trace exists.

Commands and real output:
- `uv run pytest tests/test_agent.py -k "substitut or pick_reserve or wrong_model or rate_limit or empty_supported" -v`: 6 passed.
- `uv run pytest -q`: `125 passed in 15.38s`.

Open issues:
- A fatal error leaves an empty run folder (created before ranking). Moving folder creation after ranking is Finding 18 (Fix 7).
- Real-key check of the one-line message for a wrong model id is still to do in the first real run.

## Subplan 7 — Fix 4 (ranking: direct precedent and coverage)

Decisions D1 and D2 approved by the owner. Weights were not changed.

Files changed: `src/ma_engine/ranking.py` (`_repeat_activity`, `_three_places`, `conviction_thresholds`, `_conviction`, `_score_buyer` adds `direct_closed_count`, `apply_type_coverage` now takes `sector`, new `_has_direct`, `_promotion_order`, `_direct_note`, `_fallback_warning`, `_cover_reserves`, `COVERAGE_RULE` text, `main`), `src/ma_engine/mcp_server/tools.py` (`rank_acquirers` docstring), `src/ma_engine/engine.py` (trace records `reserve_added_keys`; nothing else), `tests/test_ranking.py`, `tests/test_agent.py`, `tests/test_guardrails.py`.

What changed:
- D1 (Finding 5). Repeat activity = weighted / (weighted + `REPEAT_HALF_CREDIT`), where weighted is the sum of sector closeness over relevant closed deals (target sector 1.0, adjacent sectors 0.7–0.8 from `sector_similarity`). Same curve and same half-credit point (3). Repeat activity never used the `PRIOR_DEALS` blend; that is unchanged (the curve itself damps thin histories). Explanation now reads, for example, "2 direct + 5 adjacent relevant closed deal(s) = 5.7 weighted (a Healthcare Services deal counts in full, an adjacent-sector deal counts its sector closeness); 3 weighted deals earn half credit, with diminishing returns after that." The weighted count is shown to 1 decimal.
- D2 (Finding 6). Each buyer now carries `direct_closed_count` (closed deals in the target sector). `apply_type_coverage` promotes missing-type buyers with a direct deal first (best score first). Only when no direct-deal buyer of that type is left does it promote others, and then it adds a warning naming them ("Not enough Strategic candidates have a closed Healthcare Services deal, so N Strategic buyer(s) without one were added to the top 10 on score alone: ..."). Reading of "if none exist": the fallback applies to the slots that direct buyers cannot fill, so a pool with 1 direct Strategic promotes it plus 2 fallback buyers with a warning. Every promotion note ends with either "It has N closed <sector> deal(s)." or "It has no closed <sector> deal; no <type> candidate with one was left."
- Reserves (Fix 3 open issue). After the top `REPORT_COUNT`, the `SHORTLIST_SIZE - REPORT_COUNT` reserve places hold at least one buyer of each type when the pool has one beyond them. The missing type's best candidate (direct deals first) replaces the lowest-scoring reserve of a type that has more than one reserve. It gets a note ("Added as a reserve so a failed Strategic buyer can be replaced by one of the same type; score rank X of N candidates. ...") and is listed in the new `reserve_added_keys`. `SHORTLIST_SIZE` still means "top `REPORT_COUNT` plus reserves". Done in ranking, so the engine's slice is unchanged. For the default target no swap is needed after D1/D2: VNS Health (Strategic) is already reserve rank 14.
- Finding 10. Conviction bars display with 3 decimals (`conviction_thresholds[*].display`) and the reason quotes the score with 3 decimals: "Score 0.665; 4 relevant closed deal(s) within 2x of the target EV. High bar 0.603 ...". `score.display` stays 2 decimals. Levels are decided on the same 4-decimal values as before.

Before (default target, top 15; Direct = closed Healthcare Services deals):
| # | Score rank | Buyer | Type | Score | Conviction | Direct | Note |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Blackstone | Financial Sponsor | 0.6735 | High | 2 | |
| 2 | 2 | New Mountain Capital | Financial Sponsor | 0.6629 | High | 1 | |
| 3 | 3 | Nordic Capital | Financial Sponsor | 0.6372 | High | 4 | |
| 4 | 4 | Vista Equity | Financial Sponsor | 0.6223 | High | 2 | |
| 5 | 5 | Bain Capital | Financial Sponsor | 0.6148 | High | 1 | |
| 6 | 6 | Francisco Partners | Financial Sponsor | 0.6098 | Medium | 2 | |
| 7 | 7 | GTCR | Financial Sponsor | 0.6090 | Medium | 1 | |
| 8 | 10 | VNS Health | Strategic | 0.6027 | Low | 0 | |
| 9 | 16 | Aspen Dental (ADMI) | Strategic | 0.5766 | Medium | 0 | Included to ensure at least 3 Strategic buyers in the top 10; score rank 16 of 50 |
| 10 | 17 | Universal Health Services | Strategic | 0.5748 | Low | 0 | Included to ensure at least 3 Strategic buyers in the top 10; score rank 17 of 50 |
| 11 | 8 | Welsh Carson | Financial Sponsor | 0.6050 | Medium | 1 | Moved below the top 10; score rank 8 |
| 12 | 9 | Veritas Capital | Financial Sponsor | 0.6042 | Medium | 1 | Moved below the top 10; score rank 9 |
| 13 | 11 | General Atlantic | Financial Sponsor | 0.6004 | Medium | 1 | |
| 14 | 12 | Advent International | Financial Sponsor | 0.5977 | Medium | 1 | |
| 15 | 13 | KKR | Financial Sponsor | 0.5943 | Medium | 1 | |

Promoted: Aspen Dental, Universal Health Services. Displaced: Veritas Capital, Welsh Carson. All 5 reserves Financial Sponsors. Atrium Health, Steward Health Care, UPMC: score rank 18 / 24 / 25.

After:
| # | Score rank | Buyer | Type | Score | Conviction | Direct | Note |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Blackstone | Financial Sponsor | 0.6645 | High | 2 | |
| 2 | 2 | New Mountain Capital | Financial Sponsor | 0.6536 | High | 1 | |
| 3 | 3 | Nordic Capital | Financial Sponsor | 0.6323 | High | 4 | |
| 4 | 4 | Vista Equity | Financial Sponsor | 0.6150 | High | 2 | |
| 5 | 5 | Bain Capital | Financial Sponsor | 0.6061 | High | 1 | |
| 6 | 6 | Francisco Partners | Financial Sponsor | 0.6024 | Medium | 2 | |
| 7 | 7 | GTCR | Financial Sponsor | 0.5983 | Medium | 1 | |
| 8 | 16 | Atrium Health | Strategic | 0.5686 | Low | 3 | Included to ensure at least 3 Strategic buyers in the top 10; score rank 16 of 50 candidates. It has 3 closed Healthcare Services deal(s). |
| 9 | 20 | Steward Health Care | Strategic | 0.5532 | Low | 3 | Same rule; score rank 20 of 50. It has 3 closed Healthcare Services deal(s). |
| 10 | 22 | UPMC | Strategic | 0.5516 | Low | 3 | Same rule; score rank 22 of 50. It has 3 closed Healthcare Services deal(s). |
| 11 | 8 | Welsh Carson | Financial Sponsor | 0.5960 | Medium | 1 | Moved below the top 10; score rank 8 |
| 12 | 9 | Veritas Capital | Financial Sponsor | 0.5953 | Medium | 1 | Moved below the top 10; score rank 9 |
| 13 | 10 | General Atlantic | Financial Sponsor | 0.5907 | Medium | 1 | Moved below the top 10; score rank 10 |
| 14 | 11 | VNS Health | Strategic | 0.5893 | Low | 0 | |
| 15 | 12 | Advent International | Financial Sponsor | 0.5867 | Medium | 1 | |

Promoted: Atrium Health, Steward Health Care, UPMC. Displaced: General Atlantic, Veritas Capital, Welsh Carson. No coverage warnings. Reserves hold 4 Financial Sponsors and 1 Strategic (VNS Health). Conviction bars: High 0.603, Medium 0.545 (were shown as 0.61 / 0.55). Top 10 conviction: High 5, Medium 2, Low 3.

Why names moved. D1 lowers every buyer whose relevant deals are partly adjacent, and leaves buyers whose deals are all in Healthcare Services unchanged. VNS Health (0 direct, 6 adjacent deals = 4.5 weighted) fell from score rank 10 to 11, so the score-only top 10 became 10 Financial Sponsors; it is not promoted because it has no direct deal, and it stays on the shortlist as the Strategic reserve (rank 14). Aspen Dental (0 direct, score rank 18 now) and Universal Health Services (0 direct, score rank 17 now) were in only through the coverage rule and are no longer eligible under D2, so both left the shortlist. Atrium Health, Steward Health Care and UPMC kept exactly the same scores (3 direct deals each, so weighted = plain count) and moved up in score rank only because others dropped (18 -> 16, 24 -> 20, 25 -> 22); they entered the top 10 through the coverage rule as the only Strategics with closed Healthcare Services deals, not on score alone, and their notes say so. They start Low because each has at most 1 relevant closed deal within 2x of the target EV. General Atlantic moved from score rank 11 to 10 but was displaced along with Welsh Carson and Veritas Capital (one more displacement than before because a third Strategic had to be promoted). KKR (score rank 13) dropped off the 15-buyer shortlist because the extra displaced buyer and VNS Health now fill the reserve places ahead of it. D1 alone did not lift any direct-precedent health system into the top 10 on score: each has only 3 relevant closed deals in total (repeat activity 0.50) and at most 1 near the target size.

Fixtures updated (new values are correct):
- `tests/test_guardrails.py` valid Blackstone report: conviction reason "Score 0.67 ... High bar of 0.61" -> "Score 0.665 ... High bar of 0.603" (the 2-decimal bar no longer exists in tool output); `test_evidence_uses_display_strings_not_raw_values` uses Blackstone's new score 0.6645 / display 0.66.
- `tests/test_agent.py` full-run and same-type-substitution tests used VNS Health as the top-10 Low Strategic; it is now reserve rank 14, so they use Atrium Health (Strategic, starts Low, in the top 10). The same-type test now replaces Atrium Health with VNS Health.
- `tests/test_ranking.py` coverage fixture (Strategics with Dental deals only) is now the fallback case: note and warning text updated.

Tests added (`tests/test_ranking.py`): `test_repeat_activity_counts_direct_deals_more_than_adjacent`, `test_coverage_prefers_strategic_with_a_direct_deal`, `test_missing_type_is_promoted_into_top_10_with_fallback_warning`, `test_no_fallback_warning_when_direct_strategics_suffice`, `test_reserves_hold_both_types_when_available`, `test_reserves_unchanged_when_pool_has_no_other_type`, `test_default_promotions_have_direct_deals_and_reserves_hold_both_types`, `test_conviction_reason_uses_three_decimals`. `test_rank_and_score_rank_are_consistent` now counts reserve-added buyers as moved. `test_no_buyer_names_in_ranking_code` kept and passing.

Commands and real output:
- Before/after tables: `uv run python /tmp/fix4/top15.py` (helper calling `ranking.rank_acquirers(Target.default())`), run before and after the change.
- `uv run python -m ma_engine.ranking`: "Conviction bars: High 0.603 (p90), Medium 0.545 (p50) ... Promoted: ['atrium health', 'steward health care', 'upmc']  Displaced: ['general atlantic', 'veritas capital', 'welsh carson']  Reserves added: []".
- `uv run pytest tests/test_ranking.py -v -k "..."` on the new and kept tests: `9 passed, 18 deselected in 1.68s`.
- `uv run pytest -q`: `132 passed in 15.69s`.

Open issues:
- All three promoted Strategics start Low and sit at score ranks 16–22; the top 10 now shows direct precedent but not stronger scores. The Fix 6 sensitivity table should show how stable this is.
- Fix 5 (`weight_display`, sector-fit blended value in its explanation) is not part of this fix.

## Subplan 7 — Fix 1 (claim-to-source binding)

Findings 3, 4 and 11. Before this fix, `build_evidence` pooled every ID, number and name per buyer into flat sets, so "In 2021, Blackstone paid $299.0M at 14.6x for Evergreen Care Solutions" citing MA-2021-0407 (really Peak Medical Management, $341.7M, 10.0x) passed with zero issues.

Design:
- `build_evidence` keeps the flat sets (used by the existing checks, `agent.py` trace counts and tests) and adds a list of `Source` items. Each one has the IDs that support it, a kind, its number tokens, and (number, n) pairs. It is built by walking every tool result, using the real shapes checked by running the tools:
  - A deal row (anything with `transaction_id`: `get_acquirer_deals`, `search_deals`) supports only its own ID. It is `buyer` if its `acquirer_key` is this buyer, otherwise `comp`.
  - A dict with non-empty `source_ids` (profile stats, comps distributions, sector stats, ranking signals) supports those IDs.
  - Anything else takes the IDs of the result or statistic it sits in. For example, comps `closed_deal_count`, sector `median_deal_size` and ranking `starting_conviction` take, respectively, the comps result's `source_ids`, the sector's `source_ids`, and the ranking entry's `supporting_closed_ids` plus `weak_signal_ids`.
  - Kind by tool: `get_acquirer_profile`, `get_acquirer_deals` and the ranking entry are `buyer`; comps, sector overview, `search_deals` and the dataset overview are `comp`.
  - The target block (`data.target`, which the engine adds as a pseudo result) is `target`: it can be quoted anywhere without a citation, and its names are exempt.
  - `n` / `sample_size` is paired with every number in the same dict (comps p25/median/p75 with their `sample_size`, profile medians with `n`).
- Names, outcomes, deal types and years belong to a single deal, so they are looked up by cited ID from the dataset (`Deal` tuple, cached). A name must also still appear in the evidence (the existing check).

New checks, for every `fact` and `interpretation` statement:
- Interpretations must cite at least one ID.
- Numbers: each number must be in a source whose IDs overlap the cited IDs. In `acquirer_overview`, `strategic_fit` and `precedent_activity` only `buyer` sources count. In `valuation_context`, `risk_flags` and `conviction`, comps count too. Target-block numbers are exempt.
- Years: a year passes if it is the deal year of a cited deal, or if it is in an overlapping source (for example `first_deal_year`, or "the dataset ends in 2024" in the ranking's recency explanation). A wrong year gets its own message, listing each cited deal's year.
- n=<count>: it must be the n stored beside a number quoted in the same statement. If no quoted number has a stored n (as in "small sample (n=10)"), it must be one of the n values stored for the cited IDs. The number check no longer reads n= counts, so each problem produces one message, not two.
- Outcomes: each Withdrawn, Pending, Terminated or Rumored word (any case) must be the outcome of a cited deal. A named cited deal whose outcome is not among the outcome words used is flagged. The completion-word check is unchanged.
- Deal types: a deal type written in dataset spelling (plural allowed, case-sensitive) must be the type of a cited deal. Plain prose ("a new platform", "an add-on", "a strategic acquisition") is not read as a deal type.
- Company names: a name must be the target or acquirer of a cited deal. The buyer's own CSV names and target-block names are exempt.
- Comp IDs: `acquirer_overview` and `strategic_fit` must cite at least one of this buyer's deals. `precedent_activity` may cite only this buyer's deals (stricter than "at least one", because precedents are by definition the buyer's own deals).
- Valuation: the closed-only and n= checks now apply to every non-unknown `valuation_context` statement.
- Messages name the owning ID and list the cited deal's own figures. For example: `precedent_activity[0]: "$299.0M" belongs to MA-2021-0195, but the statement cites MA-2021-0407. Cite MA-2021-0195 or use the figures for MA-2021-0407 ($341.7M, EV/EBITDA 10.0x, EV/Revenue 1.5x, 2021).`

Known limit: small counts ("6", "4") appear in many buyer statistics, so overlap cannot tie them to one statistic. Prices, multiples, margins, names, years and n values are distinctive enough to bind.

Review cases (all now fail with one specific message per problem; tests in `tests/test_guardrails.py`):

| Case | Before | Now |
|---|---|---|
| Swapped price, multiple and target name (cites MA-2021-0407) | passed | 3 issues: `$299.0M` and `14.6x` belong to MA-2021-0195; Evergreen Care Solutions is the target of MA-2021-0195 |
| Closed deal described as Withdrawn | passed | `says "Withdrawn", but no cited deal is Withdrawn (MA-2021-0407 is Closed)` |
| Wrong deal type (Platform Investment for a SPAC Merger) | passed | `no cited deal is a Platform Investment (MA-2021-0407 is a SPAC Merger)` |
| `12.4x (n=10)` where 12.4x has n=75 | passed | `n=10 is not the sample size ... 12.4x has n=75` |
| Another buyer's comp ID as a precedent (MA-2018-0470, New Mountain Capital) | passed | `cites MA-2018-0470, which is not this buyer's deal` |
| Fit statement citing only comp IDs | passed | `cites only comps or other buyers' deals` |
| Comps median 12.1x quoted as a precedent's multiple | passed | `"12.1x" is a comps or other-buyer figure ... use MA-2021-0195 ($299.0M, EV/EBITDA 14.6x ...)` |
| Interpretation without an ID | passed | `is an interpretation but cites no transaction ID` |
| Valuation interpretation without n= | passed | `valuation interpretation has no sample size` |
| Wrong year (2019 for a 2021 deal) | passed | `"2019" is not the year of any cited deal (MA-2021-0407 is 2021)` |

False-positive check: correct reports built from real tool output, written in the prompt's style, for Blackstone (Financial Sponsor, 20 statements), Atrium Health (Strategic, 17), VNS Health (Strategic, adjacent-only, 17) and Nordic Capital (Financial Sponsor, 20). Each research path included the target block, profile, both comps scopes, closed relevant-sector deals, all deals and the sector overview. All four had 0 issues. Twelve riskier phrasings also passed with 0 issues:
- non-closed counts ("2 Withdrawn deals and 1 Rumored deal")
- two direct deals named with a shared year
- Platform Investment and Bolt-on Acquisition counts with "a new platform or an add-on"
- closeness 0.8 and 0.75 from the target block
- comps median beside the buyer's own median, each with its n
- a sample size stated alone ("small sample (n=10)")
- "Q2 2021"
- the largest deal quoted as `$14,353.7M`
- the ranking's recency explanation copied as written
- an Atrium Health relevant-deal count
- the starting conviction reason reused in `strategic_fit`
- a precedent with geography, deal type, EV/Revenue and bidder count

The run surfaced one false failure, a sample size stated alone, which is now fixed with the fallback described under n=<count> above. Three buyers are kept as `test_realistic_correct_report_passes[...]`.

Fixture: the valid Blackstone fixture passes unchanged; nothing in it was wrong.

Files changed: `src/ma_engine/guardrails.py` (Source and Deal, evidence walker, six new checks, updated valuation and names checks, docstring), `src/ma_engine/agent.py` (trace records the source count), `src/ma_engine/prompts/research_writer.md` (rules 3–5 and 8: interpretations cite IDs, quote figures only next to their own deal, deal-type spelling, buyer-versus-comps sections, n beside the multiple), `src/ma_engine/prompts/repair.md` (how to fix binding, comp-ID, n= and interpretation issues), `tests/test_guardrails.py`.

Tests changed: `test_interpretation_without_id_passes` became `test_interpretation_without_id` (it now fails, as intended). Valuation message wording changed from "valuation facts" to "valuation statements". `test_valuation_sample_size_not_in_evidence` now expects the n= message (`n=77 ... 12.4x has n=75`). Added 19 tests (16 binding cases and 3 realistic reports).

Commands and real output:
- `uv run pytest -q tests/test_guardrails.py`: `62 passed in 2.20s`
- `uv run pytest -q`: `151 passed in 17.69s`

Open issues:
- Small-count binding limit (above).
- Overlap means a statistic over many deals (for example the buyer's 22-deal median 17.2x) can be quoted beside any one of those deals' IDs. It cannot be pinned to the single deal the sentence describes.
- No live LLM run yet, so the repair rate on the new messages is unmeasured.

## Subplan 7 — Fixes 5-7

Findings 12, 13, 14, 15, 16, 17, 18, 19 and the overview-size prompt note. Fixes 1-4 were kept as they are: the claim-to-source binding is extended (one new source kind), not replaced. Weights, thresholds and the default ranking are unchanged (same top 10 and top 15 as the Fix 4 "After" table).

Files changed: `src/ma_engine/guardrails.py`, `src/ma_engine/ranking.py`, `src/ma_engine/mcp_server/tools.py`, `src/ma_engine/agent.py`, `src/ma_engine/engine.py`, `src/ma_engine/prompts/research_writer.md`, `tests/test_guardrails.py`, `tests/test_ranking.py`, `tests/test_tools.py`, `tests/test_agent.py`. `prepare.py` and `data/processed/` are unchanged, so `make data` was not needed.

### Fix 5 — quality gate and assumption labelling
- Finding 12. `quality_metrics` adds `full_report_count` (at least `config.REPORT_COUNT` reports) and `all_reports_verified` to the pass rule, and reports `required_report_count`. `make check` prints one reason line, for example `Result: FAIL (8 of 10 reports; 2 report(s) not verified)`; other failed checks keep their names (`conviction_varies`, ...).
- Finding 13. `get_sector_overview` returns each other sector as `{sector, is_adjacent, assumption: {closeness, closeness_display, reason}}`, and its warning and docstring say anything from an `assumption` field is an interpretation. Writer prompt rule 6 says the same in one sentence. In `guardrails._walk` an `assumption` field becomes a Source of kind `assumption` with no IDs, so its numbers can never bind to a cited transaction ID (before, the closeness numbers inherited the sector's 46 deal IDs as comp evidence). Quoting one now fails with: `"0.35" is a hand-written assumption (sector closeness), not a fact about any deal, so no citation can support it. Remove the number, or quote the closeness shown in the target block.` Closeness for the relevant sectors is still quotable from the ranking's target block, as before.
- Finding 16. Every ranking signal carries `weight_display` (`"0.25"`, `"0.10"`), so the raw weight is no longer evidence. The sector-fit explanation now states both values, labelled: "average sector closeness 0.81 before the blend below, 0.74 after it (the value scored)".

### Fix 6 — sensitivity table
`uv run python -m ma_engine.ranking --sensitivity` (optional `--sector`, `--ev`; plain `python -m ma_engine.ranking` still prints the diagnostic table). `ranking.sensitivity(target)` re-runs `rank_acquirers` per variant with module constants replaced temporarily and restored afterwards (`WEIGHTS`, `PRIOR_DEALS`, `MIN_PER_TYPE`), or with a client whose `is_adjacent` is recomputed in memory from the closeness scores. No file or `config.py` change. A dropped signal gets weight 0 and the other weights are scaled to sum to 1. "Coverage rule off" sets `MIN_PER_TYPE = 0`, so the top 10 is score order. About 3 s for 13 rankings.

Real output, default target (base top 10: Blackstone, New Mountain Capital, Nordic Capital, Vista Equity, Bain Capital, Francisco Partners, GTCR, Atrium Health, Steward Health Care, UPMC):

| Variant | Overlap with base | Names in | Names out |
|---|---|---|---|
| equal weights | 7/10 | VNS Health, Veritas Capital, Universal Health Services | Francisco Partners, Steward Health Care, UPMC |
| drop sector_fit | 7/10 | VNS Health, Welsh Carson, Ardent Health Services | Francisco Partners, Steward Health Care, UPMC |
| drop size_fit | 7/10 | Veritas Capital, Welsh Carson, VNS Health | Francisco Partners, GTCR, UPMC |
| drop repeat_activity | 4/10 | Ardent Health Services, Aspen Dental (ADMI), Advocate Health, Elara Caring, VNS Health, Kindred at Home | Nordic Capital, Vista Equity, Bain Capital, GTCR, Steward Health Care, UPMC |
| drop profile_fit | 8/10 | Advent International, Veritas Capital | Bain Capital, GTCR |
| drop recency | 9/10 | Advocate Health | Steward Health Care |
| drop rationale_fit | 8/10 | Welsh Carson, Advocate Health | GTCR, Atrium Health |
| adjacency 0.5 | 9/10 | Welsh Carson | GTCR |
| adjacency 0.75 | 8/10 | Welsh Carson, VNS Health | GTCR, UPMC |
| prior off | 7/10 | Ardent Health Services, UnitedHealth Group, Advocate Health | GTCR, Steward Health Care, UPMC |
| coverage rule off | 7/10 | Welsh Carson, Veritas Capital, General Atlantic | Atrium Health, Steward Health Care, UPMC |

Reading it: the top 5 sponsors (Blackstone, New Mountain, Nordic, Vista, Bain) survive every variant except "drop repeat_activity", which is the most influential signal (4/10). The three promoted health systems (Atrium, Steward, UPMC) are the least stable names: they are in through the coverage rule, and most variants swap one or more of them. Recency now changes one name (it changed none before Fix 4). Adjacency 0.5 adds Revenue Cycle as adjacent; 0.75 drops Dental. "Prior off" brings in single-deal buyers (Ardent Health Services), which is what the prior is for.

### Fix 7 — small items
- Finding 14. `get_dataset_overview` no longer returns `strong_margin` (the default target's Healthcare Services threshold). That was the smaller change: `prepare.py` and `data_quality.json` keep the block (used by `make data`'s printout), and the ranking's target block already carries the threshold for the requested sector. The docstring says where to find it.
- Finding 15. The number reader skips quarter labels (`Q1`-`Q4`) and ordinals (`1st`, `2nd`, `23rd`, `4th`) and keeps a minus sign where it cannot be a hyphen or range dash: `number_tokens("-12.1x, Q1, 1st") == {"-12.1x"}`; `"2022-2024"` and `"$1.0M-$4.0M"` still give two positive tokens. `_near_miss` handles the sign. A report quoting `-12.1x` fails as not in the tool results.
- Finding 17. In `agent._research`, a per-buyer cache keyed on (tool name, arguments as sorted JSON) holds the MCP call itself, so an identical call in a later round or in the same round is answered from it without calling the server. The repeated result gets one extra warning (`agent.REPEAT_NOTE`: "You already made this exact call for this buyer; this is the same result again. ..."), and the trace entry has `repeated: true`.
- Finding 18. The run folder is created after ranking and the model check both succeed. Until then the MCP server log is a hidden file in `config.RUNS_DIR` (same disk), renamed into the run folder once it exists; if the run stops earlier, the hidden file is deleted, so nothing is left. If the timestamp folder already exists, `-2`, `-3`, ... is added.
- Finding 19. `ranking.rationale_tags(target)` adds `Margin Improvement` (dataset spelling, on 154 rows) when `margin_profile` is `"any"`. The default target is `"strong"`, so its ranking does not change.
- Writer prompt: acquirer_overview leads with the median closed deal size and mentions the smallest or largest deal only with that deal's sector, read from its row; otherwise the range is left out.

Tests added: `test_quality_metrics_fail_on_unverified_or_missing_reports`, `test_main_names_unverified_reports`, `test_sector_assumption_number_cannot_be_cited_as_a_fact`, `test_tokenizer_skips_quarters_and_ordinals_and_keeps_minus`, `test_quarter_label_and_negative_number_in_a_report` (guardrails); `test_signals_show_weight_and_scored_sector_fit`, `test_sensitivity_variants_and_constants_restored`, `test_margin_improvement_counts_only_for_any_margin_profile` (ranking); `test_repeated_identical_calls_use_the_per_buyer_cache`, `test_run_folder_gets_a_suffix_when_the_name_is_taken`, `test_failed_ranking_leaves_no_run_folder` (agent/engine). Changed: `test_sector_overview` checks the `assumption` shape; the 2-report quality tests set `config.REPORT_COUNT = 2`; `test_main_fails_on_bad_run` expects `Result: FAIL (1 of 10 reports; conviction_varies)`; `test_wrong_model_stops_the_run_once` now checks the runs folder is empty.

Commands and real output:
- `uv run python -m ma_engine.ranking --sensitivity`: table above.
- `uv run pytest -q`: `162 passed in 18.14s`.

Open issues:
- A fatal error during research (rate limit after retries) still leaves a run folder holding only `mcp_server.log`, because the folder now exists from the end of ranking. Not removed, since the log may explain the failure.
- `make check` still has no real run to check; the first real run is next.
- The cached-call note and the assumption rule are unmeasured on a live model.

## Dockerfile (added after Subplan 7)
**Files:** `Dockerfile`, `.dockerignore`, `plans/SUBPLAN_06_UI_DELIVERY.md` (README Docker option and Docker check), `plans/PRINCIPAL_ENGINEER_REVIEW.md` (one line updated).

**Design:** one stage on `ghcr.io/astral-sh/uv:0.6.17-python3.12-bookworm-slim`. Dependencies are installed from `uv.lock` with `uv sync --frozen --no-dev`, so pytest is left out. The project stays an editable install, so `config.ROOT` is `/app`. Processed tables are rebuilt from the raw CSV during the build. The app runs as user `app` (uid 1000), who can write only `outputs/` and `.cache/`. `tini` is PID 1, so `docker stop` reaches Streamlit and the MCP child process is cleaned up. Only port 8501 is exposed, with a health check on `/_stcore/health`. The `.dockerignore` is an allow-list: `pyproject.toml`, `uv.lock`, `.python-version`, `src/`, `data/raw/`. `.env` never enters the image; the key is passed at run time with `--env-file .env`.

**Checked (real output):** no Docker engine is running on this machine, so the image itself has not been built. The build steps were run on a scratch copy holding only the allow-listed files. Results:
- `uv sync --frozen --no-dev` succeeded and `import pytest` fails, as intended.
- `config.ROOT` resolves to the copy's root.
- `python -m ma_engine.prepare` output is identical to `data/processed/` (`diff -r` empty).
- The ranking runs and the MCP server lists all 7 tools.

**Open:** `docker build` and `docker run` are untested until a Docker engine is available. The `CMD` starts `src/ma_engine/app.py`, which Subplan 6 creates; until then the container builds but the app won't start. `outputs/sample/` gets added to the image once it exists (Subplan 6, Docker check).

## Subplan 6 — App and end-to-end tests — 2026-10-31
Scope: steps 1, 2 and 4 only (app, error states, end-to-end tests). README, `docs/`, Dockerfile, `make check`, the real-key run and `outputs/sample/` were not touched (other work).

Files changed:
- New: `src/ma_engine/app.py` (358 lines), `tests/test_end_to_end.py` (149), `tests/test_app.py` (128).
- No other file changed. No new environment variables, no new dependencies.

Commands to run:
- `make run` (or `uv run streamlit run src/ma_engine/app.py`)
- `uv run pytest -q`

What the app shows (display code only; it calls `engine.run_engine` with `asyncio.run` inside the button handler):
- Sidebar form: sector, EV ($M), geography (10 regions + "Any region"), ownership, margin profile (strong / any), defaults from `config.DEFAULT_TARGET`. Values are checked with `models.Target`; a bad value shows its message, for example `ev_mm: Input should be greater than or equal to 1`. Buttons: Run (cache on) and Regenerate (`use_cache=False`).
- Live progress: `st.status` writes every `progress` message from the engine (ranking, each buyer's tool rounds, repairs, report status, substitutions, committee review, saved folder).
- Summary: run id, target, folder; warnings for fewer than 10 reports, not-verified reports, failed buyers (error text), substitutions, excluded buyers. Ranked table: rank, buyer, type, score, score rank, starting conviction, final conviction, status (Verified / Not verified), note (`selection_note`: promoted by the type rule, displaced, or "Replaced X after an error"). Download buttons for `reports.md` and `reports.json` from the run folder.
- One expander per buyer (label shows rank, name, conviction, VERIFIED / NOT VERIFIED), with a red "Not verified" banner and its issues, and four tabs:
  - Report: `guardrails.render_markdown(report)`.
  - Source rows: the cited transaction IDs (statements, risk flags, conviction, in order) and the original CSV rows for them, read by `prepare.load_raw` (all 24 original columns, as text).
  - Score breakdown: score, score rank, starting conviction and reason, and the six signals with `value_display`, `weight_display`, `contribution_display` and explanation.
  - How this was decided: rounds, stop reason, repair attempts, buyer time; tool calls (round, tool, arguments, status, duration_ms, repeated, source_id_count, error); model calls (purpose, prompt and completion tokens, cost_usd, cache_hit, duration_ms, tool_calls_requested, parse_error); first-draft guardrail issues; length warnings.
  - "Not relevant: re-run without this buyer" button: adds the key to `st.session_state.excluded` and re-runs the same target with the cache on. Exclusions are cleared when the target changes.
- Run totals (from `trace.json`): wall time, model calls (and cache hits), tokens in/out, cost (USD), cache used or not, model, ranking/research/committee ms, MCP sessions opened; committee applied/rejected/similar pairs; the quality summary (`make check` checks).
- No run in the session: buttons to open the latest saved run in `outputs/runs/` and the sample in `outputs/sample/` (either `outputs/sample/reports.json` or its newest sub-folder). If neither exists: "No saved runs found in outputs/runs or outputs/sample." No key is needed to browse.

Error states (each one sentence):
- Missing processed or raw data: lists the missing file names and says to run `make data`; the page stops there.
- Missing key: the `config.require_api_key()` message ("OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your OpenRouter key.") plus "you can open a saved run below". Checked before the engine starts, so no MCP server or folder is created.
- `LLMConfigError` (model id), authentication, permission, rate limit: one message each.
- MCP server failed to start: any error before the engine's first progress message. Measured: a server that exits at start gives `MCPError('Connection closed')` with no progress message and no run folder.
- Any other stop: "The run stopped: <type>: <message>". Text shown to the user has the key replaced with `***` if it ever appears.

Decisions made (and why):
- Saved runs do not store the ranking signals, so for a saved run the Score breakdown tab calls `ranking.rank_acquirers` for the saved target and saved `excluded_keys` (cached with `st.cache_data`) and says so in a caption. Live runs use the ranking returned by `run_engine`. This is the same deterministic Python, not new math in the app; if the processed data changed since the run, the caption is the warning. Saving the ranking in `trace.json` would remove this; not done (engine change, out of scope).
- Dropdown options are the lists `models.Target` validates against (`prepare.SECTORS`, `models.GEOGRAPHIES`, `models.OWNERSHIPS`), which Subplan 1 checked match the data exactly (0 unexpected values). One source of allowed values for the form and the check.
- Source rows use the raw CSV (`prepare.load_raw`, cached), not the processed table, so the user sees the original row exactly.
- Run is stored in `st.session_state.current_run`: the first version used `run`, which clashed with the Run button's widget key (Streamlit stores widget values under their key); the app test caught it.
- Tool calls in one research round run in parallel and are written to the trace when each finishes, so the trace is ordered by round, not by start time inside a round. The app's label says so. One end-to-end test first assumed start order and failed once in about ten runs; it now compares by tool name.

End-to-end tests (`tests/test_end_to_end.py`, fake LLM from `test_agent.py`, the engine starts the real MCP server over stdio on the real processed data):
- `test_full_run_saves_ten_reports_trace_and_server_log`: 10 verified reports ranked 1..10; folder holds exactly `mcp_server.log`, `reports.json`, `reports.md`, `trace.json`; no hidden pending log left; `load_reports` round-trips; trace has 1 MCP session, 7 tools, per-buyer tool calls with dict arguments and durations; progress starts "Ranking buyers" and ends "Saved run".
- `test_missing_processed_data_and_raw_csv`: `DataClient` says "Run `make data` first"; `load_raw` says "Raw CSV not found".
- `test_server_without_processed_data_stops_before_anything_starts`: the real server module started with its transactions path pointed at a missing file (checked by hand: it dies with `Processed file not found: ... Run make data first.`); `run_engine` raises `MCPError` and leaves no run folder.
- `test_missing_key_stops_before_the_server_starts`: real `LLMClient`, empty key: the clear error, the MCP client is never created, no runs folder.
- `test_tool_failures_go_back_to_the_model_and_the_run_completes`: every buyer also makes 2 bad calls (unknown sector, limit 500); each comes back as an error result; all 10 reports verified.
- `test_one_buyer_failure_is_substituted_and_saved`: GTCR fails; the reserve replaces it; `trace.json` has the substitution and the partial trace; `reports.md` shows "Replaced GTCR after an error".
- `test_malformed_model_output`: Nordic Capital's draft quotes `$185M` twice and ends `not_verified` with the issue, shown with the banner in `reports.md`; `make check` (`guardrails.main`) exits 1 with "1 report(s) not verified". GTCR returns text that is not JSON twice: `ReportError`, treated as a buyer error and replaced (this is how the engine works; the plan's "malformed output ends Not verified" holds for output that parses but breaks the rules).
- Not repeated (already in `test_agent.py` the same way): committee one-step rule, invalid committee output, LLM cache and request format, same-type substitution rule, wrong model and rate-limit stops, single-buyer repair, run-folder naming. `test_agent.py`'s full-run test also uses stdio; the new one adds the files on disk, the server log and the trace fields the app reads.

App tests (`tests/test_app.py`, `streamlit.testing.v1.AppTest`, runs and sample folders pointed at a temp folder):
- Loads with no exception; title "M&A Acquirer Identification"; sidebar values equal `config.DEFAULT_TARGET`; "No saved runs found" when both folders are empty.
- Run with the key set to "": shows the missing-key message; no runs folder created.
- EV 0: sidebar shows `ev_mm: Input should be greater than or equal to 1`.
- Transactions file missing: error names `transactions.csv` and `make data`.
- MCP server that exits at start (fake key): "The MCP server failed to start"; the key is not shown.
- Live run (fake LLM, in-memory server): 10 buyer expanders, status "finished"; then "Not relevant" on Blackstone re-runs with `excluded == ["blackstone"]`, 10 expanders without Blackstone, and the exclusion notice.
- Saved run (made by a fake-LLM engine run, Nordic Capital not verified), key set to "": "Open Latest run" shows the run, 10 expanders, Nordic Capital labelled NOT VERIFIED with the banner, the rendered report, both download buttons, a source-rows table containing a cited ID, and the signals table.

Real results:
- `uv run pytest -q`: `176 passed in 31.99s`, then again `176 passed in 31.87s` (162 earlier + 7 end-to-end + 7 app).
- `uv run pytest -q tests/test_end_to_end.py tests/test_app.py`: `14 passed in 15.66s`.
- Headless start: `uv run streamlit run src/ma_engine/app.py --server.headless=true --server.port=8599`, then `curl http://localhost:8599/_stcore/health` printed `ok` (HTTP 200). Opened in a browser: sidebar with the default target and "No run in this session yet ... No saved runs found in outputs/runs or outputs/sample."; no errors in the server output. Stopped afterwards (no process left).

Open issues / not done:
- No real-key run yet, so the app has not shown a real model's reports, timings or cost. `outputs/sample/` is still empty, so a reviewer without a key sees "No saved runs found" until a real run is copied there (step 5).
- Saved runs recompute the score breakdown (see decisions); storing the ranking entries in `trace.json` would make old runs exact even after `make data` changes.
- A run cannot be cancelled from the page once started; Streamlit's own Stop button stops the script.
- `make check`, README, `docs/architecture.md`, the Docker check, the secrets check and the final review (steps 3, 5–10) are not part of this work.

## Subplan 6 — README, architecture doc, and commit
**Files:** `README.md` (rewritten), `docs/architecture.md` (new: request-flow and calculation diagrams, component notes, worked example `MA-2021-0195`, MCP client setup, design decisions), `docs/app_start.png` (start screen from the real app), `.gitignore` (assignment brief excluded).

**Checked (real output):**
- The app starts headless and opens in a browser with the defaults from `config.py`.
- Pressing Run without a key shows the missing-key message, and no run folder is created.
- README facts were re-checked against the code and data: adjacent sectors (Physician Groups 0.80, Behavioral Health 0.75, Home Health/Hospice 0.75, Dental 0.70), the default top 10, the sensitivity table, 176 tests.
- Secret scan over the repo (excluding `.venv`) found no keys.
- `uv run pytest -q` → `176 passed`.

**Still blocked on an OpenRouter key:**
- The first real run: cold and warm time, tokens, cost, verified count, distinct tool paths.
- `outputs/sample/`, then adding it to the Docker image.
- A screenshot of a finished run for the README.
- The README's measured-performance numbers.

**Still blocked on a Docker engine:** `docker build` / `docker run` check.

## Subplan 8 — Backend (events, trace, request parsing, follow-ups)

Backend only. The chat UI is a later change. `app.py` already prints `event["message"]`, so it was left as it is. `prompts/parse_request.md` and `prompts/followup.md` were already in the tree and are the prompts these calls use. README, `docs/` and the Dockerfile were not edited.

Files changed:
- `src/ma_engine/agent.py` — event records, shared step counter, `parse_request`, `answer_followup`
- `src/ma_engine/engine.py` — `run_engine` stores the events, `load_run`, `parse_target`, `run_followup`, `ranking.json`
- `src/ma_engine/clients/llm_client.py` — own retry loop, `attempts` on the result, `scrub_secret`
- `src/ma_engine/guardrails.py` — `check_statements`
- `src/ma_engine/models.py` — `FollowupResult` fields
- `tests/test_activity.py` (new), `tests/test_agent.py`, `tests/test_end_to_end.py`

### Public signatures and return shapes

`progress(event)` receives every event dict below. The CLI prints `event["message"]`.

`agent.research_buyer(entry, target, llm, mcp, use_cache=True, progress=None, steps=None)`
→ `(BuyerReport, evidence, buyer_trace)`. `steps` is the run's shared counter.

`agent.parse_request(text, llm, use_cache=True, progress=None)`
→ `(Target, defaulted: list[str])`. `defaulted` is Target field names filled from `config.DEFAULT_TARGET`, in field order. Events go to `progress` only.

`engine.parse_target(text, use_cache=True, progress=None)`
→ same `(Target, defaulted)`. Creates one `LLMClient`. Does not start a run. The UI shows the target, then calls `run_engine`.

`engine.run_engine(target, use_cache=True, excluded_keys=(), progress=None)`
→ `{run_id, run_dir, target: Target, ranking, reports, trace}`.
`ranking` is the `rank_acquirers` data (also `ranking.json`).
`trace["events"]` is the activity list. `trace["totals"]` is `{wall_time_s, llm_calls, mcp_tool_calls, tokens_in, tokens_out, cost_usd, cache_hits}`.

`engine.load_run(run_dir)`
→ the same keys. `ranking` is None if `ranking.json` is missing. `trace` is `{}` if `trace.json` is missing. Reports come from `guardrails.load_reports`. Saved activity is `trace["events"]`; it is not rebuilt.

`agent.answer_followup(question, run, llm, mcp, use_cache=True, progress=None)`
→ `FollowupResult`: `{question, statements: list[Statement], status: "verified"|"not_verified", issues, markdown, events, totals}`.
`markdown` is rendered by Python and contains `[MA-...]` tags. `events` is this answer's own list (its own step counter). `totals` has the same keys as `trace["totals"]`. `run` needs `ranking` and `reports` (as `run_engine` / `load_run` return).

`engine.run_followup(question, run, use_cache=True, progress=None)`
→ the same `FollowupResult`. One `LLMClient` and exactly one MCP session. Appends the answer to `followups.json` (a list) in `run["run_dir"]`.

`guardrails.check_statements(statements, evidence)` → `list[str]` (empty means pass). Citations, numbers, sample sizes, outcomes, deal types, names, wording, unknowns. Report structure and section-ownership rules are not applied.

### Event fields

Every event has all of these keys. A step is sent twice, `running` then `completed` or `failed`, with the same `step`.

`step`, `ts` (UTC, ends with `Z`), `category` (`llm` | `mcp_tool` | `validation` | `calculation` | `app`), `name`, `acquirer_key`, `round`, `status` (`running` | `completed` | `failed`), `duration_ms` (None while running), `input`, `output_summary`, `output`, `error`, `attempts`, `tokens_in`, `tokens_out`, `cost_usd`, `decision`, `message`.

`name` is a tool name, an LLM purpose (`research`, `write`, `repair`, `committee`, `parse_request`, `followup`), or `check`, `ranking`, `apply_committee`, `run`, `request`, `mcp_connected`, `substitution`, `buyer_failed`, `save`.

`input` for a tool is the arguments object passed to `MCPClient.call_tool`. For an LLM call it is `{model, message_count, tool_count}` only. `output` for a tool is the dict `call_tool` returned. For a check it is the issue messages. For an LLM call it is the visible assistant text, cut at 300 characters. Reasoning tokens are never stored.

`attempts` on an LLM call is a list of `{n, error}` in try order. `error` is None on the try that worked. Other steps have `attempts: null`.

`decision` is one or two factual sentences from what was recorded, for example "Requested 2 tools: get_acquirer_profile, get_valuation_comps", "No further tool calls; moved to writing", "Guardrails found 3 issues; sent one repair", "Stopped at round limit". It is null while the step is running.

A run emits, in order: `run` (app), `mcp_connected` (app, tool names), `rank_acquirers` (mcp_tool, exact arguments and full result) and `ranking` (calculation), then per buyer each LLM call and each tool call, then `check` (validation) and `repair` when needed, then `committee` (llm) and `apply_committee` (calculation), `substitution` (app) when a buyer is replaced, and `save` (app). `buyer_failed` is only when a buyer errors and no reserve is left. Nothing is emitted before the MCP session opens, so a server that dies at start still looks like a server failure to the current app.

### Decisions

- `config.MAX_RETRIES` (2) is the number of tries, not extra retries after the first. The OpenAI client's `max_retries` is 0. Retried errors are connection errors, timeouts, and HTTP 408, 409, 429 and 5xx, with a short doubling wait (0.2s, then 0.4s). A 400 or 401 is raised at once. The API key in an error string is replaced with `***`. Headers and environment values are not recorded.
- "regional" or "local" with no named region is forced to "Any region" in Python, including when the model says "Multi-Regional". A region named in the sentence wins. Unstated fields come from `config.DEFAULT_TARGET` and are listed in `defaulted`. The sentence is stored as data and is not executed.
- Parsing stays separate from the run. `parse_target` does not call `run_engine`.
- Follow-up evidence is the target block, the ranking entries and that answer's tool results. One repair, then `not_verified` with the issue messages. The six research tools only (`rank_acquirers` stays hidden).
- The per-buyer `tool_calls` and `llm_calls` lists are still in `trace["buyers"]`, because the current app reads them. The activity panel should read `trace["events"]`.

Commands and real output:
- `uv run pytest -q` → `189 passed in 36.83s` (176 before this work, plus 13 in `tests/test_activity.py`).

Open:
- The chat UI (activity timeline, parsed-target confirm, follow-up chat) is not built. It should call `parse_target`, then `run_engine`, then `run_followup`, and read `trace["events"]`.
- No live-model run of the new events yet, so token and cost fields are unmeasured on a real provider. The retry path was tested with a stubbed client, not OpenRouter.

## Subplan 8 — Chat UI and execution timeline

Display only. The screen calls `parse_target`, then `run_engine`, then `run_followup`, and reads `trace["events"]`. No ranking, model, or tool logic was added, and the backend signatures were not changed. `README.md` and `docs/` were left as they are.

Files changed:
- `src/ma_engine/app.py` — chat-first New Research page, results dashboard, follow-up chat, saved-run list, About
- `src/ma_engine/ui_trace.py` (new) — activity timeline: totals, filters, one row per step, 20,000-character preview
- `tests/test_app.py` — AppTest coverage for navigation, the example request, buyer details, follow-up, saved runs, the missing key, and the preview helper
- `.streamlit/config.toml` (new) — light theme, navy primary, headless, usage stats off
- `Dockerfile` — copies `.streamlit/config.toml` into the image
- `.dockerignore` — allows that theme file and still keeps `secrets.toml` out

`.gitignore` already ignores only `.streamlit/secrets.toml`, so `config.toml` is not excluded.

### Decisions

- Pages are a sidebar radio (New Research, Research Results, About), not a second Streamlit file. Saved runs from this session, `outputs/runs`, and `outputs/sample` open through `engine.load_run` and `guardrails.latest_run_dir`.
- Streamlit's chat box cannot be given a starting sentence. The assignment example is printed in full, and "Use this example" sends that exact text.
- While a run is going, the status box shows one line per buyer and the timeline shows `output_summary` only. After the step finishes, the row can be opened. JSON longer than 20,000 characters is previewed and the full text is a download. Rows are grouped by buyer so each buyer's rounds stay in order, 50 rows to a page.
- The "MCP server: N tools" line is filled from the `mcp_connected` event's `output_summary` (7 tools with this server). The API key is never written; error text replaces it with `***`.
- A missing key stops a new request and explains `copy .env.example to .env`. Opening a saved run does not need a key.

Commands and real output:
- `uv run pytest -q` → `194 passed in 35.61s`.
- `curl -fsS http://127.0.0.1:8599/_stcore/health` → `ok`, then the server was stopped. `pgrep -fl streamlit` found no process.

Follow-up after a browser check:
- Dollar amounts are escaped only when the screen draws markdown, captions, write, and expander labels (`as_markdown` in `ui_trace.py`). `reports.md` is still plain text from `guardrails.render_markdown`.
- Timeline rows use Streamlit badges (`:blue-badge[LLM]`, `:green-badge[MCP tool]`, `:orange-badge[Validation]`, `:gray-badge[Calculation]`, `:primary-badge[App]`). Failed is a red badge, running shimmers, and empty buyer or round fields are left out.
- The sidebar starts expanded.
- `run_engine(..., request=None)` stores the user's sentence on `trace["request"]` and on the first `run` event input. The timeline shows that sentence as Question, or a description of the target when the sentence was not stored.

Open:
- No live-model click-through of this screen yet, so token and cost on a real provider are still unmeasured.
- `README.md` and `docs/architecture.md` still describe the old form. This task left them unchanged.

## Subplan 9 — Backend

Backend only (Part 1, steps 1a–1g). The chat screen was not rebuilt. `app.py` got three small guards so a buyer with no report body does not crash the current page. README and `docs/` were not edited. The Streamlit process on port 8501 was left running.

Files changed:
- `src/ma_engine/clients/data_client.py` — statistic ids and `stat_record` / `key_metrics` / `note_ranking`
- `src/ma_engine/mcp_server/tools.py` — `stat_id` on profile, sector, and comps fields
- `src/ma_engine/ranking.py` — `ST-RANK-` ids on signals and the three count fields
- `src/ma_engine/models.py` — `ReportDraft.summary`; report status `failed`; `error`; `draft` may be null
- `src/ma_engine/guardrails.py` — statistic checks, percentile labels, target numbers in unknowns, `sources.json`, narrative `reports.md`
- `src/ma_engine/clients/llm_client.py` — caller chooses `max_tokens`; records `finish_reason` and a cut-off flag
- `src/ma_engine/agent.py` — token caps, cut-off repair, research stages
- `src/ma_engine/engine.py` — no silent replacement; writes `sources.json`; follow-ups extend it
- `src/ma_engine/prompts/research_writer.md`, `repair.md`, `followup.md`
- `src/ma_engine/app.py` — null-draft guards only
- `tests/test_narrative.py` (new) plus updates in the existing tests

### Public contract for the UI

`get_client().stat_record(stat_id) -> dict | None`

`{stat_id, calculation, filters, n, value, display, source_ids}`

Ids are stable:
- `ST-COMPS-<sector>-<scope>-<band>-<metric>-<stat>` for example `ST-COMPS-healthcare-services-sector_only-100-400-ev_ebitda-median`
- `ST-SECTOR-<sector>-<field>`
- `ST-PROFILE-<buyer>-<field>` (underscores in the field become hyphens)
- `ST-RANK-<buyer>-<signal>` for example `ST-RANK-blackstone-size_fit`

`ST-RANK-` records depend on the target. `note_ranking(ranking_data)` loads them from `ranking.json`. `quality_metrics(reports, ranking)` and `python -m ma_engine.guardrails` do that load, so those ids are valid citations.

`get_client().key_metrics(acquirer_key)` is the closed-deal card, in this order, each a full record. The same list is on `get_acquirer_profile` as `data["key_metrics"]`:

1. closed count
2. median, smallest, and largest closed deal size
3. median EV/EBITDA and median EV/Revenue on closed deals
4. private-target count
5. median bidders and median days to close

`guardrails.build_sources(reports, ranking=None)` returns:

```text
{"numbers": {"MA-2021-0195": 1, "ST-COMPS-...": 2},
 "entries": [{"number": 1, "id": "MA-2021-0195", "kind": "deal",
              "record": {<full processed CSV row>}},
             {"number": 2, "id": "ST-...", "kind": "statistic",
              "record": {stat_id, calculation, filters, n, value, display, source_ids}}]}
```

Numbers follow first appearance: summaries in rank order, then each report's sections (overview, fit, precedents, valuation, risks, conviction). An id with no record gets no number.

`guardrails.extend_sources(sources, statements, ranking=None)` is what a follow-up uses. Numbers already assigned stay. New ids are appended after the highest number. A follow-up does not start again at 1.

`engine.load_run(run_dir)` adds `sources` (the `sources.json` object, or None). `engine.run_followup` extends that file and renders the answer with `[n]`.

`ReportDraft.summary` is one `Statement`, checked like the others. Sections stay lists of statements. `reports.md` joins them into paragraphs.

`BuyerReport.status` is `verified`, `not_verified`, or `failed`. A failed buyer keeps its rank. `draft` is null, `error` is the message, and `issues` is `[error]`. There is no replacement buyer. `trace["substitutions"]` is always `[]`. `trace["errors"]` still holds the partial trace. `quality_metrics` `report_count` counts reports that have a body. `failed` lists the buyers with status `failed`.

`agent.RESEARCH_STAGES` groups events for the progress card. Match `(category, name)`:

| Stage id | Label | Events |
|---|---|---|
| `identify` | Identify relevant sectors and buyers | `calculation`/`ranking`, `mcp_tool`/`rank_acquirers` |
| `investigate` | Investigate buyer transaction histories | `mcp_tool` / `get_acquirer_profile`, `get_acquirer_deals`, `search_deals`, `get_sector_overview`, `get_dataset_overview` |
| `comps` | Calculate valuation comparisons | `mcp_tool` / `get_valuation_comps` |
| `reports` | Generate and validate 10 reports | `llm`/`write`, `llm`/`repair`, `validation`/`check` |

Research rounds use 1,000 output tokens and should reply `DONE`. Write and repair use 8,000. A cut-off (`finish_reason` `length`, or JSON that stops early) is repaired once with `Your output was cut off; write shorter statements`. Visible model text in the trace is still cut at 300 characters.

### Decisions

- A buyer whose research raises stays in its ranked place with status `failed`. Reserves are not swapped in. This replaces the substitution rule from Subplan 7.
- A money, multiple, or percent that equals a statistic display must cite that `ST-` id. A figure that equals a cited deal's own field does not. Citing a ranking signal also covers the numbers in its explanation, and a cited statistic's `n` may be quoted as that integer.
- `p25`, `p50`, `p75`, `p90`, `25th percentile`, and the words `median` and `interquartile` are not numbers. An `unknown` may quote figures from the target block, such as `$200.0M`.
- `reports.md` is the narrative document (executive summary, shortlist, six sections as paragraphs with `[n]`, verification, Sources appendix). It is built only from the checked statements.

Commands and real output:
- `uv run pytest -q` → `207 passed in 43.92s`.

Real runs, default target, model `anthropic/claude-haiku-5.5`. Two runs, then no more.

First run `outputs/runs/20261031-222316`, before ranking-explanation numbers were accepted as part of the cited signal:
- Wall time 54.37 s. Cost $0.1317. Tokens 869,190 in / 89,522 out. LLM calls 42.
- Verified 4, not verified 6, failed 0.

That run's common miss was a ranking sentence such as "within 2x" or "14 of 14" rejected after the writer had cited the `ST-RANK-` id. The checker only trusted the score display, not the explanation. The fix treats the explanation and the statistic's `n` as part of the cited statistic. Tests use the real `p25`/`p75` sentence and the real unknown that quotes `$200.0M`.

Second run `outputs/runs/20261031-222706` (the measurement):
- Wall time 50.28 s (under the 60 s target). Ranking 269 ms. Research 41.4 s.
- Cost $0.1433. Tokens 992,332 in / 88,108 out. LLM calls 43. Cache hits 0.
- Verified 6, not verified 3, failed 1. No buyer was replaced.
- `sources.json`: 201 entries (156 statistics, 45 deals).
- `uv run python -m ma_engine.guardrails` on that folder:

```text
Run 20261031-222706: 9 reports
Failed buyers: 1 ['gtcr']
Invalid citations: 0
Reports not verified: 3 ['new mountain capital', 'bain capital', 'francisco partners']
Fact statements with citations: 165 of 165 (100.0%)
Conviction mix: High 3, Medium 3, Low 3
Buyer-type mix: Financial Sponsor 7, Strategic 3
Max strategic-fit similarity: 0.38 (threshold 0.5); pairs above: none
Result: FAIL (9 of 10 reports; 1 failed; 3 report(s) not verified)
```

Remaining issues in that run are the model breaking rules that stayed in force, not the old false positives:
- GTCR failed: the repair returned 5 risk flags; the format allows 4. The buyer stayed at rank 7 with status `failed` and the error on the report.
- New Mountain Capital: a Withdrawn deal id written in the text, and the word "closed" on that deal; one count (`14`) not tied to a cited statistic.
- Bain Capital: "Acquisition of" on a Pending deal.
- Francisco Partners: "completed" on a Rumored deal; an unknown that quotes `2024`, which is not a target figure.

Open:
- The screen that renders the narrative, the `[n]` links, the Key metrics card, and the four progress stages is not built. It should read `load_run`, `sources`, `key_metrics`, and `RESEARCH_STAGES`, and it should not call the engine when the user switches buyers.
- `README.md` and `docs/` still describe the older report.
- The saved `trace["quality"]` in `20261031-222706` was written before `make check` learned to load `ranking.json`, so that file still lists `ST-RANK-` ids as invalid. `python -m ma_engine.guardrails` on the same folder reports 0 invalid citations. That command is the one to trust.

## Subplan 9 — UI

The chat screen was rebuilt around the owner's layout. The product name on the page is "M&A Acquirer Research Assistant". No firm name. Numbers and sentences on the page come from the loaded run. The placeholder text in the mock-up was not copied.

Files changed:
- `src/ma_engine/app.py` — three-part screen: navy sidebar, middle column (request, short answer, progress, top 10, follow-up), right panel (five tabs and Export Report)
- `src/ma_engine/ui_report.py` — new. Turns a saved run into the report HTML, source cards, key-metrics card, and the four progress stages
- `.streamlit/config.toml` — light main theme and a `[theme.sidebar]` navy section (Streamlit 1.66)
- `tests/test_app.py` — the new views, citation anchors, a failed buyer left in place, and a switch that must not call the engine

### Decisions

- The Report tab opens on all 10 buyers (summary, shortlist, jump buttons, six sections each, Sources, Diagnostics). A Top 10 row or a jump button opens that one buyer. Previous and Next only change session state.
- `[n]` is an in-page link (`href="#src-n"`) drawn with `st.markdown(unsafe_allow_html=True)`. A full reload does not happen. In that HTML, `$` is written as `&#36;` so Streamlit does not draw math and the page does not show a backslash. Ordinary markdown still goes through `ui_trace.as_markdown`.
- Key metrics are `get_client().key_metrics` display strings, each next to its `stat_id`. A `[n]` is added only when that id already has a number in the run's sources.
- A deal card prints the stored CSV fields. Money and multiples use the same `fmt_money` / `fmt_multiple` strings the tools already use. No new statistic is computed.
- A stage's time is the clock span of the events `RESEARCH_STAGES` lists for it (first start to last finish), so parallel buyers are not added together. If an event has no timestamp, the recorded durations are added instead.
- A failed buyer stays at its rank. The page shows one short warning. The error text is only inside Diagnostics.
- `run_engine` does not put `sources` on the dict it returns. The screen reads `sources.json` when it opens the run, and again after a follow-up.

Commands and real output:
- `uv run pytest -q` → `209 passed in 44.17s`.

Checked in the browser on the saved run `outputs/runs/20261031-222706` (no new model run). The app was restarted with `make run` and left running at http://localhost:8501.

- All-buyers report: executive summary, conviction mix (High 3, Medium 3, Low 3), 6 verified, 3 not verified, 1 failed, prose with blue `[n]` links. Sidebar shows Healthcare Services, size $200.0M, margin Strong, 10 buyers.
- Clicking `[1]` changed the address to `#src-1` and landed on the source card for `ST-RANK-blackstone-score` (calculation, filters, n, value 0.66). Deal cards in the same list show values such as `$341.7M`.
- Single-buyer view for the first buyer: back link, High pill separate from a Verified pill, score 0.66 (rank 1), section tabs, prose, and the Key metrics card (for example median closed deal size `$341.8M` with its stat id).
- Agent Activity for that buyer shows the stored totals (50.28 s, 43 LLM calls, 34 tool calls) and the real steps.
- Screenshots: `/tmp/all-buyers-top.png`, `/tmp/citation-source.png`, `/tmp/single-buyer.png`, `/tmp/agent-activity.png`.

Open:
- `README.md` and `docs/` still describe the older report.
- The saved `trace["quality"]` in `20261031-222706` is still the stale file from before `make check` loaded `ranking.json`. Trust `python -m ma_engine.guardrails` on that folder.
- At a window about 1180 pixels wide, the last tab (Agent Activity) and section tabs 5 and 6 sit past a small scroll arrow. They are on the page.
- Research rounds are not one of the four `RESEARCH_STAGES` groups, so their time is only in the stored wall time (50.28 s). On this run the stage lines are 268 ms, 2.6 s, 4.1 s and 39.1 s.
- A statistic with no sample size shows `n` as a dash. The blended score is one of those.

## UI redesign — progress, edit target, layout, report panel, citations, writing prompt — 2026-11-01

Owner requests after Subplan 9: progress placement and two-level progress, an Edit target control, then Prompts 1–5 (layout, report panel, citations, report writing, sidebar reflow). No change to ranking, tools, the MCP server or the guardrail checks.

Files changed:
- `src/ma_engine/app.py` — live `st.status` with four stages and a one-line note; completion line ("Research complete · 46.71s · 10 buyers identified · 10 reports generated", plus "· k not verified" / "· k failed"); "View technical activity" grouped by run and buyer; Edit target form (Save shows "Run research with updated target", Cancel changes nothing); compact sidebar (New Research, Recent research, All research, About); chat column centred at a reading width with its own scroll area and the chat box under it; buyer cards; report panel with one tab row; Diagnostics per buyer. Removed: forced sidebar width, run card, Saved Reports page, citation chips, citation table, Sources list, source-view box, section sub-tabs.
- `src/ma_engine/ui_report.py` — `[n]` is now a `<details>` source card (deal fields; or statistic calculation, value, filters, `n`, the transactions used with "Show all", stat id under Technical details); continuous six-section report document; metric strip (relevant closed deals, closed deals in the target sector, within 2x of the target's size, median closed deal size, median EV/EBITDA).
- `src/ma_engine/agent.py` — `report_summaries` handles a failed report (follow-up crash fix); each research stage has a note; `buyer_failed` marks the reports stage failed.
- `src/ma_engine/ui_trace.py` — `step_detail_md`, `activity_groups`, `activity_md`.
- `src/ma_engine/prompts/research_writer.md` — new "How it reads" section: conclusion first, one paragraph per section, no repeated figures, audible interpretation/unknown wording, history not intent, buyer-specific risks, the exact title "Deals that did not close" with outcome-only wording.
- `tests/test_app.py`, `tests/test_activity.py`, `tests/test_narrative.py` — updated for the above.
- `README.md`, `docs/architecture.md` — screen description.

Root cause of the sidebar reflow bug: `PAGE_CSS` forced `[data-testid="stSidebar"] { width: 252px !important; min-width: 252px !important }`. Streamlit 1.66 animates and resizes the sidebar itself (it has a drag handle), so the forced width kept the space after collapse. The chat was also centred with fixed spacer columns. Fix: delete the forced width; centre a keyed chat container with `max-width` and `margin: 0 auto`. Fixed heights on `st.container` keys need `flex: none`, because Streamlit gives every block `flex: 1 1 0%`, which overrides `height`.

Decisions:
- Citations open in place with HTML only (no rerun, the report does not move). `name="cite"` keeps one open at a time. The card is `position: fixed` at the bottom right. Inside paragraphs the HTML uses `<div class="para">`, because a `<details>` inside `<p>` ends the paragraph. Chrome's `::details-content` is set to inline so `[1][2][3]` stay on one line, and a closed card is hidden with `display: none`.
- A metric figure that the report did not cite shows a small ⓘ source card built from the ranking entry or the buyer profile record. It is not given a number.
- Interpretation is shown with a dotted underline and a hover label instead of the word "Interpretation:"; unknowns start "Not in the dataset:". `reports.md` keeps the written labels.
- The statistic's transaction list comes from `get_client().deal_record(id)` for ids not in the run registry. Ranking figures are read from the run's own `ranking.json`, never from the shared client, which may hold another run's ranking.

Commands and real output:
- `uv run pytest -q` → `212 passed in 47.93s`.
- Layout measured in the browser (saved run `20261031-235342`), sidebar open/collapsed × panel open/closed, at 1280, 1440 and 1600 px, and while resizing 1600→1360→1280 with the panel open: horizontal overflow 0 and vertical page overflow 0 in every state; the chat box spans exactly the chat column (for example 1440 collapsed, panel closed: chat 304–1136, centre 720; 1280 sidebar open, panel open: chat 276–696, panel 728–1256).
- Buyers 1–4 in the panel: six headings each (Acquirer Overview, Strategic Fit Thesis, Precedent Activity, Valuation Context, Risk Flags, Conviction), 37–49 clickable citations each, 0 unregistered ids, five metric cells, Diagnostics present; New Mountain Capital shows Not verified and "3 checks did not pass — see Diagnostics." The selected tab (Valuation & Risk) stayed selected when switching buyers.
- Citations checked: a transaction (`[3]`, MA-2021-0407: target, buyer, sector, $341.7M, 2021 Q2, Closed, 10.0x, 1.5x, id), a statistic (`[56]`, median EV/EBITDA 17.2x, n = 22, 22 transactions, Show all), a ranking score (`[1]`, 0.66, n —, pointer to Score Breakdown). Opening a second `[n]` closed the first; chat scroll moved 0 px.
- Writing prompt, real model runs on the default target (tool results cached, write calls new):
  - Before (old prompt) `20261031-235342`: 8 verified, 2 not verified.
  - Draft 1 `20261101-002715`: 4 verified, 6 not verified (11 completion-word issues: titles such as "Non-closed deals").
  - Draft 2 `20261101-002902`: 5 verified, 5 not verified (10 completion-word issues).
  - Final `20261101-003055`: 10 verified, 0 not verified, $0.116915, 46.71 s.
  - Citation coverage (fact and interpretation statements with at least one id) is 100% for every buyer before and after. Blackstone: 22→19 statements, 441→431 words, 32→27 unique ids, 2→4 interpretations. The old Blackstone precedent paragraph wrote transaction ids inside the text; the new one names the deals.

Open:
- One run per prompt version. Model output varies between runs, so 10/10 is one sample, not a rate.
- New Blackstone conviction says "the strongest fit in the list": a comparison the checker does not test.
- `direct_closed_count`'s registry record lists the relevant closed deals (7) as its transactions while n is 2; that is how `note_ranking` stores it.
- Filters show the acquirer key in lower case ("Buyer: blackstone").
- The Streamlit screenshot tool sometimes captures a stale frame or a collapsed sidebar; layout figures above come from DOM measurements.

## UI — shortlist cards, chat sizing, one Agent activity row per response — 2026-11-01

Owner requests: (1) replace the cramped top-10 table with compact cards and let the chat use the space; (2) give each research answer and each follow-up one "Agent activity" section, reusing the recorded trace. No change to the backend, ranking, FastMCP tools, prompts, report generation, validation or citations.

Causes found in the edited version:
- Shortlist: the five fixed-ratio columns were too narrow beside the panel ("Ra/nk", "Financial/Sponsor" spilling into the next row, "New Mountain C…", the Not verified pill past the table edge). The owner's screenshot of overflowing two-column cards came from an earlier card version no longer in the tree; it was not traced in this code.
- Chat: a 52rem cap; a fixed `calc(100dvh - 9.75rem)` that assumed the chat box height; viewport-width rules; and `chat-beside { display: none }` under 640px, which hid the only chat box when the panel was open.
- Activity: research drew a live status plus a separate "View activity" log (Markdown dump, 420px scroller) that did not collapse with it; the finished view repeated the log in per-buyer tabs; follow-ups drew a live status and then a second "Follow-up activity" expander. `_show_output` scrubbed the text but rendered the unscrubbed object for small outputs. Updating `st.status` on every event collapsed it (or, with `expanded=True`, forced it open), and `with status:` marks a status complete when the block ends.

Files changed:
- `src/ma_engine/app.py` — shortlist: one `st.button` per buyer in a keyed grid, two columns unless the shortlist itself is narrower than 34rem (container query); selected card keyed `card-sel-…`. Chat frame: full width, one viewport tall, scroll area takes what the chat box leaves (`flex: 1 1 0; min-height: 0`); below 900px everything flows and the page scrolls. Activity: `render_activity` (one collapsed row) for research, follow-ups, a stopped research run (kept in the message history) and a stopped follow-up (kept in `stopped_followups` for the session). Live runs update only the steps and a caption under the status; the status is updated once, at the end.
- `src/ma_engine/ui_trace.py` — one step renderer (`show_step`) shared by chat, live view and buyer panel: exact tool name, kind (MCP, LLM, Validation, Calculation, App), status, buyer, round, duration; "Parameters sent" for MCP, "Request details" for LLM; "Raw tool response" (the app-recorded result), "Visible text preview" (first 300 characters only), "Issues found"; reused MCP results and cached LLM responses labelled; a step left running when a response ended shows "Not finished"; truncated previews labelled, full JSON download (`on_click="ignore"`); scrub applied to inputs, outputs, errors and downloads. `LiveSteps` gives each step one slot so `completed` replaces `running`. `activity_label` uses the recorded wall time and says "No new MCP calls" when a follow-up recorded none. Removed `activity_md`, `step_detail_md`, `live_timeline_md`, `buyer_status_lines`, `row_label`, `activity_groups`.
- `tests/test_app.py` — new assertions for the cards, one activity row per response, step titles, merging, redaction in every output branch, separate identities for equal step numbers, two follow-ups plus a stopped one, and read-only filtering.

Commands and real output:
- `uv run pytest -q` → `215 passed in 49.48s`.
- Browser, saved run `20261101-005443` (178 events, 89 steps, one follow-up with 0 MCP calls): completed research shows "Agent activity · Completed · 45.09s" collapsed above the Executive Summary; the follow-up shows "Agent activity · Completed · 9.8s · No new MCP calls" above its answer; `rank_acquirers` shows its parameters and a truncated preview ("first 20,000 of 119,381 characters") with a download. 1280px panel open: chat 603px, chat box 16px below the scroll area, page overflow 0 both ways. 390px panel open: one-column shortlist, chat box after the content, horizontal overflow 0.
- Running and failed states were checked with a temporary replay page (not in the repo) that feeds the saved events through `LiveSteps`: 61 events gave 44 rows (44 distinct steps, 27 running); the user's open/closed choice held across later events; at completion the status collapsed once to the final label; on failure 17 completed and 27 "Not finished" rows, no "Running".

Open:
- Running and failed states were verified with replayed events, not a live research run (no paid calls).
- A stopped research run started from Edit target is kept in the message history, which is drawn on the compose page only.
- Live view shows at most 50 steps while running; all are listed (paged) when it finishes.
- LLM steps record metadata and a 300-character visible preview only; full model responses are not recorded.

## Banker-facing report and citation review — 2026-11-01

Implemented the owner's approved review on top of the existing UI changes. Baselines for the six main edited files are in ignored `work/review-baseline/`; previous local edits were preserved.

Changes:
- `ranking.py`: production shortlist/reserves now follow score order; the old three-per-type policy remains a sensitivity scenario. Strategic alternatives are listed separately. Counts now carry their exact contributing closed-deal IDs.
- `clients/data_client.py`: ranking statistic registry uses those exact IDs and records buyer, sector/relevant sectors, closed-only and size-band filters. No model calculations.
- `guardrails.py`: sample-only prose binds n to its explicitly cited statistic instead of a broader tool sample; an adjacent-only claim with direct-sector evidence is rejected. Markdown executive summary shows the first three ranks with verification labels; offline sources remain available.
- `prompts/research_writer.md`: evidence-led summary/conviction, concise precedents and comps, no raw IDs or scores in headline prose, no unsupported inference from timing, bidders, financing or platform history. All six sections and at least two risks retained.
- `ui_report.py`: rank-preserving executive comparison, evidence counts on buyer cards, verification separated from attractiveness. Standardized transaction/statistic previews, explicit missing data, supporting risk fields, technical IDs in disclosure. Legacy count citations with broader evidence are labeled honestly, not silently recalculated. Stored report text/statuses unchanged.
- `app.py`: saved run/buyer/tab URLs restore existing results without MCP or LLM calls. Invalid/deleted links show a safe notice. Strategic alternatives use a collapsed section. Existing sidebar, report controls and activity UI retained.
- Documentation and tests updated for score-order policy, exact evidence, sample-size regression, verification/rank separation and URL restoration.

Browser evidence:
- Browser plugin not available; Playwright used. Initial localhost:8501 check caught stale imported helpers in the running Streamlit process; restart resolved it.
- Saved run `20261101-014527`, desktop 1440×1000: title/content render, no framework error, zero horizontal overflow. Score Breakdown changes URL to `tab=score`; browser Back restores Report.
- Continued in an isolated browser context on localhost:8502 to avoid interfering with the owner's open tab. Citation [2] opens on hover at 380×320, scrolls internally, and closes after moving away. Its legacy broader-evidence warning and unchanged count were inspected.
- 390×844: meaningful content, zero horizontal overflow, zero framework exceptions. Browser console: zero errors/warnings.
- No new paid LLM run; historical prose remains historical. New generation quality still needs the owner's next research run. Automated tests use mocks.

Final validation: `.venv/bin/pytest -q` → **239 passed in 62.66s**. `git diff --check` and `.venv/bin/python -m compileall -q src/ma_engine` passed. No saved research outputs were regenerated or deleted.

## Expandable research stages — 2026-11-01

Owner approved the stage-first activity layout. `ui_trace.py` now partitions each recorded step into one research stage (ranking, buyer research, valuation, writing/checking) or the technical event log. Completed activity retains one outer expander, buyer filter and run totals; each stage expands to its actual calls, readable parameters, Input JSON and recorded output. Valuation summaries quote stored median/sample displays. Final report counts use saved verification statuses; earlier repaired checks are distinguished from unresolved latest checks. Warnings/failures label the stage even while collapsed. LLM outputs retain the honest 300-character preview label. Cached responses are counted and labeled; no research is replayed by viewing activity.

`app.py` uses the same grouping for live progress through `LiveSteps`, updating each call in place. `agent.py` stage metadata includes tool-choice/committee events and renames the valuation stage to Retrieve valuation comparisons. Follow-ups retain their own separate activity. No backend research logic or stored outputs changed.

Tests cover step partitioning, running/completed deduplication, repaired versus unresolved checks, existing redaction and read-only saved-run interaction. Browser: isolated Playwright session on localhost:8502, saved run 20261101-014527; opened activity → valuation stage → Blackstone tool → raw response. Verified actual parameters, stored 12.1x (n=10), visible JSON, zero app exceptions and zero horizontal overflow; console clean. Existing saved runs work without regeneration. Live generation exercised with mocked LLM/MCP in the app tests, not paid calls.

Final checks: `.venv/bin/pytest -q` → **241 passed in 66.42s**; compileall and `git diff --check` passed. Narrow 390×844 activity/raw-output view also had zero horizontal overflow and no app exceptions. Main app restarted on port 8501 with the updated modules; temporary port 8502 server stopped.

## Follow-up response totals — 2026-11-01

The owner identified missing usage totals for the second answer in run 20261101-125440. Its followups.json already records 4.13s, 2 LLM calls, 0 MCP calls, 68,930 input tokens, 527 output tokens, cost $0.007157 and 0 cache hits. The activity renderer gated totals behind the original-research-only run_totals flag.

Added an explicit Response totals expander for each saved follow-up and immediately after a live follow-up completes. It reuses render_totals and labels the scope as this response only, excluding the original run and other answers. Missing saved totals retain the existing unavailable message. No new model/tool calls, recalculation or saved-file changes. Added regression assertions for separate response totals and costs across two answers.

Validation: tests/test_app.py → 31 passed, 1 skipped in 20.11s; git diff --check passed. Targeted follow-up test rerun after adding immediate live-completion totals.


## Full question checklist audit — 2026-11-01

Exercised all 75 cases in ma_agent_test_questions.md: 54 live follow-ups through the real engine/MCP/LLM, 16 core/input cases including four complete research runs, and five isolated mocked failure simulations. Baseline follow-ups were appended to requested saved chat 20261101-125440; original four answers/sources backed up first. Revised-context retests were isolated under work/question-audit/retest-run and final-run, preserving original history and citation numbering. That saved run is $150M, not $200M.

Changes: added followup_context.py to expose separately cited original run metadata, exact source-number mappings, selected full reports, recent conversation references and original evidence; engine loads saved follow-up context. Added ST-RUN source handling and safe rendering. Fixed integer source_ids counts in recorded activity crashing the evidence walker, and structural buyer-count context. Request parsing rejects unsupported explicit constraints and maps average margins to no strong-margin preference. Tool results distinguish complete versus truncated row lists. Writer/follow-up instructions reinforced scope, current-activity uncertainty, outcomes and statistics citations. app.render_activity now skips hidden timeline work until the expander is opened; tests verify saved activity opening never replays the engine.

Independent CSV checks: 500 transactions through 2024; Blackstone 22 closed transactions; ten closed HCS transactions in $100M–$400M, all with EBITDA/revenue multiples; displayed medians 12.1x and 2.9x; Evergreen Care Solutions $299.0M / 14.6x. Detailed evidence and all question verdicts: work/question-audit/AUDIT.md, review-verdicts.json, independent-truth.json and accompanying raw JSONL/events.

Review outcome: 43 pass, 9 partial, 8 fail, 7 limited/unsupported, 3 defaulted-input cases, 5 passing failure simulations. Do NOT equate model Verified status with answer completeness. Latest core runs 20261101-131650, 131807, 131929, 132033 have 9/10, 9/10, 8/10, 8/10 verified reports. Remaining defects include report truncation/schema/citation failures, adjacent-sector coverage omitting buyers, withdrawn follow-up losing filter scope, unsupported exclusion promises, and citation [3] explanation misbinding. These were not hidden or made to pass by relaxing checks. This is not an assessment-readiness sign-off.

Validation: .venv/bin/pytest -q => 246 passed, 1 skipped in 64.24s; git diff --check passed. Previous Playwright checks on localhost:8501 verified saved-run load, Next buyer URL/report update and opening stage activity/totals, with no app exceptions or error-console messages. At owner request retried the visible in-app browser; connection returned browser.user is unavailable. No further in-app browser interaction, final responsive QA or download-delivery check claimed. Server remains on 8501 with updated modules. No commits made; pre-existing changes preserved.

## Checklist remediation and selectable-model compatibility — 2026-11-01

Implemented capability-based OpenRouter request adaptation: tool-support preflight, native structured output or locally validated JSON fallback, advertised output caps, unified reasoning/on-off handling, and provider Retry-After cooldowns. Selected-model identity remains in cache keys; truncated responses are not cached. Research can continue after truncation within the existing round bound. A single repair can replace failing sections while preserving valid sections; the complete merged report still undergoes all checks. No guardrail checks were relaxed in this remediation.

Follow-up improvements retain prior tool filters, distinguish user-supplied comparison targets from saved targets, add computed coverage checks, expose source membership and catalogs, and handle read-only source/download UI questions from actual saved state. Thirteen affected follow-up cases now have verified, reviewed targeted results; raw artifacts are refined.jsonl, actions.jsonl and comparison.jsonl under work/question-audit. Four fresh complete research scenarios were rerun with the selected nvidia/nemotron-3.5-super-vl:free model; see REMEDIATION.md and capability-core.jsonl for actual results. Full-report citation, sample-size and wording failures remain visible. This is not an all-pass or assessment-readiness sign-off, and model interchangeability does not guarantee equal answer quality. Non-tool-capable models cannot run this tool-dependent workflow.

Validation: full automated suite 268 passed, 1 skipped in 69.45s; a subsequent additional capability-order test plus agent suite gives 36 passed. Source/tests/README whitespace checks pass. Server restarted on 8501 and health endpoint returns ok. In-app browser retrieval again returned browser.user is unavailable; no fresh browser QA or Playwright fallback claimed. Original history and pre-existing working-tree edits preserved; no commits.

## Sidebar session list — 2026-11-01

Removed the six-session cap from the sidebar; all saved sessions now render newest first in the existing scrollable list. Removed the short-viewport override that made the whole sidebar scroll, preserving the fixed header and History/About footer. Validation: existing sidebar navigation AppTest passed; source whitespace check passed. No browser visual check claimed.

## Follow-up length rejection — 2026-11-01

Removed the arbitrary six-statement FollowupAnswer schema cap and changed the prompt to prioritize a complete answer with concise prose. Longer answers now parse without truncating or dropping claims; every statement still undergoes the unchanged evidence checks. Fixed the UI fallback that duplicated the Not verified status and diagnostics when no structured answer was available. Existing saved failures are preserved; retrying uses the updated schema and prompt. Regression checks cover ten-statement acceptance, rejection of an unsupported source within that longer answer, and a single failure status in the UI. Validation: 44 passed, 1 skipped across follow-up quality and app tests; scoped whitespace check passed.

## Preserve submitted questions across refresh — 2026-11-01

Found that initial questions existed only in Streamlit state until research completed, and follow-ups were appended only after their answer completed. Added a small submissions journal under outputs/runs/_submissions, with one atomically replaced JSON file per request. Initial questions are saved before parsing and receive a History URL while processing; follow-ups are saved before client construction/network work. Completion hides the journal entry only after the existing result-save path succeeds. Errors/cancellation preserve the question as unfinished, without claiming a worker remains alive or automatically rerunning calls.

Unfinished new questions appear in the sidebar and History. Unfinished follow-ups appear in their saved research chat and History. Deleted research runs do not reappear through their follow-up journal entries. This does not introduce a background worker or promise that generation continues after refresh. Previously unsaved text cannot be recreated by this change.

Targeted validation: 37 passed, 1 skipped for persistence and app tests; two subsequent fresh-session/sidebar checks passed. The missing-key test now expects a saved submitted question but no research report, matching the new persistence contract. No evidence or answer checks changed.

Full regression suite after the persistence changes: 275 passed, 1 skipped in 67.19s. Scoped whitespace checks passed. No new live model calls or browser visual verification claimed.

## Startup output clarity — 2026-11-01

Added watchdog to project dependencies and lockfile, removing Streamlit's optional macOS file-watcher installation hint. make run now explains the existing on-demand stdio MCP connection and that it has no HTTP /mcp endpoint. Verified actual make run startup without the hint, then make stop; left the app stopped as it was before this check. No MCP transport change.

## OpenAI strict-schema compatibility and failed-run citations — 2026-11-01

Investigated saved run 20261101-152939 with openai/gpt-6.1-terra. Actual provider probes identified two HTTP 400 causes: description beside a $ref in ReportDraft.summary, and missing defaulted include_source_catalog in FollowupAnswer.required. The strict-schema adapter now inlines referenced fields carrying sibling metadata and lists all object properties as required. Local schemas/defaults and evidence checks remain unchanged. Both request forms were accepted after the fix; follow-up parsed successfully. A targeted Blackstone run reused successful cached research and made a fresh writing call: verified with no issues or repair. No other model configuration, ranking or prompt changes.

The failed run's sources registry was empty because it was built solely from report drafts while UI summaries still cited ranking statistics. Added append-only source completion for the displayed buyers' three ranking counts at save/load. Backed up and repaired that run's source registry (30 entries); raw bracketed ST-RANK fallback no longer appears in the rendered summary. Failed historical report statuses/narratives were preserved. Details and isolated live results: work/model-compatibility/FINDINGS.md and blackstone-result.json.

Validation: 278 passed, 1 skipped in the complete automated suite; scoped whitespace checks passed. No new browser visual verification claimed. One-buyer live success is not an all-model or full-ten-buyer guarantee.

## Application terminal logs and Claude compatibility check — 2026-11-01

Added a dedicated INFO terminal logger for existing activity events: start/end, model, buyer, tool name, timing, usage/cost/cache metadata and failures. Logs do not include input prompts or response payloads; configured API key is redacted and newline injection is removed. Provider nested error.message is now extracted into safe error text so schema rejection details appear in activity and terminal logs. Retry delay/attempt messages are logged as they happen. No research, scoring or verification behavior changed.

Validation: activity/agent/logging suites 67 passed; final retry/redaction checks 3 passed. A minimal live structured follow-up request to anthropic/claude-haiku-5.5 was accepted and parsed successfully. Earlier same-adapter OpenAI gpt-6.1-terra Blackstone test produced a verified report. These establish tested request compatibility, not identical financial-answer quality across models or support for non-tool models. README documents terminal logging. No app restart or new full research run performed.

## Delete unfinished requests — 2026-11-01

Added the same sidebar trash-button layout and confirmation dialog to unfinished request entries, plus a delete action in History. Cancel preserves the request; confirmed deletion removes its saved question. Submission finish/delete writes share a file lock, and a late finish cannot recreate a deleted journal entry. This removes the saved unfinished request; it does not add cancellation of an already-running model task. Tests cover both entry points, cancel, deletion surviving a new app session, path validation, and late completion. Existing completed-session deletion is unchanged.
