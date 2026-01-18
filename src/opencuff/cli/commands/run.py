"""Run command for OpenCuff CLI.

This module provides the `cuff run` command that starts the MCP server.
"""

from pathlib import Path
from typing import Annotated

import typer


def run_command(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to settings.yml",
        ),
    ] = Path("./settings.yml"),
    transport: Annotated[
        str,
        typer.Option(
            "--transport",
            "-t",
            help="Transport type (stdio or sse)",
        ),
    ] = "stdio",
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Host to bind to (for sse transport)",
        ),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            help="Port to bind to (for sse transport)",
        ),
    ] = 8000,
) -> None:
    """Run the OpenCuff MCP server.

    Starts the MCP server with the specified configuration. By default,
    uses stdio transport for integration with Claude Code and other MCP clients.

    Examples:
        cuff run                    # Run with stdio (default)
        cuff run -t sse -p 8080     # Run with SSE on port 8080
    """
    import os

    # Set the settings path environment variable
    os.environ["OPENCUFF_SETTINGS"] = str(config.absolute())

    # Import the server and run it
    from opencuff.server import mcp

    if transport == "stdio":
        typer.echo("Starting OpenCuff MCP server (stdio)...", err=True)
        typer.echo(f"Using settings: {config}", err=True)
        mcp.run(transport="stdio")
    elif transport == "sse":
        typer.echo(f"Starting OpenCuff MCP server on http://{host}:{port}", err=True)
        typer.echo(f"Using settings: {config}", err=True)
        mcp.run(transport="sse", host=host, port=port)
    else:
        typer.echo(f"Error: Unknown transport '{transport}'", err=True)
        typer.echo("Supported transports: stdio, sse", err=True)
        raise typer.Exit(1)
