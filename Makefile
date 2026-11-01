.PHONY: setup data mcp run stop test check

setup:
	uv sync

data:
	uv run python -m ma_engine.prepare

mcp:
	uv run python -m ma_engine.mcp_server.server

run:
	@echo "MCP: local stdio connection, started automatically when needed (no HTTP /mcp endpoint)."
	uv run streamlit run src/ma_engine/app.py

stop:
	@pids=$$(lsof -tiTCP:8501 -sTCP:LISTEN); \
	if [ -z "$$pids" ]; then echo "App is already stopped."; exit 0; fi; \
	for pid in $$pids; do \
		command=$$(ps -p "$$pid" -o command=); \
		app_dir=$$(lsof -a -p "$$pid" -d cwd -Fn | sed -n 's/^n//p'); \
		case "$$command" in \
			*"streamlit run src/ma_engine/app.py"*) \
				if [ "$$app_dir" = "$$(pwd -P)" ]; then \
					kill "$$pid" && echo "Sent stop signal to app (PID $$pid)."; \
				else echo "Port 8501 belongs to another project; left running."; exit 1; fi ;; \
			*) echo "Port 8501 belongs to another service; left running."; exit 1 ;; \
		esac; \
	done

test:
	uv run pytest

check:
	uv run python -m ma_engine.guardrails
