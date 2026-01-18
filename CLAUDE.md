# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                        # Install dependencies
uv run pre-commit install                      # Set up git hooks (once after clone)
uv run pytest                                  # Run all tests
uv run pytest tests/test_sanity.py -v          # Run a single test file
uv run fastmcp dev src/opencuff/server.py:mcp  # Run MCP server with inspector
uv run ruff check .                            # Lint code
uv run ruff format .                           # Format code
```

## Architecture

OpenCuff is an MCP (Model Context Protocol) server that provides governed access to system commands for AI coding agents. Built on FastMCP.

- `src/opencuff/server.py` - Main FastMCP server instance (`mcp`)
- Tools are registered on the `mcp` instance using the `@mcp.tool()` decorator
- Tests use `fastmcp.Client` with in-process connection to the server for testing
