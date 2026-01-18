# OpenCuff Examples

This directory contains example configurations for using OpenCuff with AI coding agents.

## Claude Code Integration

### 1. Configure OpenCuff Settings

Copy `settings.yml` to your project and customize it:

```bash
cp examples/settings.yml ./settings.yml
```

Edit the file to configure which plugins to load and their settings.

### 2. Configure Claude Code

Add the OpenCuff MCP server to your Claude Code configuration.

**Option A: Global Configuration**

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "opencuff": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/OpenCuff",
        "fastmcp",
        "run",
        "src/opencuff/server.py:mcp"
      ],
      "env": {
        "OPENCUFF_SETTINGS": "/path/to/your/project/settings.yml"
      }
    }
  }
}
```

**Option B: Project-Level Configuration**

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "opencuff": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/OpenCuff",
        "fastmcp",
        "run",
        "src/opencuff/server.py:mcp"
      ],
      "env": {
        "OPENCUFF_SETTINGS": "./settings.yml"
      }
    }
  }
}
```

### 3. Available Tools

Once configured, Claude Code will have access to tools from enabled plugins:

**Makefile Plugin** (when enabled):
- `makefile.make_<target>` - Execute a Makefile target
- `makefile.make_list_targets` - List available targets

**Dummy Plugin** (for testing):
- `dummy.echo` - Echo a message
- `dummy.add` - Add two numbers
- `dummy.slow` - Sleep for testing

## Example: Makefile Plugin

Given a Makefile with targets `build`, `test`, `lint`:

```makefile
.PHONY: build test lint

## Build the project
build:
	cargo build --release

## Run tests
test:
	cargo test

## Run linter
lint:
	cargo clippy
```

And `settings.yml`:

```yaml
plugins:
  makefile:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.makefile
    config:
      makefile_path: ./Makefile
      targets: "build,test,lint"
      extractor: simple
```

Claude Code can now run:
- `makefile.make_build` - Build the project
- `makefile.make_test` - Run tests
- `makefile.make_lint` - Run linter
- `makefile.make_list_targets` - See available targets

## Security Notes

1. **Untrusted Makefiles**: Set `trust_makefile: false` to prevent code execution during target discovery
2. **Target Filtering**: Use `targets` and `exclude_targets` to limit which targets are exposed
3. **Working Directory**: Set `working_directory` to control where commands run
