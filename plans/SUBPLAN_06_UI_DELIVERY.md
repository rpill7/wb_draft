# Subplan 6 — Streamlit App, End-to-End Tests, and Delivery

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 40 minutes  
**Dependency:** Subplan 5 complete and verified.

## Goal
Give the reviewer one command to run the full engine in Streamlit, see the 10 reports, open a citation, and see how each decision was made. Then finish the README, sample output, quality check, and demo. The screen itself is specified in Subplan 9. The assignment values evidence and prompt quality over visual polish, so keep the interface plain and clear.

## Steps
1. **`src/ma_engine/app.py` (Streamlit), started by `make run`.** The screen is specified in Subplan 9. The app contains display code only. Load the processed data with `st.cache_data`. Call `run_engine` with `asyncio.run` from the research action.
2. **Clear error states:** missing processed data (tell the user to run `make data`), missing API key, MCP server failed to start, a buyer failed, fewer than 10 valid reports.
3. **`make check`:** runs the Subplan 4 quality metrics on the latest run in `outputs/runs/` and prints a short pass/fail summary.
4. **End-to-end tests** (`tests/test_end_to_end.py`) with a mocked LLM and the real MCP server and data: a full run produces 10 reports and a trace; missing CSV; missing key; tool failure; one buyer failure with substitution; malformed model output ends as "Not verified". No real API calls in tests.
5. **Real run and measurement** (requires a key): cold-cache and warm-cache wall-clock time, per-buyer time, tokens, cost, model name. Copy one complete run into `outputs/sample/` and commit it.
6. **`docs/architecture.md`:**
   - A Mermaid diagram (renders directly on GitHub) of the request flow from `MAIN_PLAN.md`: Streamlit, `run_engine`, MCP client, FastMCP server and tools, pre-calculated data, ranking, agent loop, guardrails, committee review, saved outputs.
   - A second, smaller diagram showing where calculations happen (data step, ranking, tools, AI).
   - One short paragraph per component: what it does, which file it lives in, and what it never does (for example "tools never calculate", "the AI never calculates").
   - How an outside MCP client connects to the server.
   - Kept in sync with the code as built, not the plan. If the code differs, the diagram follows the code.
7. **README.md** — written for someone who has never seen the project and may not be a Python expert. Short sections, plain words, every command copy-pasteable. Order:
   1. **What this is:** two or three sentences, plus a screenshot of the app.
   2. **Quick start** that works on a clean macOS, Linux, or Windows (WSL) machine:
      - Prerequisites: Python 3.12, `uv` (with its one-line install command), `make`, and an OpenRouter API key (with a link to where to get one).
      - Steps: clone the repo; `make setup`; copy `.env.example` to `.env` and paste the key; `make data`; `make run`; open the address Streamlit prints.
      - What you should see, and roughly how long the first run takes.
      - Commands without `make` as well, for machines that don't have it.
      - **Docker option** (only Docker needed): `docker build -t ma-engine .` then `docker run --rm -p 8501:8501 --env-file .env ma-engine`, and open `http://localhost:8501`.
   3. **No API key?** Point to `outputs/sample/` and the demo video.
   4. **Troubleshooting:** the common failures and their fixes (missing key, missing processed data, wrong Python version, port already in use, OpenRouter rate limit).
   5. **How it works:** a short summary and the main diagram, with a link to `docs/architecture.md` for detail.
   6. The assignment's required topics, each as its own short section:
      - How the CSV is used: cleaning, pre-calculated tables, sector closeness, size bands, use of adjacent sectors, PE skew.
      - Ranking formula and weights.
      - FastMCP tools, and how to connect another MCP client with `make mcp`.
      - Agent design and the prompts (link the prompt files).
      - Guardrails and citations: the rules, the checks, and what "Not verified" means.
      - Non-determinism: temperature, seed, response cache, Regenerate.
      - Assumptions: target region, "strong margin" threshold, dataset-only reports, synthetic deals with real names.
      - Known limitations and failure modes, with honest measured numbers.
      - What we'd do with more time (for example Langfuse or Arize Phoenix for production tracing, an evaluation set reviewed by bankers, outside data sources behind the same guardrails, HTTP transport for the MCP server).
      - Environment variables (only the two) and how to run the tests.
   7. **Final test of the README:** follow it from scratch in a fresh clone and fix every step that doesn't work as written.
8. **Docker check:** once `app.py` and `outputs/sample/` exist, add `!outputs/sample/` to `.dockerignore` and `COPY outputs/sample ./outputs/sample` to the `Dockerfile`, then build and run the image on a machine with Docker running. Confirm the app opens, a run completes, `docker stop` exits within a few seconds, and `docker run --rm ma-engine id -u` prints `1000`.
9. **Secrets check:** `.env` ignored; no keys in code, logs, traces, screenshots, or the demo.
10. **Final review against the assignment:** 10 reports, six sections each, at least two risks, varied conviction, every fact cited, AI-selected tools, single command, README complete, architecture doc linked, sample output committed, demo recorded. Record the result in `plans/PROGRESS.md`.

## Files
`src/ma_engine/app.py`, `README.md`, `docs/architecture.md`, `tests/test_end_to_end.py`, `outputs/sample/`, `Makefile` (`check` target if not done).

## Acceptance criteria
- Someone new to the project follows the README on a clean machine and starts the app without outside help.
- `docs/architecture.md` matches the code as built and is linked from the README.
- The app uses the real MCP tools and real processed data, with no mock results in the app path.
- Every citation can be opened to show its source row.
- Reports that failed checks are clearly marked.
- Tests pass; real timings, tokens, and cost are in the README.
- No secrets or unnecessary generated files in the repository.

## Out of scope
Authentication, deployment, databases, multi-user features, React frontend, PDF export, background jobs, a second interface.

## Completion
Build Subplan 6 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md`.
