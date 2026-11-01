# M&A Research Assistant — Setup

## Prerequisites

- Install [uv](https://docs.astral.sh/uv/getting-started/installation/). It manages Python 3.12 and the project dependencies.
- Use macOS, Linux, or Windows with WSL. The commands below assume a Unix shell.
- Create an [OpenRouter API key](https://openrouter.ai/keys) to run research through the app.

## Install and configure

```bash
git clone https://github.com/rpill7/wb_draft.git
cd wb_draft
make setup
cp .env.example .env
```

Open `.env` and set your API key and model ID:

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
LLM_MODEL=anthropic/claude-haiku-5.5
```

Choose an OpenRouter model that supports tool calling. A free option is `nvidia/nemotron-3.5-super-vl:free`; availability, rate limits, and answer quality vary by model. No locally hosted model is required. Keep your actual `.env` private.

Prepare the data:

```bash
make data
```

This reads `data/raw/` and rebuilds the tables in `data/processed/`.

## Start, stop, and restart

Start the app:

```bash
make run
```

Open [http://localhost:8501](http://localhost:8501). The app starts its MCP server automatically when needed. Application activity appears in the terminal as requests are processed.

Stop the app with `Ctrl+C` in its terminal, or from another terminal in the project folder:

```bash
make stop
```

Restart after changing `.env`:

```bash
make stop
make run
```

`make stop` targets this project's app on port 8501. If you start it on another port, stop it with `Ctrl+C` in its terminal.

### Without make

From the project folder, after creating and configuring `.env`:

```bash
uv sync
uv run python -m ma_engine.data.prepare
uv run streamlit run src/ma_engine/app.py
```

Stop with `Ctrl+C`.

## Run with Docker

Install Docker and configure `.env` as above, then run:

```bash
docker build -t ma-engine .
docker run --rm -p 8501:8501 --env-file .env ma-engine
```

Open [http://localhost:8501](http://localhost:8501). Stop with `Ctrl+C` or `docker stop <container-id>`. With this command, saved sessions are removed when the container is removed.

## Connect an external MCP client

The server uses **stdio**, not HTTP. A local MCP client launches it as a subprocess; there is no `/mcp` URL. The Streamlit UI URL is not an MCP endpoint.

Run `make setup` and `make data` first. Add this configuration to a client that supports local stdio MCP servers, replacing the repository path with your absolute checkout path:

```json
{
  "mcpServers": {
    "ma-engine": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/wb_draft",
        "run",
        "python",
        "-m",
        "ma_engine.mcp_server.server"
      ]
    }
  }
}
```

If the client cannot find `uv`, replace `command` with its absolute executable path, obtained with `command -v uv`.

The client starts and manages the server. You do not need to run the UI or start `make mcp` separately. The read-only data tools do not require an OpenRouter key; your external client manages its own model connection.

To start a standalone stdio process manually:

```bash
make mcp
```

It waits for an MCP client and does not open a web page. Clients requiring a remote or Streamable HTTP endpoint are not supported by the current transport.

## Setup troubleshooting

| Issue | Action |
|---|---|
| Missing API key | Copy `.env.example` to `.env`, set `OPENROUTER_API_KEY`, and restart the app. |
| Model not found or tool calling unsupported | Check `LLM_MODEL` and select a tool-capable model available through OpenRouter. |
| Missing processed data | Run `make data`. |
| Port 8501 already in use | Run `make stop` for this app, or choose another port with `uv run streamlit run src/ma_engine/app.py --server.port 8502`. |
| Provider rate limit | Wait for the provider's indicated retry period or choose another available tool-capable model. |
| `make` unavailable | Use the commands under “Without make.” |
