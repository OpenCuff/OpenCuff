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

## Quick Start

### 1. Configure OpenCuff

Create a `settings.yml` file (see `examples/settings.yml`):

```yaml
version: "1"
plugins:
  makefile:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.makefile
    config:
      makefile_path: ./Makefile
      targets: "build,test,clean"
```

### 2. Add to Claude Code

Add to your `~/.claude.json`:

```json
{
  "mcpServers": {
    "opencuff": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/OpenCuff", "fastmcp", "run", "src/opencuff/server.py:mcp"],
      "env": {"OPENCUFF_SETTINGS": "/path/to/settings.yml"}
    }
  }
}
```

See `examples/` for more configuration options.

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
├── server.py              # FastMCP server definition
└── plugins/
    ├── base.py            # Plugin interfaces (InSourcePlugin, ToolDefinition)
    ├── config.py          # Configuration models (settings.yml)
    ├── manager.py         # PluginManager, lifecycle management
    ├── registry.py        # Tool registry with namespacing
    ├── barrier.py         # Request barrier for live reload
    ├── watcher.py         # Config file watcher
    └── builtin/
        ├── dummy.py       # Test plugin
        └── makefile.py    # Makefile target plugin
examples/
├── settings.yml           # Example OpenCuff configuration
└── claude-code-mcp-config.json  # Example Claude Code config
```

## License

Apache 2.0
