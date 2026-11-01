# Subplan 0 — Project Foundation

**Project:** M&A Acquirer Identification Engine  
**Status:** Ready for implementation  
**Time box:** 15 minutes  
**Dependency:** None. Read `AGENTS.md` and `plans/MAIN_PLAN.md` first.

## Goal
Set up dependencies, configuration, secrets handling, and the single-command run path, so every later subplan starts from the same base. No business logic in this step.

## Steps
1. **`pyproject.toml`** (managed with `uv`, Python 3.12). Runtime dependencies: `pandas`, `pydantic`, `python-dotenv`, `fastmcp`, `openai`, `streamlit`. Dev dependency: `pytest`. Configure the `src/ma_engine` package so `python -m ma_engine.<module>` works after `uv sync`. Add nothing else unless a later subplan needs it.
2. **`.env.example`** with exactly two lines:
   ```text
   OPENROUTER_API_KEY=your-openrouter-key
   LLM_MODEL=anthropic/claude-sonnet-5.5
   ```
3. **`src/ma_engine/config.py`** — the only file that reads the environment. Load `.env` with `python-dotenv`, then define:
   - `OPENROUTER_API_KEY`, `LLM_MODEL` (read from the environment; may be empty at import time).
   - `OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"`.
   - Paths: project root, raw CSV, processed folder (and each processed file name), outputs folder, runs folder, cache folder (`.cache/llm/`), prompts folder.
   - LLM settings: temperature `0.2`, seed `7`, request timeout, maximum retries for temporary errors.
   - Agent limits: maximum tool rounds per buyer (`4`), maximum buyers in parallel (`10`), maximum repair attempts (`1`).
   - Output sizes: shortlist size (`15`), report count (`10`).
   - Default target profile: sector Healthcare Services, EV $200M, ownership Private, geography "Any region" (regional preference), strong EBITDA margin.
   - A small function that returns the API key or raises a clear error if it is missing. Call it only when the LLM client is created, so data prep, ranking, and tests run without a key.
4. **`.gitignore`** — it already ignores `.env`. Add `outputs/runs/` and `.cache/`. Do not ignore `outputs/sample/` or `data/processed/` (processed files are small and let reviewers inspect the pre-calculated tables).
5. **`Makefile`** with targets `setup`, `data`, `mcp`, `run`, `test`, `check` as listed in `AGENTS.md`. Targets for modules that don't exist yet are fine to add now; they will work once their subplan is done.
6. **Smoke check:** `uv sync` succeeds and `python -c "import ma_engine.config"` works without a `.env` file.

## Files
`pyproject.toml`, `.env.example`, `.gitignore`, `Makefile`, `src/ma_engine/config.py`. Create `src/ma_engine/__init__.py` only if the packaging setup needs it.

## Acceptance criteria
- `make setup` installs everything.
- Importing `config.py` works with no `.env` file present.
- `.env.example` contains only the two variables.
- No business logic, placeholder modules, or extra settings.

## Out of scope
Data cleaning, tools, ranking, prompts, UI, README content.

## Completion
Build Subplan 0 only, following `AGENTS.md`. Record the results in `plans/PROGRESS.md` and review them before starting the next subplan.
