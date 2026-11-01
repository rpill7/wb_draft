# Subplan 2 — Data Client and FastMCP Tools

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 30 minutes  
**Dependency:** Subplan 1 complete and verified.

## Goal
Expose the processed data through a reusable data client and a small, read-only FastMCP server. The AI agent (Subplan 5) will discover and call these tools through an MCP client. Any other MCP client, such as Claude Desktop or Cursor, can also connect to the same server, which is why the tools live behind MCP instead of inside the agent.

**Tools do no new math.** They look up pre-calculated tables and filter rows. The one exception is `rank_acquirers`, which calls the tested Python ranking in Subplan 3.

## Inputs
- All files in `data/processed/` and the findings in `plans/PROGRESS.md`.
- `config.py` for paths and limits.

## Steps
1. **`src/ma_engine/clients/data_client.py`.** Load every processed file once and keep it in memory. Provide small lookup and filter methods used by the tools. Hold the single formatting helper for display strings, or import it from `prepare.py`, so there is one source.
2. **`src/ma_engine/mcp_server/tools.py`.** Plain Python functions, testable without MCP. Each returns `{"data": ..., "source_ids": [...], "warnings": [...]}`. Numbers appear as both raw value and display string.
3. **`src/ma_engine/mcp_server/server.py`.** Create the FastMCP server, register the tool functions, and run over stdio. The data client is created once when the server starts. Nothing else lives here.
4. **Tools to implement now:**

   | Tool | Arguments | Returns |
   |---|---|---|
   | `get_dataset_overview` | none | Sector list, size band labels, outcome list, buyer-type mix, data-quality warnings. Lets the agent learn the allowed values. |
   | `get_sector_overview` | `sector` | Pre-calculated sector stats and the list of other sectors with closeness scores and reasons. |
   | `get_acquirer_profile` | `acquirer_key` | The pre-calculated buyer profile from `acquirer_profiles.json`. |
   | `get_acquirer_deals` | `acquirer_key`, optional `sectors`, `closed_only` (default true), `limit` | That buyer's deal rows (key columns only), newest first, with outcome clearly shown. |
   | `get_valuation_comps` | `sector`, `scope` (`sector_only` or `sector_plus_adjacent`), `size_band` | The matching row from `valuation_comps.csv`, with both sample sizes and source IDs. |
   | `search_deals` | optional `sectors`, `size_band`, `outcomes`, `acquirer_type`, `deal_types`, `geography`, `limit` | Filtered deal rows. Filtering only, no aggregates. |

   `rank_acquirers` is added to the same server in Subplan 3.
5. **Tool descriptions are prompts.** Each docstring says what the tool returns, when the agent should use it (for example "Use when the buyer has few deals in the target sector, to see adjacent-sector comps"), and what each argument means. Keep them short and exact.
6. **Safety limits.** Validate arguments against allowed values and return a clear message for unknown sectors, bands, outcomes, or keys. Cap result rows (default 10, maximum 25). Read-only. No free-form SQL or file paths. Distinguish "no matching rows" (normal, with a warning) from a tool error.
7. **Tests** (`tests/test_tools.py`): each tool on real processed data; unknown argument values; empty results; row caps; closed-only default; source IDs always present and valid; display strings match raw values. Add one test that uses FastMCP's in-memory client to list tools and call one, to prove the server wiring works.
8. **Document** in `plans/PROGRESS.md` how to start the server (`make mcp`) and how to connect an outside MCP client to it. The README (Subplan 6) will reuse this.

## Files
`src/ma_engine/clients/data_client.py`, `src/ma_engine/mcp_server/tools.py`, `src/ma_engine/mcp_server/server.py`, `src/ma_engine/models.py` (only the small shared models needed now), `tests/test_tools.py`.

## Acceptance criteria
- `make mcp` starts the server and it lists all six tools.
- Every tool result includes valid source IDs and display strings.
- No tool computes a new statistic; aggregates come from pre-calculated files.
- Valuation results come from closed deals only and show both sample sizes.
- Tests pass and the real output is reported.

## Out of scope
Ranking (Subplan 3), LLM calls, agent loop, UI, HTTP transport, authentication.

## Completion
Build Subplan 2 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md` and review them before starting the next subplan.
