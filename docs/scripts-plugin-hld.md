# Scripts Plugin - High-Level Design

**Version:** 1.1
**Date:** 2026-01-18
**Status:** Reviewed

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Configuration](#configuration)
4. [Tool Generation](#tool-generation)
5. [Script Execution](#script-execution)
6. [Discovery Interface](#discovery-interface)
7. [Security Considerations](#security-considerations)
8. [Error Handling](#error-handling)
9. [Examples](#examples)

---

## Executive Summary

The Scripts Plugin exposes shell scripts as MCP tools based on configurable glob patterns. This allows organizations to provide controlled access to a curated set of scripts for AI coding agents, without exposing arbitrary command execution.

**Key Features:**
- Glob-based script selection (e.g., `scripts/*.sh`, `build/*.sh`)
- Automatic tool generation from matched scripts
- Support for multiple script types (shell, Python, etc.)
- Configurable execution parameters (timeout, working directory, environment)
- Discovery support for `cuff init`

---

## Goals and Non-Goals

### Goals

- **Controlled Access**: Only scripts matching configured patterns are exposed
- **Discoverability**: Automatically find scripts during `cuff init`
- **Flexibility**: Support various script types and execution modes
- **Safety**: Prevent path traversal and unauthorized script execution
- **Usability**: Generate meaningful tool names and descriptions from scripts

### Non-Goals

- Interactive script execution (scripts must be non-interactive)
- Script editing or creation via the plugin
- Remote script execution
- Script argument templating or complex parameter passing

---

## Configuration

### Settings Schema

```yaml
plugins:
  scripts:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.scripts
    config:
      # Glob patterns for scripts to expose (required)
      patterns:
        - "scripts/*.sh"
        - "scripts/*.py"
        - "build/deploy.sh"
        - "tools/**/*.sh"

      # Base directory for resolving patterns (default: ".")
      base_directory: "."

      # Scripts to exclude (optional glob patterns)
      exclude:
        - "scripts/internal_*.sh"
        - "**/*_test.sh"

      # Default timeout in seconds (default: 300)
      default_timeout: 300

      # Working directory for script execution (default: base_directory)
      working_directory: "."

      # Environment variables to pass to scripts (optional)
      environment:
        CI: "true"
        VERBOSE: "1"

      # Whether to expose a list_scripts tool (default: true)
      expose_list_scripts: true

      # Script interpreter mapping (optional, auto-detected by default)
      interpreters:
        ".sh": "/bin/bash"
        ".py": "python3"
        ".rb": "ruby"

      # Whether to require scripts to be executable (default: false)
      require_executable: false

      # Cache TTL for script discovery in seconds (default: 300)
      cache_ttl: 300
```

### Configuration Details

#### `patterns` (required)
List of glob patterns to match scripts. Patterns are relative to `base_directory`.

Supported glob syntax:
- `*` - Match any characters except path separator
- `**` - Match any characters including path separator (recursive)
- `?` - Match single character
- `[abc]` - Match character class

#### `exclude` (optional)
List of glob patterns to exclude from matched scripts. Applied after `patterns` matching.

#### `interpreters` (optional)
Mapping of file extensions to interpreter commands. If not specified, the plugin uses:
1. The script's shebang line (e.g., `#!/bin/bash`)
2. Default interpreters based on extension
3. Direct execution if the script is executable

---

## Tool Generation

### Tool Naming

Tools are named based on the script's relative path with the following transformations:

1. Remove the file extension
2. Replace path separators with underscores
3. Replace hyphens and dots with underscores
4. Prefix with `script_`

**Examples:**

| Script Path | Tool Name |
|------------|-----------|
| `scripts/build.sh` | `script_scripts_build` |
| `scripts/deploy-prod.sh` | `script_scripts_deploy_prod` |
| `tools/db/migrate.py` | `script_tools_db_migrate` |
| `build.sh` | `script_build` |

### Tool Description

Tool descriptions are generated from:

1. **Primary**: First comment block in the script (lines starting with `#` after shebang)
2. **Fallback**: `"Run {script_path}"`

**Example script with description:**

```bash
#!/bin/bash
# Deploy the application to production
#
# This script handles the full deployment process including
# building, testing, and releasing to production servers.

set -e
# ... script content
```

Generated description: `"Deploy the application to production"`

### Tool Parameters

Each script tool accepts the following parameters:

```json
{
  "type": "object",
  "properties": {
    "args": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Arguments to pass to the script"
    },
    "timeout": {
      "type": "integer",
      "description": "Execution timeout in seconds (overrides default)"
    },
    "env": {
      "type": "object",
      "additionalProperties": {"type": "string"},
      "description": "Additional environment variables"
    }
  }
}
```

### List Scripts Tool

When `expose_list_scripts` is true, an additional tool is exposed:

**Name:** `script_list_scripts`

**Description:** `"List all available scripts"`

**Returns:**
```json
{
  "scripts": [
    {
      "name": "script_scripts_build",
      "path": "scripts/build.sh",
      "description": "Build the project",
      "interpreter": "/bin/bash"
    }
  ]
}
```

---

## Script Execution

### Execution Flow

```
┌─────────────────┐
│  Tool Invoked   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Validate Script │──── Script not in allowed set ──► Error
│    Path         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Determine     │
│  Interpreter    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Build Command   │
│  & Environment  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Execute      │
│   Subprocess    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Capture Output  │
│  & Exit Code    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Return Result   │
└─────────────────┘
```

### Interpreter Detection

Order of precedence:

1. **Configured interpreter**: Check `interpreters` config for file extension
2. **Shebang line**: Parse `#!` line from script (e.g., `#!/usr/bin/env python3`)
3. **Extension defaults**:
   - `.sh` → `/bin/sh`
   - `.bash` → `/bin/bash`
   - `.py` → `python3`
   - `.rb` → `ruby`
   - `.js` → `node`
   - `.pl` → `perl`
4. **Direct execution**: If `require_executable` is true and script is executable

### Process Execution

```python
async def execute_script(
    self,
    script_path: Path,
    args: list[str],
    timeout: int,
    env: dict[str, str],
    working_directory: Path,
    interpreter: str | None,
) -> ToolResult:
    """Execute a script and return the result."""

    # Sanitize arguments (raises ValueError if dangerous chars found)
    sanitized_args = self._sanitize_args(args)

    # Validate environment variables (raises ValueError if blocked vars found)
    validated_env = self._validate_env(env)

    # Build command
    if interpreter:
        cmd = [interpreter, str(script_path)] + sanitized_args
    else:
        cmd = [str(script_path)] + sanitized_args

    # Merge environment (config env first, then user env)
    process_env = os.environ.copy()
    process_env.update(self.config.environment)
    process_env.update(validated_env)

    # Execute
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_directory,
        env=process_env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        return ToolResult(
            success=False,
            error=f"Script timed out after {timeout} seconds",
        )

    return ToolResult(
        success=process.returncode == 0,
        data={
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
        },
        error=f"Script failed with exit code {process.returncode}"
              if process.returncode != 0 else None,
    )
```

---

## Discovery Interface

### Discovery Method

```python
@classmethod
def discover(cls, directory: Path) -> DiscoveryResult:
    """Discover scripts in the directory.

    Scans for common script patterns and returns suggested configuration.
    """
    # Common script locations to check
    script_patterns = [
        "scripts/*.sh",
        "scripts/*.py",
        "bin/*.sh",
        "tools/*.sh",
        "*.sh",  # Root-level scripts
    ]

    discovered_scripts: list[Path] = []
    matched_patterns: list[str] = []

    for pattern in script_patterns:
        matches = list(directory.glob(pattern))
        if matches:
            discovered_scripts.extend(matches)
            matched_patterns.append(pattern)

    if not discovered_scripts:
        return DiscoveryResult(
            applicable=False,
            confidence=0.0,
            suggested_config={},
            description="No scripts found",
        )

    # Build tool names for discovered_items
    tool_names = ["script_list_scripts"]
    for script in discovered_scripts:
        rel_path = script.relative_to(directory)
        tool_name = cls._path_to_tool_name(rel_path)
        tool_names.append(tool_name)

    return DiscoveryResult(
        applicable=True,
        confidence=0.8,  # Lower confidence than Makefile/package.json
        suggested_config={
            "patterns": matched_patterns,
            "base_directory": ".",
            "exclude": [],
            "default_timeout": 300,
            "working_directory": ".",
            "expose_list_scripts": True,
            "cache_ttl": 300,
        },
        description=f"Found {len(discovered_scripts)} scripts",
        discovered_items=tool_names,
        warnings=_generate_warnings(discovered_scripts),
    )

@staticmethod
def _path_to_tool_name(path: Path) -> str:
    """Convert a script path to a tool name."""
    # Remove extension
    name = path.with_suffix("").as_posix()
    # Replace separators and special chars
    name = name.replace("/", "_").replace("-", "_").replace(".", "_")
    return f"script_{name}"
```

### Discovery Warnings

The discovery method generates warnings for potential issues:

```python
import os
import stat

def _generate_warnings(scripts: list[Path]) -> list[str]:
    """Generate warnings about discovered scripts."""
    warnings = []

    for script in scripts:
        # Check for potentially sensitive scripts
        name_lower = script.name.lower()
        if any(w in name_lower for w in ["secret", "password", "credential", "key"]):
            warnings.append(
                f"Script '{script}' may contain sensitive operations - review before enabling"
            )

        # Check for scripts without shebang
        try:
            content = script.read_bytes()

            # Check if file is binary (non-text)
            if b'\x00' in content[:8192]:
                warnings.append(
                    f"Script '{script}' appears to be binary - verify this is intentional"
                )
                continue  # Skip text-based checks for binary files

            text_content = content.decode("utf-8", errors="replace")
            first_line = text_content.split("\n")[0]
            if not first_line.startswith("#!"):
                warnings.append(
                    f"Script '{script}' has no shebang line - interpreter will be guessed"
                )
        except Exception:
            pass

        # Check for world-writable scripts (security risk)
        try:
            mode = script.stat().st_mode
            if mode & stat.S_IWOTH:
                warnings.append(
                    f"SECURITY: Script '{script}' is world-writable - "
                    "this allows any user to modify the script"
                )
        except Exception:
            pass

        # Check for symlinks that might escape the base directory
        if script.is_symlink():
            warnings.append(
                f"Script '{script}' is a symlink - target will be validated at runtime"
            )

    return warnings
```

### CLI Commands

The scripts plugin exposes CLI commands for direct interaction:

```python
@classmethod
def get_cli_commands(cls) -> list[CLICommand]:
    return [
        CLICommand(
            name="list",
            help="List scripts matching configured patterns",
            callback=cls._cli_list_scripts,
            options=[
                CLIOption(
                    name="--pattern",
                    help="Glob pattern to match (default: scripts/*.sh)",
                    default="scripts/*.sh",
                ),
            ],
        ),
        CLICommand(
            name="run",
            help="Run a specific script",
            callback=cls._cli_run_script,
            arguments=[
                CLIArgument(
                    name="script",
                    help="Path to the script to run",
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
                    type=int,
                ),
            ],
        ),
    ]
```

---

## Security Considerations

### Path Validation

All script paths are validated to prevent path traversal attacks:

```python
def _validate_script_path(self, script_path: str) -> Path:
    """Validate and resolve a script path.

    Raises:
        ValueError: If the path is invalid or not allowed.
    """
    # Resolve the path
    base = Path(self.config.base_directory).resolve()
    full_path = (base / script_path).resolve()

    # Ensure path is within base directory
    try:
        full_path.relative_to(base)
    except ValueError:
        raise ValueError(f"Path traversal detected: {script_path}")

    # Check if path matches any allowed pattern
    if not self._matches_allowed_patterns(full_path):
        raise ValueError(f"Script not in allowed patterns: {script_path}")

    # Check if path is excluded
    if self._matches_exclude_patterns(full_path):
        raise ValueError(f"Script is excluded: {script_path}")

    # Verify file exists and is a file
    if not full_path.is_file():
        raise ValueError(f"Script not found: {script_path}")

    return full_path
```

### Argument Sanitization

Arguments passed to scripts are sanitized to prevent command injection:

```python
# Characters that could enable shell injection if passed to scripts
DANGEROUS_CHARS = frozenset(';&|`$(){}[]<>\\\'\"!*?~')

def _sanitize_args(self, args: list[str]) -> list[str]:
    """Sanitize arguments to prevent injection attacks.

    Raises:
        ValueError: If an argument contains dangerous characters.
    """
    sanitized = []
    for arg in args:
        # Check for dangerous characters
        dangerous_found = DANGEROUS_CHARS.intersection(arg)
        if dangerous_found:
            raise ValueError(
                f"Argument contains dangerous characters: {dangerous_found}"
            )
        sanitized.append(arg)
    return sanitized
```

### Blocked Environment Variables

Certain environment variables are blocked to prevent privilege escalation:

```python
# Environment variables that should never be overridden
BLOCKED_ENV_VARS = frozenset({
    "PATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",  # macOS equivalent of LD_PRELOAD
    "DYLD_LIBRARY_PATH",       # macOS library path
    "PYTHONPATH",              # Can affect Python script execution
    "NODE_PATH",               # Can affect Node.js script execution
    "RUBYLIB",                 # Can affect Ruby script execution
    "PERL5LIB",                # Can affect Perl script execution
    "HOME",                    # Can affect config file locations
    "USER",                    # Identity spoofing
    "SHELL",                   # Shell override
})

def _validate_env(self, env: dict[str, str]) -> dict[str, str]:
    """Validate environment variables.

    Raises:
        ValueError: If a blocked environment variable is provided.
    """
    blocked_found = BLOCKED_ENV_VARS.intersection(env.keys())
    if blocked_found:
        raise ValueError(
            f"Blocked environment variables: {blocked_found}"
        )
    return env
```

### Symlink Handling

Symlinks are resolved and validated to prevent escaping the base directory:

```python
def _validate_script_path(self, script_path: str) -> Path:
    """Validate and resolve a script path, handling symlinks safely."""
    base = Path(self.config.base_directory).resolve()
    full_path = (base / script_path).resolve()  # resolve() follows symlinks

    # After symlink resolution, verify still within base
    try:
        full_path.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Script resolves outside base directory (symlink escape): {script_path}"
        )

    # ... rest of validation
```

### Security Table

| Threat | Mitigation |
|--------|------------|
| Path traversal | Paths resolved and validated against base directory |
| Symlink escape | Symlinks resolved and target validated against base directory |
| Unauthorized scripts | Only scripts matching configured patterns are exposed |
| Command injection via args | Arguments sanitized with DANGEROUS_CHARS check |
| Environment variable injection | BLOCKED_ENV_VARS prevents PATH/LD_PRELOAD override |
| Infinite execution | Configurable timeout with kill on timeout |
| Sensitive script exposure | Exclude patterns and discovery warnings |
| World-writable scripts | Discovery warns about insecure permissions |

### Recommendations

1. **Use specific patterns**: Prefer `scripts/deploy.sh` over `scripts/*.sh` for sensitive operations
2. **Review discovery warnings**: Check warnings from `cuff init` before enabling, especially SECURITY warnings
3. **Set appropriate timeouts**: Configure timeouts based on expected script duration
4. **Use exclude patterns**: Exclude test scripts, internal tools, and sensitive scripts
5. **Limit arguments**: Consider disabling args for scripts that don't need them
6. **Fix world-writable scripts**: Run `chmod o-w <script>` on any scripts flagged as world-writable
7. **Avoid symlinks**: Use direct paths when possible; symlinks add complexity and risk
8. **Review blocked env vars**: If scripts need PATH modifications, do it within the script itself

---

## Error Handling

### Error Types

| Error | Cause | Result |
|-------|-------|--------|
| Script not found | Path doesn't exist | `ToolResult(success=False, error="Script not found: {path}")` |
| Permission denied | Script not executable (when required) | `ToolResult(success=False, error="Permission denied: {path}")` |
| Timeout | Script exceeds timeout | `ToolResult(success=False, error="Script timed out after {n} seconds")` |
| Non-zero exit | Script returns non-zero | `ToolResult(success=False, data={stdout, stderr, exit_code}, error="Script failed with exit code {n}")` |
| Pattern mismatch | Script not in allowed patterns | `ToolResult(success=False, error="Script not in allowed patterns: {path}")` |
| Dangerous arguments | Arguments contain shell metacharacters | `ToolResult(success=False, error="Argument contains dangerous characters: {chars}")` |
| Blocked env var | User attempts to set PATH, LD_PRELOAD, etc. | `ToolResult(success=False, error="Blocked environment variables: {vars}")` |
| Symlink escape | Symlink target outside base directory | `ToolResult(success=False, error="Script resolves outside base directory: {path}")` |

### Logging

The plugin logs execution details for debugging and auditing:

```python
logger.info("Executing script", extra={
    "script": str(script_path),
    "args": args,
    "timeout": timeout,
    "working_directory": str(working_directory),
})

logger.info("Script completed", extra={
    "script": str(script_path),
    "exit_code": result.returncode,
    "duration_seconds": duration,
})
```

---

## Examples

### Example 1: Basic Setup

```yaml
# settings.yml
plugins:
  scripts:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.scripts
    config:
      patterns:
        - "scripts/*.sh"
```

**Project structure:**
```
myproject/
├── scripts/
│   ├── build.sh
│   ├── test.sh
│   └── deploy.sh
└── settings.yml
```

**Exposed tools:**
- `script_list_scripts`
- `script_scripts_build`
- `script_scripts_test`
- `script_scripts_deploy`

### Example 2: Multi-Directory with Exclusions

```yaml
plugins:
  scripts:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.scripts
    config:
      patterns:
        - "scripts/**/*.sh"
        - "tools/**/*.py"
        - "bin/*"
      exclude:
        - "**/*_test.sh"
        - "**/internal_*"
      default_timeout: 600
      environment:
        LOG_LEVEL: "info"
```

### Example 3: Python Scripts with Custom Interpreter

```yaml
plugins:
  scripts:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.scripts
    config:
      patterns:
        - "scripts/*.py"
      interpreters:
        ".py": "/usr/bin/python3.11"
      environment:
        PYTHONPATH: "./src"
```

### Example 4: Discovery Output

```
$ cuff init

Discovering plugins...
  [+] makefile: Found Makefile with 12 targets
  [+] packagejson: Found package.json with 5 scripts (npm)
  [+] scripts: Found 8 scripts
      Warning: Script 'scripts/deploy_secrets.sh' may contain sensitive operations
      Warning: Script 'tools/backup.sh' has no shebang line

Generated settings.yml with 3 plugins
```

---

## Appendix A: Supported Script Types

| Extension | Default Interpreter | Notes |
|-----------|-------------------|-------|
| `.sh` | `/bin/sh` | POSIX shell |
| `.bash` | `/bin/bash` | Bash shell |
| `.zsh` | `/bin/zsh` | Zsh shell |
| `.py` | `python3` | Python 3 |
| `.rb` | `ruby` | Ruby |
| `.js` | `node` | Node.js |
| `.ts` | `npx ts-node` | TypeScript (requires ts-node) |
| `.pl` | `perl` | Perl |
| `.php` | `php` | PHP |
| (none) | Direct execution | Must be executable |

---

## Appendix B: Tool Name Examples

| Script Path | Tool Name |
|------------|-----------|
| `build.sh` | `script_build` |
| `scripts/build.sh` | `script_scripts_build` |
| `scripts/ci/test.sh` | `script_scripts_ci_test` |
| `tools/db-migrate.py` | `script_tools_db_migrate` |
| `bin/run_server` | `script_bin_run_server` |
| `scripts/build.prod.sh` | `script_scripts_build_prod` |
