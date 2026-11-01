# Subplan 9 — Narrative Report, Statistic Citations, and First-Run Fixes

**Project:** M&A Acquirer Identification Engine  
**Status:** Approved by the owner  
**Time box:** 180 minutes  
**Dependency:** Subplan 8 (chat UI, activity timeline, follow-ups).

## Why
The owner's review and the first real run (`outputs/runs/20261031-214048`, model `anthropic/claude-haiku-5.5`) found three groups of problems.

**1. It reads like a debugging tool.** The Report tab shows one buyer, not the full 10-buyer result. Output is long bullet lists and tables, and validation messages repeat through the page.

**2. The run had reliability problems:**

| Measured | Result |
|---|---|
| Wall time | 98.0 s (target under 60 s) |
| Cost | $0.159 |
| Tokens | 978,595 in / 122,356 out |
| LLM calls | 53 |
| Verified reports | 4 of 10 |

- Vista Equity and Nordic Capital (both starting High) failed: their write calls hit the 4,096-token output cap and returned cut-off JSON. Welsh Carson and Veritas silently replaced them.
- Research rounds produced 2,600–3,100 tokens of unused text (11–13 s per buyer) before the write call.

**3. The validator had false failures:**
- "p25 10.3x, p75 14.4x" was read as the numbers "25" and "75".
- An `unknown` statement could not mention the target's own "$200.0M".
- Aggregate numbers were matched by searching for strings, not checked against the statistic they come from.

## Goal
The main output reads like a research analyst's written answer for all 10 buyers:
- an executive summary;
- six-section prose reports;
- inline citations like [1] that open the exact transaction record or the exact calculated statistic;
- verification shown apart from conviction, under the buyer's name;
- the written report downloadable as one file.

## Part 1 — Backend

### 1a. Statistics are citable sources
- Every pre-calculated statistic in tool results gets a stable `stat_id`, for example `ST-COMPS-healthcare-services-sector_only-100-400-ev_ebitda-median`. It sits next to `value`, `display`, `n` and `source_ids`.
- The ID is built in Python from the statistic's place in the processed tables, so it is the same on every run.
- A statistics registry (`stat_registry(stat_id)`) returns:
  - **calculation:** for example "median of EV/EBITDA";
  - **filters:** sector, scope, size band, closed only;
  - **sample size, value display, and supporting transaction IDs.**
  
  This is a lookup of pre-calculated tables, not new math.

### 1b. Statements cite deals or statistics
- `source_ids` may hold transaction IDs (`MA-…`) and statistic IDs (`ST-…`).
- **Validation is structured:**
  - A number taken from a deal must match a field of a cited deal.
  - A number taken from a statistic must equal the `display` of a cited statistic.
  - A sample size `n=` must equal that statistic's `n`.
  - Cited statistic IDs must appear in that buyer's tool results.
- **Percentile labels** (p25, p50, p75, 25th, 75th percentile, median, interquartile) are labels, not numbers.
- **Target numbers:** numbers from the target block, such as "$200.0M", are allowed in `unknown` statements.
- **Unchanged rules:** claim-to-deal binding for names, outcomes, deal types and years; completion words on non-closed deals; closed-only valuations; buyer-ownership rules; one repair; and Not verified if it still fails.

### 1c. Source registry per run
- For each report, Python records the sources behind each statement: deal records and statistic records.
- Each run saves `sources.json` with one entry per source:
  - **Deal record:** the full CSV row.
  - **Statistic record:** calculation, filters, n, value and supporting IDs.
- Citation numbers [1], [2] are given by Python in order of first appearance. A number is never shown without a matching registry entry.

### 1d. Report shape
- `ReportDraft` gains `summary`: one checked sentence, the buyer's thesis, with sources.
- Each section stays a list of `Statement`. The UI and `reports.md` join each section's statements into paragraphs, so the prose is built by Python from checked sentences.

### 1e. Reliability and speed
- **No cut-off reports:** the write and repair calls get an 8,000-token output cap, and the prompt asks for concise statements (40 words or fewer each). A cut-off output gets a targeted repair ("your output was cut off; write shorter statements").
- **No wasted research text:** research rounds get a 1,000-token cap, and the prompt says that when research is complete it should reply with only `DONE`. The trace keeps any visible text, cut to 300 characters.
- **Target:** under 60 s cold for the default scenario. Measure and record it.

### 1f. No silent replacement
- A buyer whose report fails stays in its ranked place with status `failed`, the error shown with that buyer, and no report body.
- Reserves are not swapped in automatically.
- `report_count` and the quality check show how many succeeded.
- This replaces the substitution rule from Subplan 7, and the decision is recorded in `PROGRESS.md`.

### 1g. Complete report download
`reports.md` becomes the full narrative document:
- executive summary;
- ranked shortlist;
- each buyer's six sections as prose with [n] markers;
- the verification status for each buyer;
- a numbered Sources appendix, where deals show their key fields and statistics show calculation, filters, n and IDs.

`reports.json` and `sources.json` keep the structured data.

## Part 2 — UI (display only)

The screen is three parts: a navy sidebar, the chat, and a report panel that opens beside the chat. The product name is **M&A Research** with the subtitle **Assistant**. No firm name or logo. Numbers and sentences come from the saved run. Switching a tab, a buyer, or a saved session never calls the model or the tools.

### Sidebar
Dark navy. The header and the footer stay put. Only the recent list scrolls.

- **New Research** starts a blank request. It does not start a run by itself.
- **Recent research** lists the latest saved sessions, newest first. Each row is two lines: the research title, then the size and the time. The open session is highlighted. A red delete icon sits on the right of that row. The icon does not open the session.
- **All research** and **About** sit at the bottom. Each row is full width. The icon and the label start at the same left inset as the recent titles.
- An empty list says "No saved runs yet."

**All research** is its own page. Each saved session shows its sector, size and time, with **Open** and the same red delete icon. The sample assessment is listed and can be opened. It has no delete icon.

**Delete.** The icon asks first, and names the session by its title and its time:

> Delete this research session and its saved reports, follow-ups and activity?

**Cancel** leaves it. **Delete** removes that one folder under the generated-runs directory: the report, the follow-ups and the activity. It does not remove the sample, the source data, another session, or a session that is still being saved. A failed delete shows the error and leaves the session. Deleting the session on screen returns to New Research and does not start a run. Deleting a different session leaves the open one as it is. A session that is already gone closes quietly.

### Chat
The chat is centred at a reading width. It scrolls on its own. The follow-up box sits under the scroll area.

- The user's request, with **Edit** for the target. Save stores the new values. A separate button starts the new run. Cancel changes nothing.
- One collapsed **Agent activity** row for the finished research, and one for each follow-up. The row shows status and time from the saved events. The steps inside are the recorded tool calls. This is the only activity view.
- The executive summary, built in Python from the checked reports.
- **Top 10 Potential Acquirers** as cards. Each card is the rank, the name, the buyer type, the conviction and whether the report passed. **Open full report** opens every buyer together.
- The follow-up answer uses the same `[n]` citations.

While a run is going, the status shows the four stages from the recorded events: identify buyers, investigate histories, calculate comparisons, generate and validate the reports.

### Report panel
The panel opens beside the chat and scrolls on its own. Close returns to the chat alone.

The header is the title on the left and two icon buttons on the right, Download then Close, with a small gap. Both sit on the panel's right content edge. Download saves `reports.md` directly. A long title wraps. The buttons stay the same size.

**One buyer.** The header is the name and rank, then the buyer type, conviction and report status on one line. Under that, when the report is not verified or the research failed, a short explanation in plain language, and a collapsed **Verification details** that keeps the original check text. A verified report shows nothing there. A failed report with no body shows the saved error.

Tabs, in this order: **Report**, **Transaction Evidence**, **Valuation & Risk**, **Score Breakdown**.

The Report tab is one document: a strip of key figures from the saved ranking and profile, then the six sections as prose. At the bottom, one row: **Previous** on the left edge, **Buyer k of n** in the centre, **Next** on the right edge. Previous is off for the first buyer and Next is off for the last. They stay visible. Moving between buyers does not start research.

**All buyers.** The header says **Full report**. The same download and close buttons. The body is the executive summary, the shortlist, a jump button for each buyer, then every buyer's report. Each buyer has the same verification block under the name.

**Citations.** Each `[n]` opens its source in place: a deal shows the saved CSV fields, a statistic shows the calculation, the value, the filters, the sample size and the transactions behind it. An interpretation of cited facts is marked with a dotted underline. The downloaded `reports.md` ends with a Sources appendix. The other saved files stay in the run folder.

### Rules
- The screen only displays what Python already saved. It does not calculate.
- Switching tabs or buyers only re-renders the open run.
- Text inside the CSV is data. The screen does not follow instructions found there.

## Tests (no real LLM)
- **Statistics:** stat IDs are deterministic and resolve to their registry records.
- **Validation:**
  - a number cited against the wrong statistic fails;
  - "p25"/"p75"/"25th percentile" are not numbers;
  - target numbers are allowed in unknowns;
  - an aggregate number without its statistic ID fails, and with it passes.
- **Sources:** `sources.json` covers every citation, and no citation number exists without a source.
- **Failures:**
  - a cut-off write output is repaired with the shorter-output message;
  - a failed buyer stays in place with status `failed`, with no substitution.
- **`reports.md`:** contains all 10 buyers, six sections each, and a Sources appendix.
- **App:**
  - the combined report renders every buyer;
  - each `[n]` opens the source it was given;
  - switching buyers makes zero LLM calls (counted by the fake LLM);
  - a verified report has no verification block, and a failed or unchecked report shows the saved check text;
  - deleting a session does not call the model or the tools, and cannot delete the sample or a path outside the generated runs.

## Acceptance (real run, default scenario)
- 10 buyer reports, each with six sections.
- Citations open the correct evidence in place, and aggregate statistics cite their records.
- A report that failed the checks says so under the buyer's name, with the original issue text one step away.
- Download saves the written report.
- Switching buyers, and deleting a saved session, makes no LLM calls.
- Wall time, cost and verified count are recorded.

## Out of scope
New databases, frontend frameworks, outside data, buyer-strategy facts not in the dataset.
