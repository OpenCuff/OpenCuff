from fastmcp import FastMCP

mcp = FastMCP("OpenCuff")


@mcp.tool()
def hello() -> str:
    """A simple hello world tool to verify the server is running."""
    return "Hello from OpenCuff!"
