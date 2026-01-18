# Package.json Plugin - High-Level Design Document

**Version:** 1.0
**Date:** 2026-01-18
**Status:** Draft

**Revision History:**

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-18 | Initial draft |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Script Extraction](#3-script-extraction)
4. [Package Manager Detection](#4-package-manager-detection)
5. [Tool Naming Convention](#5-tool-naming-convention)
6. [Tool Parameters](#6-tool-parameters)
7. [Caching Strategy](#7-caching-strategy)
8. [Security Considerations](#8-security-considerations)
9. [Configuration](#9-configuration)
10. [Error Handling](#10-error-handling)
11. [Observability](#11-observability)
12. [Plugin Implementation](#12-plugin-implementation)
13. [Example Usage](#13-example-usage)
14. [Testing Strategy](#14-testing-strategy)
15. [Future Considerations](#15-future-considerations)

---

## 1. Overview

### 1.1 Purpose

The package.json plugin extracts npm/pnpm scripts from `package.json` files and exposes them as MCP tools. This allows AI coding agents to discover and execute project scripts through governed, observable tool calls.

### 1.2 Goals

- **Script Discovery**: Automatically parse `package.json` to find all available scripts
- **Package Manager Flexibility**: Support npm, pnpm, and auto-detection based on lock files
- **Consistent Interface**: Follow the same patterns as the Makefile plugin
- **Security**: Validate scripts and sanitize arguments before execution
- **Performance**: Cache extracted scripts with content-hash invalidation

### 1.3 Non-Goals

- Parsing `package.json` dependencies or other metadata (only scripts)
- Supporting Yarn (may be added in future iterations)
- Running arbitrary node commands outside of defined scripts

## 2. Architecture

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Package.json Plugin                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐     ┌──────────────────────────────┐  │
│  │  ScriptExtractor │────▶│         ScriptCache          │  │
│  │                  │     │  (hash-based invalidation)   │  │
│  └────────┬─────────┘     └──────────────────────────────┘  │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    NpmScript                         │   │
│  │  - name: str                                         │   │
│  │  - command: str                                      │   │
│  │  - description: str | None                           │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              PackageManagerDetector                  │   │
│  │  - Detects npm/pnpm from lock files                  │   │
│  │  - Falls back to configured default                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                  ScriptExecutor                      │   │
│  │  - Runs scripts via subprocess                       │   │
│  │  - Handles timeout and environment                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
package.json ──▶ ScriptExtractor ──▶ NpmScript[] ──▶ ToolDefinition[]
                       │                                    │
                       ▼                                    ▼
                  ScriptCache                          MCP Server
                       │                                    │
                       ▼                                    ▼
              (content hash)                        Tool Invocation
                                                           │
                                                           ▼
                                                   ScriptExecutor
                                                           │
                                                           ▼
                                                    npm/pnpm run
```

## 3. Script Extraction

### 3.1 Extraction Strategy

Unlike Makefiles which require complex parsing (includes, variables, shell expansions), `package.json` files are straightforward JSON. A single extraction strategy is sufficient:

```python
class ScriptExtractor:
    """Extracts scripts from package.json files."""

    async def extract(self, package_json_path: Path) -> list[NpmScript]:
        """Parse package.json and extract scripts."""
        content = await asyncio.to_thread(package_json_path.read_text)
        data = json.loads(content)
        scripts = data.get("scripts", {})

        return [
            NpmScript(
                name=name,
                command=command,
                description=self._extract_description(name, data)
            )
            for name, command in scripts.items()
            if self._should_include(name)
        ]

    def _extract_description(self, name: str, data: dict) -> str | None:
        """Try to extract description from comments or metadata.

        Note: 'scripts-info' is an OpenCuff convention (not an npm standard).
        Projects can add a 'scripts-info' object to package.json to provide
        human-readable descriptions for their scripts.
        """
        scripts_info = data.get("scripts-info", {})
        return scripts_info.get(name)

    def _should_include(self, name: str) -> bool:
        """Filter scripts based on include/exclude patterns."""
        # Apply fnmatch patterns from config
        ...
```

### 3.2 Script Data Model

```python
@dataclass
class NpmScript:
    """Represents an npm/pnpm script."""
    name: str
    command: str
    description: str | None = None

    def to_tool_definition(self, package_manager: str) -> ToolDefinition:
        """Convert script to MCP tool definition."""
        tool_name = self._make_tool_name(package_manager)
        return ToolDefinition(
            name=tool_name,
            description=self._make_description(package_manager),
            parameters=self._make_parameters(),
            returns={"type": "object", ...}
        )

    def _make_tool_name(self, package_manager: str) -> str:
        """Generate tool name from script name."""
        # Sanitize: replace - with _, : with __
        sanitized = self.name.replace("-", "_").replace(":", "__")
        return f"{package_manager}_{sanitized}"
```

## 4. Package Manager Detection

### 4.1 Auto-Detection

The plugin auto-detects the package manager based on lock files:

| Lock File | Package Manager |
|-----------|-----------------|
| `pnpm-lock.yaml` | pnpm |
| `package-lock.json` | npm |
| `yarn.lock` | yarn (future) |
| None found | Use configured default |

**Lock File Precedence**: If multiple lock files exist (e.g., during a migration), the detection follows the order in the table above: `pnpm-lock.yaml` takes precedence over `package-lock.json`. To override auto-detection, explicitly set `package_manager` in the configuration.

```python
class PackageManagerDetector:
    """Detects the package manager to use."""

    LOCK_FILE_MAPPING = {
        "pnpm-lock.yaml": "pnpm",
        "package-lock.json": "npm",
    }

    def detect(self, working_directory: Path, default: str = "npm") -> str:
        """Auto-detect package manager from lock files."""
        for lock_file, manager in self.LOCK_FILE_MAPPING.items():
            if (working_directory / lock_file).exists():
                return manager
        return default
```

### 4.2 Configuration Override

Users can explicitly set the package manager in configuration:

```yaml
plugins:
  packagejson:
    config:
      package_manager: pnpm  # Override auto-detection
```

## 5. Tool Naming Convention

### 5.1 Naming Rules

Following the Makefile plugin pattern of `make_{target}`:

- Scripts become `{package_manager}_{script_name}`
- Special characters are sanitized for valid tool names:
  - `-` (hyphen) → `_` (underscore)
  - `:` (colon) → `__` (double underscore)
  - `.` (dot) → `_` (underscore)

### 5.2 Examples

| Script Name | Tool Name (npm) | Tool Name (pnpm) |
|-------------|-----------------|------------------|
| `test` | `npm_test` | `pnpm_test` |
| `build:prod` | `npm_build__prod` | `pnpm_build__prod` |
| `lint-fix` | `npm_lint_fix` | `pnpm_lint_fix` |
| `test:unit:ci` | `npm_test__unit__ci` | `pnpm_test__unit__ci` |

### 5.3 Plugin Manager Namespacing

When registered with the Plugin Manager, tools are further namespaced as `{plugin_name}.{tool_name}`. For example:

| Script | Local Tool Name | Fully Qualified Name |
|--------|-----------------|----------------------|
| `test` | `npm_test` | `packagejson.npm_test` |
| `build:prod` | `pnpm_build__prod` | `packagejson.pnpm_build__prod` |

This ensures no conflicts with tools from other plugins. The `call_plugin_tool` MCP endpoint uses the fully qualified name.

## 6. Tool Parameters

### 6.1 Parameter Schema

Each script tool accepts these parameters:

```python
SCRIPT_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "extra_args": {
            "type": "string",
            "description": "Additional arguments to pass to the script"
        },
        "timeout": {
            "type": "integer",
            "description": "Execution timeout in seconds",
            "minimum": 1
        },
        "env": {
            "type": "object",
            "description": "Additional environment variables",
            "additionalProperties": {"type": "string"}
        },
        "dry_run": {
            "type": "boolean",
            "default": False,
            "description": "Print command without executing"
        }
    }
}
```

### 6.2 Execution Examples

```python
# Simple execution
await call_tool("npm_test", {})
# Executes: npm run test

# With extra arguments
await call_tool("npm_test", {"extra_args": "--coverage --watch"})
# Executes: npm run test -- --coverage --watch

# With timeout
await call_tool("npm_build__prod", {"timeout": 600})
# Executes: npm run build:prod (with 10 minute timeout)

# Dry run
await call_tool("npm_deploy", {"dry_run": True})
# Returns: "Would execute: npm run deploy"
```

## 7. Caching Strategy

### 7.1 Cache Entry

Use content-hash based caching identical to Makefile plugin:

```python
@dataclass
class CacheEntry:
    """Cached script extraction result."""
    scripts: list[NpmScript]
    timestamp: float
    package_json_hash: str
    config_hash: str

    def is_valid(
        self,
        ttl: int,
        current_hash: str,
        current_config_hash: str
    ) -> bool:
        """Check if cache entry is still valid."""
        if ttl <= 0:
            return False
        if time.time() - self.timestamp >= ttl:
            return False
        if current_hash != self.package_json_hash:
            return False
        if current_config_hash != self.config_hash:
            return False
        return True
```

### 7.2 Cache Invalidation

The cache is invalidated when:

1. **TTL expires**: Configurable, default 300 seconds
2. **Content changes**: package.json file hash changes
3. **Config changes**: Plugin configuration hash changes
4. **Manual refresh**: Via `npm_list_scripts` with `refresh=True`

## 8. Security Considerations

> **WARNING**: Package.json scripts can execute arbitrary shell commands. Scripts have full access to the system, environment variables (which may contain secrets), and the filesystem. Before enabling this plugin, audit the `package.json` scripts in your project to ensure they do not perform destructive or sensitive operations when invoked by AI agents.

### 8.1 Script Name Validation

Validate script names against a safe pattern:

```python
SAFE_SCRIPT_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_:.-]*$")

def validate_script_name(name: str) -> bool:
    """Ensure script name is safe."""
    return bool(SAFE_SCRIPT_PATTERN.match(name))
```

### 8.2 Lifecycle Script Filtering

Optionally block internal npm lifecycle scripts that could be dangerous:

```python
LIFECYCLE_SCRIPTS = {
    "preinstall", "install", "postinstall",
    "preuninstall", "uninstall", "postuninstall",
    "prepublish", "prepare", "prepublishOnly",
    "prepack", "postpack",
}

# In config
exclude_lifecycle_scripts: bool = True
```

### 8.3 Argument Sanitization

Block shell metacharacters in extra arguments:

```python
DANGEROUS_CHARS = {";", "|", "&", "`", "$", "(", ")", "\n", "\r"}

def sanitize_arguments(args: str) -> list[str]:
    """Sanitize extra arguments and split into list."""
    for char in DANGEROUS_CHARS:
        if char in args:
            raise ValueError(f"Dangerous character in arguments: {char!r}")
    return shlex.split(args)
```

### 8.4 Package Manager Validation

```python
ALLOWED_PACKAGE_MANAGERS = {"npm", "pnpm"}

def validate_package_manager(manager: str) -> None:
    """Ensure package manager is allowed."""
    if manager not in ALLOWED_PACKAGE_MANAGERS:
        raise ValueError(f"Invalid package manager: {manager}")
```

## 9. Configuration

### 9.1 Configuration Schema

```yaml
plugins:
  packagejson:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.packagejson
    config:
      # Path to package.json (relative to working_directory or absolute)
      package_json_path: ./package.json

      # Package manager: npm, pnpm, or auto (default)
      package_manager: auto

      # Comma-separated fnmatch patterns for scripts to expose
      # Use * for all scripts
      scripts: "*"

      # Comma-separated fnmatch patterns for scripts to exclude
      exclude_scripts: ""

      # Exclude npm lifecycle scripts (pre/post install, etc.)
      exclude_lifecycle_scripts: true

      # Cache TTL in seconds (0 to disable caching)
      cache_ttl: 300

      # Working directory for script execution
      working_directory: .

      # Default timeout for script execution (seconds)
      default_timeout: 300

      # Additional environment variables for all scripts
      environment: {}

      # Expose a 'npm_list_scripts' / 'pnpm_list_scripts' tool
      expose_list_scripts: true
```

### 9.2 Pydantic Model

```python
class PackageJsonPluginConfig(BaseModel):
    """Configuration for package.json plugin."""

    package_json_path: str = "./package.json"
    package_manager: Literal["npm", "pnpm", "auto"] = "auto"
    scripts: str = "*"
    exclude_scripts: str = ""
    exclude_lifecycle_scripts: bool = True
    cache_ttl: int = Field(default=300, ge=0)
    working_directory: str = "."
    default_timeout: int = Field(default=300, ge=1)
    environment: dict[str, str] = Field(default_factory=dict)
    expose_list_scripts: bool = True

    @field_validator("package_manager")
    @classmethod
    def validate_package_manager(cls, v: str) -> str:
        if v not in ("npm", "pnpm", "auto"):
            raise ValueError(f"Invalid package_manager: {v}")
        return v
```

## 10. Error Handling

### 10.1 Error Codes

```python
class PackageJsonPluginErrorCode(str, Enum):
    """Error codes for package.json plugin operations."""

    # Initialization errors (1xx)
    PACKAGE_JSON_NOT_FOUND = "PKGJSON_101"
    INVALID_JSON = "PKGJSON_102"
    PACKAGE_MANAGER_NOT_FOUND = "PKGJSON_103"

    # Configuration errors (2xx)
    INVALID_CONFIG = "PKGJSON_201"
    INVALID_PACKAGE_MANAGER = "PKGJSON_202"
    INVALID_SCRIPT_PATTERN = "PKGJSON_203"

    # Execution errors (3xx)
    SCRIPT_NOT_FOUND = "PKGJSON_301"
    EXECUTION_FAILED = "PKGJSON_302"
    EXECUTION_TIMEOUT = "PKGJSON_303"
    ARGUMENT_VALIDATION_FAILED = "PKGJSON_304"

    # Runtime errors (4xx)
    PLUGIN_NOT_INITIALIZED = "PKGJSON_401"
    CACHE_ERROR = "PKGJSON_402"
```

### 10.2 Error Categories

| Error Type | Error Code | Handling |
|------------|------------|----------|
| `package.json` not found | `PKGJSON_101` | Return error in ToolResult, log warning |
| Invalid JSON | `PKGJSON_102` | Return error with parse details |
| Package manager not installed | `PKGJSON_103` | Return error suggesting installation |
| Script not found | `PKGJSON_301` | Return error listing available scripts |
| Execution timeout | `PKGJSON_303` | Kill process, return timeout error |
| Non-zero exit code | `PKGJSON_302` | Return stdout/stderr with exit code |
| Dangerous argument | `PKGJSON_304` | Return error identifying the issue |

### 10.3 Error Response Format

```python
@dataclass
class ToolResult:
    success: bool
    output: str | None = None
    error: str | None = None
    error_code: str | None = None  # PackageJsonPluginErrorCode value
    exit_code: int | None = None
    metadata: dict[str, Any] | None = None
```

### 10.4 Error Handling Flow

```
Tool Invocation
       │
       ▼
┌──────────────────┐
│ Plugin initialized? │──No──▶ Return PKGJSON_401
└────────┬─────────┘
         │ Yes
         ▼
┌──────────────────┐
│ Script exists?   │──No──▶ Return PKGJSON_301
└────────┬─────────┘
         │ Yes
         ▼
┌──────────────────┐
│ Validate args    │──Fail──▶ Return PKGJSON_304
└────────┬─────────┘
         │ Pass
         ▼
┌──────────────────┐
│ Execute script   │
└────────┬─────────┘
         │
    ┌────┴────┐
    │         │
 Success   Failure
    │         │
    ▼         ▼
 Return    Timeout? ──Yes──▶ Return PKGJSON_303
 output       │
              │ No
              ▼
         Return PKGJSON_302 with exit code
```

## 11. Observability

### 11.1 Metrics

The plugin exposes the following metrics for monitoring:

| Metric | Type | Description |
|--------|------|-------------|
| `packagejson_extraction_duration_seconds` | Histogram | Time to parse package.json |
| `packagejson_cache_hits_total` | Counter | Number of cache hits |
| `packagejson_cache_misses_total` | Counter | Number of cache misses |
| `packagejson_script_execution_duration_seconds` | Histogram | Script execution time |
| `packagejson_script_executions_total` | Counter | Total script executions |
| `packagejson_script_failures_total` | Counter | Failed script executions |
| `packagejson_scripts_discovered` | Gauge | Number of discovered scripts |

### 11.2 Structured Logging

All log entries include these standard fields:

```python
@dataclass
class LogContext:
    """Standard log context for package.json plugin."""
    plugin_name: str = "packagejson"
    instance_name: str = ""
    package_json_path: str = ""
    package_manager: str = ""
    correlation_id: str | None = None
```

Example log entries:

```json
{
  "level": "INFO",
  "message": "Scripts extracted from package.json",
  "plugin_name": "packagejson",
  "package_json_path": "./package.json",
  "package_manager": "pnpm",
  "script_count": 10,
  "duration_ms": 5.2,
  "cache_hit": false
}
```

```json
{
  "level": "ERROR",
  "message": "Script execution failed",
  "plugin_name": "packagejson",
  "script_name": "build",
  "error_code": "PKGJSON_302",
  "exit_code": 1,
  "duration_ms": 15234
}
```

### 11.3 Health Checks

The plugin provides health check methods for monitoring:

```python
async def health_check(self) -> bool:
    """Quick health check - returns True if plugin is operational."""
    if not self._initialized:
        return False
    if not self._package_json_path.exists():
        return False
    return True

async def detailed_health_check(self) -> dict[str, Any]:
    """Detailed health check with diagnostics."""
    return {
        "healthy": await self.health_check(),
        "initialized": self._initialized,
        "package_json_exists": self._package_json_path.exists(),
        "package_manager": self._package_manager,
        "script_count": len(self._scripts),
        "cache_valid": self._cache.is_valid(),
        "last_extraction": self._cache.last_extraction_time,
    }
```

## 12. Plugin Implementation

### 12.1 Class Structure

```python
class Plugin(InSourcePlugin):
    """Package.json plugin exposing npm/pnpm scripts as MCP tools."""

    def __init__(
        self,
        config: dict[str, Any],
        instance_name: str = "packagejson",
        cache: ScriptCache | None = None,
    ) -> None:
        super().__init__(config)
        self._plugin_config = PackageJsonPluginConfig.model_validate(config)
        self._instance_name = instance_name
        self._scripts: list[NpmScript] = []
        self._tool_to_script: dict[str, str] = {}
        self._cache = cache if cache is not None else ScriptCache()
        self._package_manager: str | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize plugin and discover scripts."""
        await self._detect_package_manager()
        await self._refresh_scripts()
        self._initialized = True

    async def shutdown(self) -> None:
        """Clean up plugin resources."""
        self._cache.invalidate()
        self._scripts = []
        self._tool_to_script = {}
        self._initialized = False

    def get_tools(self) -> list[ToolDefinition]:
        """Return tool definitions for discovered scripts."""
        tools = []

        if self._plugin_config.expose_list_scripts:
            tools.append(self._make_list_scripts_tool())

        for script in self._scripts:
            tool_def = script.to_tool_definition(self._package_manager)
            self._tool_to_script[tool_def.name] = script.name
            tools.append(tool_def)

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute the requested npm script."""
        if not self._initialized:
            return ToolResult(success=False, error="Plugin not initialized")

        if tool_name == f"{self._package_manager}_list_scripts":
            return await self._list_scripts(arguments)

        script_name = self._tool_to_script.get(tool_name)
        if script_name is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}"
            )

        return await self._execute_script(script_name, arguments)

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration reload (hot reload support)."""
        old_config = self._plugin_config
        self._plugin_config = PackageJsonPluginConfig.model_validate(new_config)

        # Check if we need to re-detect package manager
        if old_config.package_manager != self._plugin_config.package_manager:
            await self._detect_package_manager()

        # Check if paths changed
        path_changed = (
            old_config.package_json_path != self._plugin_config.package_json_path
            or old_config.working_directory != self._plugin_config.working_directory
        )

        # Check if filter patterns changed
        patterns_changed = (
            old_config.scripts != self._plugin_config.scripts
            or old_config.exclude_scripts != self._plugin_config.exclude_scripts
        )

        if path_changed or patterns_changed:
            self._cache.invalidate()
            await self._refresh_scripts()

    async def health_check(self) -> bool:
        """Quick health check - returns True if plugin is operational."""
        if not self._initialized:
            return False
        package_json_path = Path(self._plugin_config.package_json_path)
        if not package_json_path.exists():
            return False
        return True

    async def detailed_health_check(self) -> dict[str, Any]:
        """Detailed health check with diagnostics."""
        return {
            "healthy": await self.health_check(),
            "initialized": self._initialized,
            "package_json_exists": Path(self._plugin_config.package_json_path).exists(),
            "package_manager": self._package_manager,
            "script_count": len(self._scripts),
            "cache_valid": self._cache.is_valid() if self._cache else False,
            "last_extraction": getattr(self._cache, "last_extraction_time", None),
        }
```

### 12.2 Script Execution

```python
async def _execute_script(
    self,
    script_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Execute an npm/pnpm script."""
    extra_args = arguments.get("extra_args", "")
    timeout = arguments.get("timeout", self._plugin_config.default_timeout)
    env = arguments.get("env", {})
    dry_run = arguments.get("dry_run", False)

    # Build command
    cmd = [self._package_manager, "run", script_name]
    if extra_args:
        sanitized = sanitize_arguments(extra_args)
        cmd.extend(["--", *sanitized])

    if dry_run:
        return ToolResult(
            success=True,
            output=f"Would execute: {' '.join(cmd)}"
        )

    # Build environment
    full_env = {
        **os.environ,
        **self._plugin_config.environment,
        **env,
    }

    # Execute
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._plugin_config.working_directory,
            env=full_env,
        )

        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )

        return ToolResult(
            success=process.returncode == 0,
            output=stdout.decode(),
            exit_code=process.returncode,
        )

    except asyncio.TimeoutError:
        process.kill()
        return ToolResult(
            success=False,
            error=f"Script timed out after {timeout} seconds"
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=str(e)
        )
```

## 13. Example Usage

### 13.1 Example package.json

```json
{
  "name": "my-project",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "build:prod": "NODE_ENV=production vite build",
    "test": "vitest",
    "test:unit": "vitest run",
    "test:e2e": "playwright test",
    "lint": "eslint .",
    "lint:fix": "eslint . --fix",
    "format": "prettier --write .",
    "typecheck": "tsc --noEmit"
  },
  "scripts-info": {
    "dev": "Start development server with hot reload",
    "build:prod": "Build for production with optimizations"
  }
}
```

### 13.2 Generated Tools

With the above package.json and auto-detected `pnpm`:

| Tool Name | Description |
|-----------|-------------|
| `pnpm_list_scripts` | List all available pnpm scripts |
| `pnpm_dev` | Start development server with hot reload |
| `pnpm_build` | Run build script |
| `pnpm_build__prod` | Build for production with optimizations |
| `pnpm_test` | Run test script |
| `pnpm_test__unit` | Run test:unit script |
| `pnpm_test__e2e` | Run test:e2e script |
| `pnpm_lint` | Run lint script |
| `pnpm_lint_fix` | Run lint:fix script |
| `pnpm_format` | Run format script |
| `pnpm_typecheck` | Run typecheck script |

### 13.3 Tool Invocation Examples

```python
# List all scripts
result = await call_tool("pnpm_list_scripts", {})
# Returns: {"scripts": ["dev", "build", "build:prod", ...]}

# Run tests with coverage
result = await call_tool("pnpm_test", {"extra_args": "--coverage"})
# Executes: pnpm run test -- --coverage

# Build with custom environment
result = await call_tool("pnpm_build__prod", {
    "env": {"VITE_API_URL": "https://api.example.com"},
    "timeout": 600
})

# Dry run to see command
result = await call_tool("pnpm_lint_fix", {"dry_run": True})
# Returns: "Would execute: pnpm run lint:fix"
```

## 14. Testing Strategy

### 14.1 Test Categories

1. **Unit Tests**: Script extraction, name sanitization, config validation
2. **Integration Tests**: Full plugin lifecycle with mock package.json
3. **Cache Tests**: TTL expiration, hash invalidation
4. **Security Tests**: Argument sanitization, script validation
5. **Execution Tests**: Success, failure, timeout scenarios

### 14.2 Test Fixtures

Create test fixtures in `tests/fixtures/package_json/`:

```
tests/fixtures/package_json/
├── simple/
│   └── package.json          # Basic scripts
├── complex/
│   └── package.json          # Nested scripts with colons
├── lifecycle/
│   └── package.json          # Contains lifecycle scripts
├── with_pnpm/
│   ├── package.json
│   └── pnpm-lock.yaml
└── with_npm/
    ├── package.json
    └── package-lock.json
```

## 15. Future Considerations

- **Yarn Support**: Add detection for `yarn.lock` and yarn execution
- **Bun Support**: Add detection for `bun.lockb` and bun execution
- **Workspace Support**: Handle monorepo workspaces with multiple package.json files
- **Script Dependencies**: Parse and expose script dependency graph
- **Parallel Execution**: Support running multiple scripts in parallel
