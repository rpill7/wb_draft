"""One FastMCP session per run over stdio, shared by every buyer's agent.

The server is started the same way as `make mcp` (`python -m ma_engine.mcp_server.server`, with
this interpreter, from the repo root). Tools are listed once and converted to the chat tool
format. Tool errors (bad arguments, timeouts) come back as `{"error": message}` so the agent can
pass them to the model as a normal tool result instead of crashing.
"""

import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from ma_engine import config

SERVER_ARGS = ["-m", "ma_engine.mcp_server.server"]
TOOL_TIMEOUT_S = 30


def to_chat_tool(tool) -> dict:
    """An MCP tool as an OpenAI-style function tool (name, description, JSON schema)."""
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description or "", "parameters": tool.input_schema,
    }}


class MCPClient:
    """Async context manager: starts the server, opens one session, closes it at the end."""

    def __init__(self, log_file: Path | None = None, transport=None):
        """`log_file` receives the server's stderr. `transport` (anything `fastmcp.Client`
        accepts, such as the server object for in-memory tests) replaces the stdio server."""
        transport = transport or StdioTransport(
            command=sys.executable, args=SERVER_ARGS, cwd=str(config.ROOT), keep_alive=False,
            log_file=log_file)
        self._client = Client(transport, timeout=TOOL_TIMEOUT_S)
        self.tools: list[dict] = []
        self.sessions_opened = 0

    async def __aenter__(self) -> "MCPClient":
        await self._client.__aenter__()
        self.sessions_opened += 1
        try:
            self.tools = [to_chat_tool(t) for t in await self._client.list_tools()]
        except BaseException as exc:
            await self._client.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._client.__aexit__(*exc_info)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """The tool's {data, source_ids, warnings} result, or {"error": message}."""
        try:
            result = await self._client.call_tool(name, arguments, timeout=TOOL_TIMEOUT_S,
                                                  raise_on_error=False)
        except Exception as exc:
            return {"error": f"Tool {name} failed: {type(exc).__name__}: {exc}"}
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content).strip()
            return {"error": text or f"Tool {name} failed without a message."}
        return result.structured_content
