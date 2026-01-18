import pytest
from fastmcp import Client

from opencuff import mcp


@pytest.mark.asyncio
async def test_sanity():
    """Verify the MCP server exposes at least one tool."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert len(tools) >= 1, "Expected at least one tool to be exposed"
