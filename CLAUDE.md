# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Use `make help` to see all available commands. Common ones:

```bash
make dev          # Set up development environment (install + pre-commit hooks)
make test         # Run all tests
make check        # Run lint + format check + tests
make lint         # Run linter
make lint-fix     # Run linter and auto-fix issues
make format       # Format code
make run-dev      # Run MCP server with inspector
make run          # Run MCP server
```

Or use uv directly:

```bash
uv run pytest tests/test_sanity.py -v          # Run a single test file
uv run pytest tests/test_makefile_plugin.py -k "test_simple"  # Run specific tests
```

## Architecture

OpenCuff is an MCP (Model Context Protocol) server that provides governed access to system commands for AI coding agents. Built on FastMCP.

### Core Components

- `src/opencuff/server.py` - Main FastMCP server instance (`mcp`)
- `src/opencuff/plugins/` - Plugin system
  - `base.py` - `InSourcePlugin` ABC, `ToolDefinition`, `ToolResult`
  - `config.py` - Pydantic models for `settings.yml`
  - `manager.py` - `PluginManager`, `PluginLifecycle`, `HealthMonitor`
  - `registry.py` - `ToolRegistry` with `{plugin}.{tool}` namespacing
  - `barrier.py` - `RequestBarrier` for live reload without dropping requests
  - `watcher.py` - `ConfigWatcher` for settings.yml hot reload

### Built-in Plugins

- `plugins/builtin/dummy.py` - Test plugin (echo, add, slow)
- `plugins/builtin/makefile.py` - Exposes Makefile targets as tools
  - Extractors: `SimpleExtractor`, `MakeDatabaseExtractor`, `ExtractorSelector`
  - Caching with content hash, included files tracking, TTL

### Testing

- Tests use `fastmcp.Client` with in-process connection
- Fixtures in `tests/fixtures/makefiles/` for Makefile parsing tests

## Documentation

All design documents should be placed in the `docs/` directory in markdown format.

- `docs/plugin-system-hld.md` - Plugin system design
- `docs/makefile-plugin-hld.md` - Makefile plugin design

## Examples

Configuration examples are in `examples/`:

- `examples/settings.yml` - OpenCuff plugin configuration
- `examples/claude-code-mcp-config.json` - Claude Code MCP server config
