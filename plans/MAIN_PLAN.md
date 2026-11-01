# M&A Acquirer Identification Engine — Main Plan

This file explains what we are building and why. The engineering rules (guardrails, where calculations happen, configuration, code size, testing) are in `AGENTS.md`. Each subplan covers one build step.

## Purpose
Build a small, evidence-based application that identifies and ranks 10 potential acquirers for a private, regional Healthcare Services company worth approximately $200 million in enterprise value, with strong EBITDA margins. Historical transactions supply the facts, Python does every calculation, and an AI agent chooses which tools to call and writes a buyer-specific explanation for each acquirer. Every factual claim must trace back to a transaction ID in the CSV. Aim for an end-to-end run under 60 seconds.

## Assignment requirements
- Read the supplied 500-row historical M&A transaction CSV. No external database.
- Discover buyers that acquired comparable or related businesses. Do not limit discovery to a narrow sector and size filter; the assignment explicitly rewards using adjacent sectors and the full dataset.
- Rank 10 likely acquirers using explainable signals. Never hardcode buyers.
- Generate 10 evidence-backed, roughly one-page buyer reports, each with: Acquirer Overview; Strategic Fit Thesis; Precedent Activity; Valuation Context; at least two Risk Flags; and High/Medium/Low Conviction with a 1–2 sentence reason tied to data.
- Use an LLM to generate the rationale, not just filter or sort rows.
- Runnable with a single command after installing dependencies.
- README covering: how to run, architecture decisions, prompt design, how the CSV is used, assumptions, known limitations, how output non-determinism is handled, and what we would improve. It must let someone with no context set up and run the project on their own computer by following it step by step.
- Architecture diagram in `docs/architecture.md`, linked from the README.
- Never commit API keys; use environment variables. Because the prototype needs a paid key, include a recorded demo video and commit one sample run so reviewers can judge output without a key.

### Where each requirement is handled
| Requirement | Subplan |
|---|---|
| Use the CSV intelligently (multiples, deal types, sector signals, buyer types) | 1, 3 |
| Tools the agent can call | 2 |
| Ranking without hardcoding | 3 |
| Source citations and no invented facts | 4 (built before the writer) |
| LLM-written, buyer-specific reports with all six sections | 5 |
| Conviction that varies and is defensible | 3 (starting level), 5 (committee review) |
| Under 60 seconds | 5 (concurrency, caching), 6 (measured) |
| Single command, README, architecture doc, demo, sample output | 0, 6 |
| Non-determinism | 5 (low temperature, fixed seed, response cache), 6 (README) |
| Stretch: any target profile, "not relevant" feedback | 3 (target is an input), 6 (UI) |

## What the real data looks like
A quick count was done while planning. Subplan 1 must confirm these numbers and record them in `plans/PROGRESS.md`.

- 500 rows, 24 columns. The data is clean: the only blanks are 94 empty `days_to_close` values (likely non-closed deals).
- Every row already has a unique `transaction_id` such as `MA-2020-0001`. **Use it as the citation ID. Do not create new IDs.**
- Outcomes: Closed 406, Withdrawn 49, Pending 21, Terminated 14, **Rumored 10** (the assignment only mentions four outcomes).
- 107 distinct acquirers; 51 of them have 3 or more deals. 290 deals are by Financial Sponsors and 210 by Strategic buyers.
- 10 sectors. Healthcare Services has 46 deals; 12 of them are $100M–$400M, and 10 of those closed. This matches the assignment. Across all sectors, 154 deals are $100M–$400M, so adjacent sectors matter.
- Useful columns beyond the assignment's list: `target_ownership_pre` (208 private targets), `num_bidders`, `financing_type`, `days_to_close`, `synergy_pct_of_deal`, `target_revenue_mm`, `target_ebitda_mm`.
- Acquirer names are real but the deals are synthetic. For example, a software-focused PE firm appears buying healthcare services businesses. This is one more reason to rely on the dataset only and never on outside knowledge.

## Design principles
1. **Guardrails and citations are the first priority.** They are designed in from the start, built and tested before the report writer, and enforced on every report. See Subplan 4.
2. **The AI never calculates.** Target-independent numbers are pre-calculated during data preparation. Target-dependent numbers (ranking) are calculated by tested Python. The AI only chooses tools, reads results, and writes.
3. **Decoupled tools.** All research operations are exposed by a FastMCP server. The agent reaches them through an MCP client, and any other MCP client (Claude Desktop, Cursor, another agent) can connect to the same server.
4. **Small and explainable.** Every line should be explainable in a follow-up interview. No agent frameworks, no databases, no unnecessary layers.

## Architecture decisions
- **UI: Streamlit.** One interface only. It calls a single function, `run_engine(target)`, and displays the results.
- **LLM: OpenRouter with `anthropic/claude-sonnet-5.5`.** Accessed through the standard `openai` Python library pointed at OpenRouter's address. On OpenRouter this model supports tool calling and structured output, and costs $2 per million input tokens and $10 per million output tokens. It is strong at following strict output rules and fast enough to run 10 reports side by side. The model name lives in `.env`, so switching to another model (for example `anthropic/claude-haiku-5.5` for speed or `anthropic/claude-opus-5.5` for quality) is a one-line change. When requesting structured output, ask OpenRouter to route only to providers that support the requested features.
- **Agent: a short, hand-written tool-calling loop** (no Claude Agent SDK, OpenAI Agents SDK, LangChain, or similar). It is easier to control, test, and explain.
- **Tools: FastMCP server** with read-only tools. The tool logic is plain Python in `mcp_server/tools.py`; `mcp_server/server.py` only registers it. The app's MCP client starts the server over stdio, so the app still starts with one command. `make mcp` starts the server on its own for any other MCP client.
- **Data: pandas and CSV/JSON files** in `data/processed/`. All target-independent statistics are pre-calculated there. No database.
- **Validation: Pydantic** for tool inputs and the report structure, plus `guardrails.py` for fact checks. Pydantic AI is not used.
- **Configuration:** `.env` holds only `OPENROUTER_API_KEY` and `LLM_MODEL`. `config.py` reads them and holds all shared settings (paths, timeouts, limits, temperature, seed, default target). Every other file imports from `config.py`. No toggles.
- **Non-determinism:** temperature 0.2 and a fixed seed where supported, plus a response cache on disk keyed by model, messages, and tools. Re-running the same target returns identical output instantly. A "Regenerate" button in the UI skips the cache for that run.
- **Run records (observability):** each run writes `outputs/runs/<run_id>/trace.json` with every tool call, its arguments, duration, status, token usage, cost (OpenRouter returns cost with usage), and guardrail results. Streamlit shows this in a "How this was decided" section. External tools such as Langfuse, Braintrust, or Arize Phoenix are not used; the README names them as the production next step.

### Request flow
```text
Streamlit (target form)
  -> run_engine(target)
       -> MCP client (one session per run) -> FastMCP server (stdio)
       -> rank_acquirers(target)              [Python ranking, no AI]
       <- shortlist of 15 with score breakdowns and source IDs
       -> for each top buyer, in parallel (up to 10 at once):
            agent loop (LLM client)
              -> chooses MCP tools -> pre-calculated lookups -> results with source IDs
              <- decides whether more evidence is needed (max 4 tool rounds)
            -> writes structured report (only quoting tool-provided numbers)
            -> guardrails.check(report, evidence the agent received)
            -> one repair attempt if needed, else "Not verified"
       -> committee review: one LLM call over all 10 reports
            adjusts conviction by at most one level, flags repeated arguments
            -> guardrails.check again
       -> save reports.json, reports.md, trace.json
  <- Streamlit shows ranked list, six-section reports, clickable source rows, trace
```

### Where calculations happen
| Place | Calculates | Example |
|---|---|---|
| `prepare.py` (once, offline) | Anything not tied to the target | Buyer deal counts by sector, median multiples by sector and size band, "margin above sector median" labels |
| `ranking.py` (per request) | Only what depends on the target | Distance from $200M, sector closeness to the target sector, final score |
| MCP tools | Nothing new | Look up tables, filter rows, call ranking |
| AI agent | Nothing | Chooses tools, writes the narrative |

Why not pre-calculate everything? Ranking depends on the target's sector, size, and geography. Fixing those in the data step would hardcode one target and rule out the "any target profile" stretch goal. Ranking stays in Python, is deterministic, and is tested, so the rule "the AI never calculates" still holds.

## Guardrails and citations (summary)
Full design in Subplan 4.
- Each report section is a list of statements. Each statement has text, a label (`fact`, `interpretation`, `unknown`), and the transaction IDs it relies on. A `fact` without IDs fails.
- A cited ID must exist in the dataset **and** appear in the tool results that buyer's agent received.
- Every number in the text must match a display string from the tool results.
- Non-closed deals may not be described with completion words.
- Valuation statements must use closed-deal comps and state the sample size.
- Company names in the text must belong to that buyer's evidence.
- Python, not the AI, turns the checked statements into the final Markdown and adds the citation tags, so the shown citations are exactly the checked ones.

## Folder structure
Folders exist already; files are created only by the subplan that needs them.

```text
wb_draft/
├── AGENTS.md                     # engineering guidelines
├── README.md                     # setup and overview for any reader
├── docs/
│   └── architecture.md           # architecture diagram and component notes
├── Makefile
├── pyproject.toml
├── .env.example                  # OPENROUTER_API_KEY, LLM_MODEL only
├── data/
│   ├── raw/ma_transactions_500.csv      # never modified
│   └── processed/                       # written by `make data`
│       ├── transactions.csv             # cleaned rows + per-deal derived fields
│       ├── acquirer_profiles.json       # one pre-calculated profile per buyer
│       ├── valuation_comps.csv          # closed-deal multiples by sector, scope, size band
│       ├── sector_stats.csv             # per-sector counts, medians, margin thresholds
│       ├── sector_similarity.csv        # documented closeness between sectors
│       └── data_quality.json            # checks, counts, warnings
├── src/ma_engine/
│   ├── config.py
│   ├── models.py                 # Pydantic models: target, tool results, report
│   ├── prepare.py                # Subplan 1
│   ├── clients/
│   │   ├── data_client.py        # loads processed files once; formatting helpers
│   │   ├── mcp_client.py
│   │   └── llm_client.py         # OpenRouter, cache, usage and cost
│   ├── mcp_server/
│   │   ├── server.py             # FastMCP registration only
│   │   └── tools.py              # plain tool functions
│   ├── ranking.py                # Subplan 3
│   ├── guardrails.py             # Subplan 4
│   ├── agent.py                  # Subplan 5
│   ├── engine.py                 # run_engine(target)
│   ├── prompts/
│   │   ├── research_writer.md
│   │   ├── repair.md
│   │   └── committee.md
│   └── app.py                    # Streamlit
├── tests/
├── outputs/
│   ├── sample/                   # one committed example run
│   └── runs/                     # per-run outputs and traces (git-ignored)
└── plans/
    ├── MAIN_PLAN.md
    ├── PROGRESS.md
    └── SUBPLAN_0X_*.md
```

## Subplans and time boxes
The assignment expects 3–4 hours. Time boxes keep the build inside that window.

| # | Subplan | Time box |
|---|---|---|
| 0 | Project foundation (`SUBPLAN_00_FOUNDATION.md`) | 15 min |
| 1 | Data cleaning and pre-calculation (`SUBPLAN_01_DATA_PREPARATION.md`) | 30 min |
| 2 | Data client and FastMCP tools (`SUBPLAN_02_FASTMCP_TOOLS.md`) | 30 min |
| 3 | Buyer discovery and ranking (`SUBPLAN_03_BUYER_RANKING.md`) | 30 min |
| 4 | Guardrails and citation checks (`SUBPLAN_04_GUARDRAILS.md`) | 35 min |
| 5 | Agent research and report writing (`SUBPLAN_05_AGENT_REPORTS.md`) | 70 min |
| 7 | Review fixes and first real run (`SUBPLAN_07_REVIEW_FIXES.md`) — runs before 6 | 120 min |
| 6 | Streamlit app, end-to-end tests, README, demo (`SUBPLAN_06_UI_DELIVERY.md`) | 40 min |

Guardrails (4) are built before the writer (5) on purpose: the checker defines what a good report is, and the writer is built to pass it. Subplan 7 comes from `PRINCIPAL_ENGINEER_REVIEW.md` and runs before Subplan 6, so the app and sample run show corrected behavior.

### If we fall behind, cut in this order
1. Committee review step (keep the Python starting conviction instead).
2. "Not relevant" feedback button.
3. Company-name check in guardrails (keep the ID, number, and outcome-wording checks).
4. Reduce agent tool rounds from 4 to 2.

Never cut: source citations, the ID and number checks, the "Not verified" status, the README, or the sample output.

## Important data cautions
- The dataset leans toward PE buyers. That reflects the data, not proof that PE firms are better buyers. Show the buyer-type mix in the results and discuss it in the README.
- Healthcare Services alone gives only about 10 closed comps in the $100M–$400M range. Valuation context should show both the narrow comps and the wider "sector plus adjacent sectors" comps, each with its sample size.
- The target's exact region is not given ("regional"). Default assumption: geography fit rewards buyers with regional (not National or Multi-Regional) deal history. The UI lets the user pick a specific region.
- "Strong EBITDA margins" is defined from the data: above the Healthcare Services median margin (exact threshold recorded by Subplan 1).
- Historical activity does not prove current buying intent. Reports must say so where it matters.

## Completion standard
A reviewer can start the app with one command, see why each of the 10 buyers was chosen, click any citation to see the original CSV row, see which tools the agent called and why, see any report that failed checks clearly marked, and understand the limits from the README without reading much code.
