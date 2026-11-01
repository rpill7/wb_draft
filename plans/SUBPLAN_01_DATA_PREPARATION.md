# Subplan 1 — Clean the Data and Pre-calculate Everything Target-Independent

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 30 minutes  
**Dependency:** Subplan 0 complete.  
**Scope:** Cleaning, validation, and pre-calculated tables only. **Do not build MCP tools, ranking, the agent, or UI.**

## 1. Goal
Turn the source CSV into a consistent, traceable dataset and pre-calculate every number the later steps need that does not depend on the target. After this step, the MCP tools should be able to answer questions by looking things up, not by calculating. The AI will never calculate anything (see `AGENTS.md`, Rule 2).

**Input:** `data/raw/ma_transactions_500.csv` (never modified)  
**Code:** `src/ma_engine/prepare.py`, run with `make data`  
**Tests:** `tests/test_prepare.py`

## 2. What we already know about the file
A quick count during planning found the following. Confirm each item and record actual numbers in `plans/PROGRESS.md`.
- 500 rows, 24 columns. Only `days_to_close` has blanks (94).
- `transaction_id` already exists and is unique (format `MA-YYYY-NNNN`). **Use it as the ID for every citation. Do not create new IDs.**
- Outcomes: Closed, Withdrawn, Pending, Terminated, and **Rumored**.
- 10 sectors, 107 acquirers, two acquirer types (Strategic, Financial Sponsor).
- Columns: `transaction_id, target_company, acquirer, sector, sub_sector, deal_year, deal_quarter, deal_type, geography, financing_type, deal_size_mm, target_revenue_mm, target_ebitda_mm, ebitda_margin_pct, revenue_growth_pct, ev_ebitda_multiple, ev_revenue_multiple, synergy_pct_of_deal, outcome, strategic_rationale_tags, num_bidders, days_to_close, acquirer_type, target_ownership_pre`.

Because the data is clean, keep the cleaning short. Spend the time on the pre-calculated tables.

## 3. Steps

### Step 1.1 — Load and validate
- Load with pandas. Fail with a clear message if the file is missing or unreadable.
- Check every expected column is present; fail with the list of missing ones. Report unexpected columns.
- Check `transaction_id` is unique and non-empty.
- Check numeric columns convert cleanly; deal size and multiples must be positive when present. Record invalid values with their `transaction_id`. Never replace bad numbers with zero, never drop rows.
- Check categories (sector, outcome, acquirer type, geography, deal type, ownership, quarter) against the observed values and log anything unexpected.
- Check whether percentage columns are on a 0–100 scale (they appear to be). Do not rescale unless clearly needed.

### Step 1.2 — Add per-deal derived fields to `transactions.csv`
Keep every original column unchanged. Add:
- `acquirer_key`: lower-case, whitespace-normalized acquirer name, used for grouping. Never merge different names by guesswork.
- `is_closed`: true only for Closed.
- `rationale_tags`: the pipe-delimited tags split, trimmed, and stored as a JSON list string. Keep the original column.
- `size_band`: one of `Under $100M`, `$100M–$400M`, `$400M–$1B`, `Over $1B` (the middle band matches the assignment's mid-market range). Check the distribution and record counts per band.
- `margin_vs_sector`: `above`, `at`, or `below` the median EBITDA margin of that deal's sector.
- `is_regional`: true when geography is a named region (not `National` or `Multi-Regional`).

### Step 1.3 — Pre-calculate the tables
Every statistic below must carry the `transaction_id`s it was calculated from, so later citations are possible. Store numbers as raw values **and** as display strings (`$185.0M`, `13.2x`, `24.1%`) produced by one shared formatting helper. The AI will quote display strings exactly, so it never rounds or converts.

**`acquirer_profiles.json`** — one entry per `acquirer_key`:
- Display name, acquirer type.
- Deal count, closed count, and non-closed count by outcome (with IDs for each).
- Closed deals by sector, by deal type (notably Platform Investment versus Bolt-on Acquisition), and by geography.
- Closed deal size: median, smallest, largest.
- Median EV/EBITDA and EV/Revenue paid on closed deals, each with its own sample size.
- Count of closed deals for private targets (`target_ownership_pre = Private`).
- Financing type mix, median number of bidders, median days to close (closed deals only, with sample size).
- First and last deal year.
- Most common rationale tags with counts.

**`valuation_comps.csv`** — closed deals with valid positive multiples only. One row per combination of:
- `sector` (each of the 10 sectors),
- `scope`: `sector_only` or `sector_plus_adjacent` (using the similarity table below, closeness at or above a documented threshold),
- `size_band` (each band plus `All sizes`).

Columns: deal count; EV/EBITDA count, median, 25th and 75th percentile; EV/Revenue count, median, 25th and 75th percentile; median EBITDA margin; source IDs. The EV/EBITDA and EV/Revenue sample sizes may differ; never imply they are the same.

**`sector_stats.csv`** — per sector: deal count, closed count, median deal size, median and 75th percentile EBITDA margin (defines "strong margin"), share of deals by Financial Sponsors, most common deal types.

**`sector_similarity.csv`** — a small, hand-written closeness score (0 to 1) between every pair of the 10 sectors, with a one-line reason for each pair. This is a documented business judgment, not calculated from data. Suggested starting point for Healthcare Services: Physician Groups, Behavioral Health, Home Health/Hospice, and Dental are close (provider services); Revenue Cycle is moderate (services sold to providers); Health IT and Health Insurance are lower; Medical Devices and Pharma/Biotech are lowest. Define the table as a constant in `prepare.py` and write it out, so tools and ranking read one source.

**`data_quality.json`** — row and column checks, invalid values with IDs, category counts, outcome and buyer-type distributions, size-band counts, repeat-buyer counts, Healthcare Services coverage (total, $100M–$400M, closed), the "strong margin" threshold used, and any warnings or assumptions. No timestamps, so repeated runs produce identical files.

### Step 1.4 — Tests
Small fixture rows, not the full file:
1. Missing column gives a clear error.
2. Duplicate or empty `transaction_id` is reported.
3. Name normalization groups formatting variants but does not merge different companies.
4. Non-closed outcomes (including Rumored) are kept and marked `is_closed = false`.
5. Tags parse correctly.
6. Valuation comps use closed deals with positive multiples only, and the two sample sizes are counted separately.
7. Medians and display strings are correct for a known fixture.
8. Every statistic carries the right source IDs.
9. Running twice on the same input gives byte-identical outputs.

## 4. Required findings for `plans/PROGRESS.md`
Actual numbers for: rows and columns; distinct and repeat acquirers with top frequencies; outcome counts; buyer-type mix; deals per sector and per size band; Healthcare Services in $100M–$400M and how many closed; the strong-margin threshold; invalid or unexpected values. Add two or three sentences on what these mean for ranking and tools.

## 5. Definition of done
- [ ] Raw file untouched; every processed row keeps its `transaction_id`.
- [ ] All six processed files written by `make data`.
- [ ] Every pre-calculated number has raw value, display string, sample size where relevant, and source IDs.
- [ ] Tests pass and the real output is reported.
- [ ] No tools, ranking, LLM, or UI code added.

## Completion
Build Subplan 1 only, following `AGENTS.md`. Begin by checking the real CSV against section 2. Record the results in `plans/PROGRESS.md` and review them before starting the next subplan.
