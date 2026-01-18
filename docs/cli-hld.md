# OpenCuff CLI - High-Level Design

**Version:** 1.0
**Date:** 2026-01-18
**Status:** Draft

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Command Structure](#command-structure)
4. [Command Specifications](#command-specifications)
5. [Architecture](#architecture)
6. [Plugin Discovery Interface](#plugin-discovery-interface)
7. [Output Formatting](#output-formatting)
8. [Signal Handling](#signal-handling)
9. [Error Handling](#error-handling)
10. [Configuration](#configuration)
11. [Future Considerations](#future-considerations)

---

## Executive Summary

The `cuff` CLI provides a command-line interface for OpenCuff that allows users to:

- **Initialize** projects by discovering applicable plugins and generating configuration
- **Diagnose** plugin status and troubleshoot integration issues
- **Interact** with plugins directly (list targets, run commands, etc.)

The CLI complements the MCP server by providing a standalone tool for setup, diagnostics, and direct plugin interaction without requiring an AI agent.

---

## Goals and Non-Goals

### Goals

- **Simple Initialization**: `cuff init` should create a working `settings.yml` with zero manual configuration for common setups
- **Plugin Discovery**: Automatically detect applicable plugins based on project files (Makefile, package.json, etc.)
- **Diagnostic Capability**: Provide tools to verify plugin health and troubleshoot issues
- **Direct Plugin Access**: Allow users to interact with plugins from the command line
- **Consistent UX**: Follow standard CLI conventions (subcommands, flags, exit codes)

### Non-Goals

- Replace the MCP server functionality
- Provide a REPL or interactive mode (v1)
- Support remote plugin management
- GUI or TUI interfaces

---

## Command Structure

```
cuff <command> [subcommand] [options] [arguments]
```

### Top-Level Commands

| Command | Description |
|---------|-------------|
| `cuff init` | Initialize a new settings.yml based on discovered plugins |
| `cuff status` | Show status of all configured plugins |
| `cuff doctor` | Diagnose common issues and suggest fixes |
| `cuff <plugin> <action>` | Plugin-specific commands |

### Plugin Commands

Each plugin can expose CLI commands. The pattern is:

```
cuff <plugin-name> <action> [args...]
```

Examples:
- `cuff makefile list-targets`
- `cuff makefile run-target build`
- `cuff packagejson list-scripts`
- `cuff packagejson run-script test`

---

## Command Specifications

### `cuff init`

Initialize a new project with OpenCuff configuration.

```
cuff init [options]

Options:
  --output, -o <path>    Output path for settings.yml (default: ./settings.yml)
  --force, -f            Overwrite existing settings.yml
  --dry-run              Show what would be generated without writing
  --plugins <list>       Comma-separated list of plugins to include (default: all discovered)
  --exclude <list>       Comma-separated list of plugins to exclude
```

**Behavior:**

1. Scan current directory for discoverable items
2. For each registered plugin, call `Plugin.discover(path)`
3. Collect discovery results and build suggested configuration
4. Generate `settings.yml` with discovered plugins enabled
5. Print summary of discovered plugins and created configuration

**Example Output:**

```
$ cuff init

Discovering plugins...
  [+] makefile: Found Makefile with 12 targets
  [+] packagejson: Found package.json with 5 scripts (npm)
  [-] dockerfile: No Dockerfile found

Generated settings.yml with 2 plugins:
  - makefile: 12 targets (build, test, clean, ...)
  - packagejson: 5 scripts (start, test, build, ...)

Run 'cuff status' to verify the configuration.
```

**Exit Codes:**

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | No plugins discovered |
| 2 | Output file exists (without --force) |
| 3 | Write error |

---

### `cuff status`

Show the current status of all configured plugins.

```
cuff status [options]

Options:
  --config, -c <path>    Path to settings.yml
  --json                 Output as JSON
  --verbose, -v          Show detailed information
```

**Behavior:**

1. Load settings.yml
2. For each enabled plugin:
   - Check if plugin can be loaded
   - Run health check
   - List available tools
3. Display status summary

**Example Output:**

```
$ cuff status

OpenCuff Status
===============

Settings: ./settings.yml
Plugins: 2 enabled, 0 disabled

makefile (active)
  Health: OK
  Tools: 12
    - make_build
    - make_test
    - make_clean
    ...

packagejson (active)
  Health: OK
  Package Manager: npm
  Tools: 6
    - npm_list_scripts
    - npm_start
    - npm_test
    ...
```

---

### `cuff doctor`

Diagnose common issues and suggest fixes.

```
cuff doctor [options]

Options:
  --config, -c <path>    Path to settings.yml
  --fix                  Attempt to fix issues automatically
```

**Checks Performed:**

1. Settings file exists and is valid YAML
2. All referenced files exist (Makefile, package.json, etc.)
3. Required dependencies are available (make, npm/pnpm, etc.)
4. Plugin modules can be imported
5. No configuration conflicts

**Example Output:**

```
$ cuff doctor

Running diagnostics...

[PASS] Settings file: ./settings.yml
[PASS] YAML syntax valid
[PASS] makefile plugin: ./Makefile exists
[WARN] packagejson plugin: pnpm-lock.yaml found but package_manager set to 'npm'
       Suggestion: Change package_manager to 'pnpm' or 'auto'
[PASS] All plugin modules can be imported

Summary: 4 passed, 1 warning, 0 errors
```

---

### `cuff <plugin> <action>`

Execute plugin-specific commands.

#### Makefile Plugin Commands

```
cuff makefile list-targets [options]
cuff makefile run-target <target> [options]

Options:
  --config, -c <path>    Path to settings.yml
  --makefile <path>      Override Makefile path
  --dry-run              Show command without executing (for run-target)
  --timeout <seconds>    Execution timeout
```

**Example:**

```
$ cuff makefile list-targets

Available Makefile targets:
  build          Build the project
  test           Run all tests
  test-verbose   Run tests with verbose output
  clean          Clean build artifacts
  install        Install dependencies
  ...

$ cuff makefile run-target test --dry-run
Would execute: make -f ./Makefile test

$ cuff makefile run-target test
Running: make test
...
[output from make test]
...
Exit code: 0
```

#### Package.json Plugin Commands

```
cuff packagejson list-scripts [options]
cuff packagejson run-script <script> [options]

Options:
  --config, -c <path>       Path to settings.yml
  --package-json <path>     Override package.json path
  --package-manager <pm>    Override package manager (npm, pnpm)
  --dry-run                 Show command without executing
  --timeout <seconds>       Execution timeout
```

**Example:**

```
$ cuff packagejson list-scripts

Package Manager: npm
Available scripts:
  start        node server.js
  test         jest
  build        webpack --mode production
  lint         eslint src/

$ cuff packagejson run-script test
Running: npm run test
...
[output from npm run test]
...
Exit code: 0
```

---

## Architecture

### Component Diagram

```
+------------------------------------------------------------------+
|                           cuff CLI                                |
|                                                                   |
|  +-------------+  +-------------+  +---------------------------+  |
|  |   typer     |  |  PluginCLI  |  |    PluginDiscovery       |  |
|  |  (commands) |  |  Registry   |  |    Coordinator           |  |
|  +------+------+  +------+------+  +-------------+-------------+  |
|         |                |                       |                |
|         v                v                       v                |
|  +------+----------------+--+          +---------+---------+      |
|  |      CLI Router          |          |  Discovery Runner |      |
|  +------+-------------------+          +---------+---------+      |
|         |                                        |                |
+---------+----------------------------------------+----------------+
          |                                        |
          v                                        v
  +-------+-------+                      +---------+---------+
  | Plugin System |                      | Plugin.discover() |
  | (existing)    |                      | (class method)    |
  +---------------+                      +-------------------+
```

### Module Structure

```
src/opencuff/
├── cli/
│   ├── __init__.py
│   ├── main.py           # Entry point, typer app
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── init.py       # cuff init
│   │   ├── status.py     # cuff status
│   │   └── doctor.py     # cuff doctor
│   ├── plugin_cli.py     # Dynamic plugin command registration
│   └── discovery.py      # Discovery coordination
└── plugins/
    ├── base.py           # Add DiscoveryResult, discover() interface
    └── ...
```

### Entry Point

```python
# src/opencuff/cli/main.py
"""OpenCuff CLI entry point."""

import logging
import typer

from opencuff.cli.commands import init, status, doctor
from opencuff.cli.plugin_cli import register_plugin_commands

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="cuff",
    help="OpenCuff CLI - Controlled operations for coding agents",
    no_args_is_help=True,
)

# Register core commands
app.command()(init.init_command)
app.command()(status.status_command)
app.command()(doctor.doctor_command)

# Dynamically register plugin commands with error handling
try:
    register_plugin_commands(app)
except Exception as e:
    # Log but don't crash - core commands should still work
    logger.warning(f"Failed to register some plugin commands: {e}")


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
```

---

## Plugin Discovery Interface

> **Note:** The canonical definition of `DiscoveryResult`, `CLICommand`, and the discovery protocol
> is in [plugin-system-hld.md](./plugin-system-hld.md#plugin-discovery-interface).
> This section shows CLI-specific examples of how plugins implement discovery.

### Discovery Protocol

Each plugin that supports discovery must implement a class method:

```python
@dataclass
class DiscoveryResult:
    """Result of plugin discovery."""

    applicable: bool
    """Whether this plugin is applicable to the directory."""

    confidence: float
    """Confidence score 0.0-1.0 for applicability."""

    suggested_config: dict[str, Any]
    """Suggested plugin configuration."""

    description: str
    """Human-readable description of what was discovered."""

    warnings: list[str] = field(default_factory=list)
    """Any warnings about the discovery (e.g., missing optional files)."""


class InSourcePlugin(ABC):
    """Base class for in-source plugins."""

    @classmethod
    def discover(cls, directory: Path) -> DiscoveryResult:
        """Discover if this plugin is applicable to the given directory.

        This is a CLASS METHOD that runs WITHOUT instantiating the plugin.
        It should check for the presence of relevant files and return
        a suggested configuration.

        Args:
            directory: The directory to scan for applicable files.

        Returns:
            DiscoveryResult indicating applicability and suggested config.
        """
        # Default implementation: not discoverable
        return DiscoveryResult(
            applicable=False,
            confidence=0.0,
            suggested_config={},
            description="This plugin does not support discovery",
        )
```

### Makefile Plugin Discovery Example

```python
class MakefilePlugin(InSourcePlugin):
    """Plugin that exposes Makefile targets."""

    @classmethod
    def discover(cls, directory: Path) -> DiscoveryResult:
        """Discover Makefiles in the directory."""
        makefile_names = ["Makefile", "makefile", "GNUmakefile"]

        for name in makefile_names:
            makefile_path = directory / name
            if makefile_path.exists():
                # Parse to count targets
                targets = cls._extract_targets_static(makefile_path)

                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={
                        "makefile_path": f"./{name}",
                        "targets": "*",
                        "extractor": "auto",
                        "cache_ttl": 300,
                        "trust_makefile": True,
                        "working_directory": ".",
                    },
                    description=f"Found {name} with {len(targets)} targets",
                )

        return DiscoveryResult(
            applicable=False,
            confidence=0.0,
            suggested_config={},
            description="No Makefile found",
        )

    @staticmethod
    def _extract_targets_static(makefile_path: Path) -> list[str]:
        """Static target extraction for discovery (no instance needed)."""
        # Simple regex extraction for discovery purposes
        content = makefile_path.read_text()
        pattern = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:", re.MULTILINE)
        return pattern.findall(content)
```

### Package.json Plugin Discovery Example

```python
class PackageJsonPlugin(InSourcePlugin):
    """Plugin that exposes npm/pnpm scripts."""

    @classmethod
    def discover(cls, directory: Path) -> DiscoveryResult:
        """Discover package.json in the directory."""
        package_json_path = directory / "package.json"

        if not package_json_path.exists():
            return DiscoveryResult(
                applicable=False,
                confidence=0.0,
                suggested_config={},
                description="No package.json found",
            )

        try:
            content = json.loads(package_json_path.read_text())
            scripts = content.get("scripts", {})

            # Detect package manager
            pm = cls._detect_package_manager_static(directory)

            warnings = []
            if pm == "npm" and (directory / "pnpm-lock.yaml").exists():
                warnings.append(
                    "pnpm-lock.yaml found but no pnpm detected; "
                    "consider using package_manager: pnpm"
                )

            return DiscoveryResult(
                applicable=True,
                confidence=1.0,
                suggested_config={
                    "package_json_path": "./package.json",
                    "package_manager": "auto",
                    "scripts": "*",
                    "exclude_scripts": "",
                    "exclude_lifecycle_scripts": True,
                    "cache_ttl": 300,
                    "working_directory": ".",
                },
                description=f"Found package.json with {len(scripts)} scripts ({pm})",
                warnings=warnings,
            )

        except json.JSONDecodeError:
            return DiscoveryResult(
                applicable=False,
                confidence=0.0,
                suggested_config={},
                description="package.json exists but contains invalid JSON",
            )

    @staticmethod
    def _detect_package_manager_static(directory: Path) -> str:
        """Detect package manager from lock files.

        Lock file precedence (first match wins):
        1. pnpm-lock.yaml -> pnpm
        2. yarn.lock -> yarn
        3. bun.lockb -> bun
        4. package-lock.json -> npm
        5. (default) -> npm
        """
        if (directory / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (directory / "yarn.lock").exists():
            return "yarn"
        if (directory / "bun.lockb").exists():
            return "bun"
        if (directory / "package-lock.json").exists():
            return "npm"
        return "npm"  # default
```

### CLI Commands for Plugins

Plugins can optionally expose CLI commands by implementing:

```python
@dataclass
class CLICommand:
    """Definition of a CLI command exposed by a plugin."""

    name: str
    """Command name (e.g., 'list-targets')."""

    help: str
    """Help text for the command."""

    callback: Callable[..., Any]
    """The function to call when command is invoked."""

    arguments: list[CLIArgument] = field(default_factory=list)
    """Positional arguments."""

    options: list[CLIOption] = field(default_factory=list)
    """Optional flags."""


class InSourcePlugin(ABC):
    """Base class for in-source plugins."""

    @classmethod
    def get_cli_commands(cls) -> list[CLICommand]:
        """Return CLI commands this plugin provides.

        Override to expose plugin-specific CLI commands.
        """
        return []
```

### Makefile CLI Commands Example

```python
class MakefilePlugin(InSourcePlugin):

    @classmethod
    def get_cli_commands(cls) -> list[CLICommand]:
        return [
            CLICommand(
                name="list-targets",
                help="List available Makefile targets",
                callback=cls._cli_list_targets,
                options=[
                    CLIOption(
                        name="--makefile",
                        help="Path to Makefile",
                        default="./Makefile",
                    ),
                ],
            ),
            CLICommand(
                name="run-target",
                help="Run a Makefile target",
                callback=cls._cli_run_target,
                arguments=[
                    CLIArgument(
                        name="target",
                        help="Target name to run",
                        required=True,
                    ),
                ],
                options=[
                    CLIOption(
                        name="--dry-run",
                        help="Show command without executing",
                        is_flag=True,
                    ),
                    CLIOption(
                        name="--timeout",
                        help="Execution timeout in seconds",
                        default=300,
                    ),
                ],
            ),
        ]

    @classmethod
    def _cli_list_targets(cls, makefile: str = "./Makefile") -> None:
        """List targets CLI handler."""
        path = Path(makefile)
        if not path.exists():
            typer.echo(f"Error: Makefile not found: {makefile}", err=True)
            raise typer.Exit(1)

        targets = cls._extract_targets_static(path)
        typer.echo("Available Makefile targets:")
        for target in targets:
            typer.echo(f"  {target}")

    @classmethod
    def _cli_run_target(
        cls,
        target: str,
        dry_run: bool = False,
        timeout: int = 300,
    ) -> None:
        """Run target CLI handler."""
        cmd = ["make", target]

        if dry_run:
            typer.echo(f"Would execute: {' '.join(cmd)}")
            return

        typer.echo(f"Running: make {target}")
        result = subprocess.run(cmd, timeout=timeout)
        typer.echo(f"Exit code: {result.returncode}")
        raise typer.Exit(result.returncode)
```

---

## Output Formatting

### Standard Output

- Use plain text for human-readable output
- Support `--json` flag for machine-readable output
- Use colors sparingly (green for success, yellow for warnings, red for errors)
- Respect `NO_COLOR` environment variable

### JSON Output Schema

```json
{
  "status": "success" | "warning" | "error",
  "data": { ... },
  "warnings": ["..."],
  "errors": ["..."]
}
```

### Progress Indicators

For long-running operations:
- Use simple dots or spinners for terminal
- Use line-by-line progress for CI environments (detect via `CI` env var)

---

## Signal Handling

The CLI handles system signals for graceful shutdown during long-running operations:

### SIGINT (Ctrl+C)

- First SIGINT: Attempt graceful cancellation
  - For running subprocesses: Send SIGTERM to child process
  - For discovery: Cancel current plugin, return partial results
  - Display "Cancelling..." message
- Second SIGINT: Force immediate exit
  - For running subprocesses: Send SIGKILL to child process
  - Exit with code 130 (standard for SIGINT)

### SIGTERM

- Treat as graceful shutdown request
- Same behavior as first SIGINT

### Implementation

```python
import signal
import sys
from typing import Any

_interrupt_count = 0
_current_process: subprocess.Popen | None = None


def _handle_interrupt(signum: int, frame: Any) -> None:
    """Handle SIGINT/SIGTERM."""
    global _interrupt_count, _current_process
    _interrupt_count += 1

    if _interrupt_count == 1:
        typer.echo("\nCancelling...", err=True)
        if _current_process:
            _current_process.terminate()
    else:
        typer.echo("\nForce exit", err=True)
        if _current_process:
            _current_process.kill()
        sys.exit(130)


# Register handlers
signal.signal(signal.SIGINT, _handle_interrupt)
signal.signal(signal.SIGTERM, _handle_interrupt)
```

---

## Error Handling

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Configuration error |
| 3 | Plugin error |
| 4 | File not found |
| 5 | Permission denied |

### Error Messages

Format: `Error: <message>`

Include suggestions where possible:
```
Error: settings.yml not found

Run 'cuff init' to create a configuration file, or specify a path with --config.
```

---

## Configuration

### Settings File Discovery

The CLI searches for `settings.yml` in order:
1. `--config` / `-c` flag value
2. `OPENCUFF_SETTINGS` environment variable
3. `./settings.yml`
4. `~/.opencuff/settings.yml`

### Plugin Registry

For `cuff init` discovery, the CLI needs to know which plugins exist. This is handled via a plugin registry:

```python
# src/opencuff/plugins/registry.py

DISCOVERABLE_PLUGINS: dict[str, type[InSourcePlugin]] = {
    "makefile": MakefilePlugin,
    "packagejson": PackageJsonPlugin,
    # Add new plugins here
}


def get_discoverable_plugins() -> dict[str, type[InSourcePlugin]]:
    """Return all plugins that support discovery."""
    return {
        name: cls
        for name, cls in DISCOVERABLE_PLUGINS.items()
        if hasattr(cls, "discover")
    }
```

---

## Future Considerations

### Potential Enhancements

1. **Interactive Init Mode**
   - Prompt user for choices during `cuff init`
   - Allow selecting specific targets/scripts to expose

2. **Plugin Marketplace**
   - `cuff plugin list` - List available plugins
   - `cuff plugin install <name>` - Install community plugins

3. **Watch Mode**
   - `cuff watch` - Monitor settings and plugins for changes

4. **Shell Completions**
   - Generate completion scripts for bash/zsh/fish

5. **Remote Diagnostics**
   - `cuff doctor --remote` - Check HTTP plugin connectivity

---

## Appendix A: Full Command Reference

```
cuff - OpenCuff CLI

USAGE:
    cuff <COMMAND>

COMMANDS:
    init        Initialize settings.yml from discovered plugins
    status      Show status of configured plugins
    doctor      Diagnose issues and suggest fixes
    makefile    Makefile plugin commands
    packagejson Package.json plugin commands

GLOBAL OPTIONS:
    --config, -c <PATH>    Path to settings.yml
    --verbose, -v          Enable verbose output
    --quiet, -q            Suppress non-error output
    --no-color             Disable colored output
    --help, -h             Show help
    --version              Show version

EXAMPLES:
    cuff init                           # Create settings.yml
    cuff status                         # Check plugin status
    cuff makefile list-targets          # List make targets
    cuff makefile run-target test       # Run 'make test'
    cuff packagejson run-script build   # Run 'npm run build'
```

---

## Appendix B: pyproject.toml Entry Point

```toml
[project.scripts]
cuff = "opencuff.cli.main:main"
```
