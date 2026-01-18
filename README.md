# OpenCuff

Controlled and secure operations for coding agents.

## What is OpenCuff?

OpenCuff provides a governed way for AI coding agents (Claude, OpenCode, and others) to safely execute operations in your environment. It acts as an MCP server that offers controlled access to a curated set of tools and commands.

## Key Features

- **Policy-based control** - Define what agents can do through simple configuration, no code changes required
- **Governed tool access** - Expose only the commands you trust: bash scripts, Makefile targets, pnpm scripts, and more
- **Zero friction** - Designed to be lightweight with no operational penalty for use
- **Dramatic security improvement** - Prevent agents from running arbitrary commands while still enabling their productivity

## How It Works

OpenCuff sits between your AI coding agent and your system as an MCP (Model Context Protocol) server. Instead of giving agents unrestricted shell access, you define a policy that specifies exactly which commands are allowed.

## Development

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Install dependencies
uv sync

# Set up git hooks
uv run pre-commit install

# Run the MCP server with the inspector for development
uv run fastmcp dev src/opencuff/server.py:mcp
```

### Testing

```bash
uv run pytest
```

### Linting

```bash
uv run ruff check .   # Lint
uv run ruff format .  # Format
```

### Project Structure

```
src/opencuff/
├── __init__.py
└── server.py      # FastMCP server definition
tests/
└── test_sanity.py # Basic connectivity test
```

## License

Apache 2.0
