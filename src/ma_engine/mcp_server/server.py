"""FastMCP server that registers the read-only tools from `tools.py`.

Run over stdio with `make mcp` (`python -m ma_engine.mcp_server.server`).
"""

from fastmcp import FastMCP

from ma_engine.mcp_server import tools

TOOLS = [
    tools.get_dataset_overview,
    tools.get_sector_overview,
    tools.get_acquirer_profile,
    tools.get_acquirer_deals,
    tools.get_valuation_comps,
    tools.search_deals,
    tools.rank_acquirers,
]

INSTRUCTIONS = (
    "Read-only research tools over a 500-row historical M&A transaction dataset. Every result "
    "is {data, source_ids, warnings}. Quote numbers exactly as their display strings, cite "
    "transaction IDs from source_ids, and read the warnings. Deals that did not close must "
    "never be described as acquired or completed."
)


def create_server() -> FastMCP:
    tools.get_client()
    server = FastMCP(name="ma-engine", instructions=INSTRUCTIONS)
    for tool in TOOLS:
        server.tool(tool, annotations={"readOnlyHint": True})
    return server


mcp = create_server()

if __name__ == "__main__":
    mcp.run(show_banner=False)
