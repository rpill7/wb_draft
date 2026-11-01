.PHONY: setup data mcp run test check

setup:
	uv sync

data:
	uv run python -m ma_engine.prepare

mcp:
	uv run python -m ma_engine.mcp_server.server

run:
	uv run streamlit run src/ma_engine/app.py

test:
	uv run pytest

check:
	uv run python -m ma_engine.guardrails
