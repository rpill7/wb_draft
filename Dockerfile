# M&A Acquirer Identification Engine — Streamlit app.
# Build:  docker build -t ma-engine .
# Run:    docker run --rm -p 8501:8501 --env-file .env ma-engine   (then open http://localhost:8501)

FROM ghcr.io/astral-sh/uv:0.6.17-python3.12-bookworm-slim

# tini forwards stop signals to Streamlit and cleans up the MCP server child process.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so code changes don't reinstall them. Versions come from uv.lock.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Code, prompts, and the raw transaction CSV. The processed tables are rebuilt from the CSV.
COPY src ./src
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY data/raw ./data/raw
RUN uv sync --frozen --no-dev \
    && python -m ma_engine.prepare

# Run as a non-root user that can write only run outputs and the LLM cache.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p outputs/runs .cache \
    && chown -R app:app outputs .cache
USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"

ENTRYPOINT ["tini", "--"]
CMD ["streamlit", "run", "src/ma_engine/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
