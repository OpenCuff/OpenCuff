# OpenCuff Plugin System - High-Level Design

**Version:** 1.1
**Date:** 2026-01-18
**Status:** Draft

**Revision History:**
| Version | Date | Changes |
|---------|------|---------|
| 1.1 | 2026-01-18 | Added request barrier for live reload, tool namespacing, HTTP re-initialization, health check scheduling, stderr handling, fixed naming consistency |
| 1.0 | 2026-01-18 | Initial draft |

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Architecture Overview](#architecture-overview)
4. [Plugin Types](#plugin-types)
5. [Configuration Schema](#configuration-schema)
6. [Plugin Interface Definitions](#plugin-interface-definitions)
7. [Plugin Lifecycle](#plugin-lifecycle)
8. [Live Reload Mechanism](#live-reload-mechanism)
9. [Example Plugin Implementations](#example-plugin-implementations)
10. [Security Considerations](#security-considerations)
11. [Error Handling](#error-handling)
12. [Future Considerations](#future-considerations)

---

## Executive Summary

This document describes the plugin architecture for OpenCuff, an MCP (Model Context Protocol) server that provides governed access to system commands for AI coding agents. The plugin system enables extensibility while maintaining the core principle of keeping the "bare metal" version fast by loading only essential components.

The architecture supports three plugin types:
- **In-source plugins**: Python modules within the opencuff package, loaded via importlib
- **Process plugins**: Standalone executables communicating via JSON over stdin/stdout
- **HTTP plugins**: Remote services accessed via HTTP with JSON payloads

All plugin types support live reload without server restart, enabling zero-downtime configuration changes.

---

## Goals and Non-Goals

### Goals

- **Minimal Core Footprint**: The base OpenCuff server loads no plugins by default, ensuring fast startup and low memory usage
- **Simple Plugin Development**: Developers can create plugins with minimal boilerplate
- **Live Reload Support**: Plugin configuration changes take effect without restarting the server
- **Type Safety**: Configuration and plugin interfaces are fully typed using Pydantic
- **Uniform Interface**: All plugin types expose tools through a consistent API
- **Security by Default**: Plugins operate within defined security boundaries

### Non-Goals

- Plugin marketplace or registry (out of scope for v1)
- Plugin sandboxing at the OS level (plugins are trusted code)
- Automatic dependency resolution for plugins
- Plugin versioning or compatibility management
- GUI for plugin management

---

## Architecture Overview

### System Context Diagram

```
+------------------+     MCP Protocol      +------------------+
|                  |<--------------------->|                  |
|   AI Coding      |                       |    OpenCuff      |
|   Agent          |                       |    MCP Server    |
|  (Claude, etc.)  |                       |                  |
+------------------+                       +--------+---------+
                                                    |
                                                    v
                                           +--------+---------+
                                           |  Plugin Manager  |
                                           +--------+---------+
                                                    |
                    +-------------------------------+-------------------------------+
                    |                               |                               |
                    v                               v                               v
           +--------+--------+             +--------+--------+             +--------+--------+
           |   In-Source     |             |    Process      |             |      HTTP       |
           |   Plugins       |             |    Plugins      |             |    Plugins      |
           |  (importlib)    |             | (subprocess)    |             |   (httpx)       |
           +-----------------+             +-----------------+             +-----------------+
```

### Component Architecture

```
+-----------------------------------------------------------------------------------+
|                                  OpenCuff Server                                  |
|                                                                                   |
|  +-------------+    +------------------+    +------------------+                  |
|  |   FastMCP   |<-->|  Tool Registry   |<-->|  Plugin Manager  |                  |
|  |   (mcp)     |    |                  |    |                  |                  |
|  +-------------+    +------------------+    +--------+---------+                  |
|                                                      |                            |
|                                             +--------+---------+                  |
|                                             | Config Watcher   |                  |
|                                             | (watchfiles)     |                  |
|                                             +--------+---------+                  |
|                                                      |                            |
|  +-------------------------------------------------------------------------------+|
|  |                             Plugin Adapters                                   ||
|  |  +------------------+  +------------------+  +------------------+             ||
|  |  | InSourceAdapter  |  | ProcessAdapter   |  |   HTTPAdapter    |             ||
|  |  |                  |  |                  |  |                  |             ||
|  |  | - load_module()  |  | - spawn_proc()   |  | - http_client    |             ||
|  |  | - get_tools()    |  | - send_request() |  | - call_tool()    |             ||
|  |  | - call_tool()    |  | - recv_response()|  | - health_check() |             ||
|  |  +------------------+  +------------------+  +------------------+             ||
|  +-------------------------------------------------------------------------------+|
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

### Data Flow

```
                    settings.yml change detected
                              |
                              v
                    +-------------------+
                    | Config Watcher    |
                    +--------+----------+
                             |
                             v
                    +-------------------+
                    | Parse & Validate  |
                    | (Pydantic)        |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
        +----------+   +----------+   +----------+
        | Unload   |   |  Reload  |   |   Load   |
        | Removed  |   | Changed  |   |   New    |
        +----------+   +----------+   +----------+
              |              |              |
              +--------------+--------------+
                             |
                             v
                    +-------------------+
                    | Update Tool       |
                    | Registry          |
                    +-------------------+
                             |
                             v
                    +-------------------+
                    | FastMCP reflects  |
                    | new tools         |
                    +-------------------+
```

---

## Plugin Types

### 1. In-Source Plugins

In-source plugins are Python modules that live within the `opencuff` package structure. They are loaded dynamically using `importlib` when specified in configuration.

**Location**: `src/opencuff/plugins/<plugin_name>/`

**Characteristics**:
- Fastest execution (no IPC overhead)
- Full access to Python ecosystem
- Shares process memory with OpenCuff core
- Best for core functionality extensions

**Directory Structure**:
```
src/opencuff/plugins/
    __init__.py
    makefile/
        __init__.py
        plugin.py        # Main plugin implementation
        config.py        # Plugin-specific configuration
    git/
        __init__.py
        plugin.py
        config.py
```

### 2. Process Plugins

Process plugins are standalone executables that communicate with OpenCuff via JSON messages over stdin/stdout. They run as child processes managed by the plugin system.

**Location**: Any executable path specified in configuration

**Characteristics**:
- Language-agnostic (Python, Rust, Go, Node.js, etc.)
- Process isolation (crash doesn't affect OpenCuff)
- Higher latency due to IPC
- Can maintain state across calls

**Protocol**:
```
OpenCuff                              Plugin Process
   |                                        |
   |  ---- JSON Request (stdin) --->        |
   |                                        |
   |  <--- JSON Response (stdout) ----      |
   |                                        |
```

**Stderr Handling:**

- Stderr from process plugins is captured and logged at DEBUG level
- Plugins SHOULD NOT write to stderr during normal operation
- Stderr output is intended for debugging and diagnostic purposes only
- Excessive stderr output may indicate a plugin issue and should be investigated

```python
class ProcessAdapter:
    """Adapter for process plugins with stderr handling."""

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        """Background task to capture and log stderr."""
        if process.stderr is None:
            return

        async for line in process.stderr:
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if decoded:
                logger.debug(
                    "plugin_stderr",
                    plugin=self.name,
                    message=decoded
                )
```

### 3. HTTP Plugins

HTTP plugins are remote services that receive tool invocations via HTTP POST requests with JSON payloads.

**Location**: Any HTTP(S) endpoint

**Characteristics**:
- Can run on remote machines
- Supports horizontal scaling
- Highest latency
- Stateless per request (state managed externally)
- Supports authentication via headers

---

## Configuration Schema

### settings.yml Structure

```yaml
# OpenCuff Plugin Configuration
# Location: ~/.opencuff/settings.yml or ./settings.yml

version: "1"

# Global plugin settings
plugin_settings:
  # Fallback polling interval for config changes (seconds)
  # Used only when watchfiles/inotify is unavailable (e.g., network filesystems)
  # When watchfiles works, changes are detected immediately via OS notifications
  config_poll_interval: 5

  # Timeout for plugin operations (seconds)
  default_timeout: 30

  # Enable/disable live reload
  live_reload: true

  # Interval for plugin health checks (seconds)
  # Set to 0 to disable periodic health checks
  health_check_interval: 30

# Plugin definitions
plugins:
  # In-source plugin example
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./Makefile
      targets: "install-*,run-*,test-*"
      allow_parallel: true

  # Process plugin example
  custom_linter:
    type: process
    enabled: true
    command: /usr/local/bin/custom-linter
    args: ["--mode", "mcp"]
    config:
      severity_threshold: warning
      max_issues: 100
    # Process-specific settings
    process_settings:
      restart_on_crash: true
      max_restarts: 3
      restart_delay: 5

  # HTTP plugin example
  code_review:
    type: http
    enabled: true
    endpoint: https://api.example.com/v1/review
    config:
      language: python
      style_guide: pep8
    # HTTP-specific settings
    http_settings:
      timeout: 60
      headers:
        Authorization: "Bearer ${CODE_REVIEW_API_KEY}"
      retry_count: 3
      retry_delay: 1
```

### Pydantic Configuration Models

```python
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, SecretStr


class PluginType(str, Enum):
    IN_SOURCE = "in_source"
    PROCESS = "process"
    HTTP = "http"


class ProcessSettings(BaseModel):
    """Settings specific to process plugins."""
    restart_on_crash: bool = True
    max_restarts: int = 3
    restart_delay: float = 5.0
    env: dict[str, str] = Field(default_factory=dict)


class HTTPSettings(BaseModel):
    """Settings specific to HTTP plugins."""
    timeout: float = 30.0
    headers: dict[str, str] = Field(default_factory=dict)
    retry_count: int = 3
    retry_delay: float = 1.0
    verify_ssl: bool = True


class PluginConfig(BaseModel):
    """Configuration for a single plugin."""
    type: PluginType
    enabled: bool = True

    # Type-specific fields
    module: str | None = None  # For in_source
    command: str | None = None  # For process
    args: list[str] = Field(default_factory=list)  # For process
    endpoint: str | None = None  # For HTTP

    # Plugin-specific configuration (passed to plugin)
    config: dict[str, Any] = Field(default_factory=dict)

    # Type-specific settings
    process_settings: ProcessSettings | None = None
    http_settings: HTTPSettings | None = None


class PluginSettings(BaseModel):
    """Global plugin system settings."""
    config_poll_interval: float = 5.0
    default_timeout: float = 30.0
    live_reload: bool = True
    health_check_interval: float = 30.0  # 0 = disabled


class Settings(BaseModel):
    """Root configuration model."""
    version: str = "1"
    plugin_settings: PluginSettings = Field(default_factory=PluginSettings)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)
```

---

## Plugin Interface Definitions

### Base Plugin Protocol

All plugins must implement this interface, regardless of type:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolDefinition:
    """Describes a tool provided by a plugin."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for parameters
    returns: dict[str, Any]  # JSON Schema for return value


@dataclass
class ToolResult:
    """Result of a tool invocation."""
    success: bool
    data: Any | None = None
    error: str | None = None


class PluginProtocol(ABC):
    """Protocol that all plugin adapters must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this plugin."""
        ...

    @abstractmethod
    async def initialize(self, config: dict[str, Any]) -> None:
        """Initialize the plugin with its configuration."""
        ...

    @abstractmethod
    async def get_tools(self) -> list[ToolDefinition]:
        """Return list of tools provided by this plugin."""
        ...

    @abstractmethod
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> ToolResult:
        """Invoke a tool with the given arguments."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the plugin is healthy and responsive."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources and shut down the plugin."""
        ...
```

### In-Source Plugin Interface

```python
from typing import Any


class InSourcePlugin:
    """Base class for in-source plugins.

    Note: Method names align with PluginProtocol for consistency:
    - initialize() corresponds to PluginProtocol.initialize()
    - shutdown() corresponds to PluginProtocol.shutdown()
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize with plugin-specific configuration."""
        self.config = config

    def get_tools(self) -> list[ToolDefinition]:
        """Return tools provided by this plugin.

        Override this method to define your plugin's tools.
        """
        raise NotImplementedError

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> ToolResult:
        """Handle tool invocation.

        Override this method to implement tool logic.
        """
        raise NotImplementedError

    async def initialize(self) -> None:
        """Called when plugin is loaded. Override for initialization."""
        pass

    async def shutdown(self) -> None:
        """Called when plugin is unloaded. Override for cleanup."""
        pass

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Called when plugin configuration changes.

        Default behavior: full shutdown/initialize cycle.
        Override for graceful config updates.
        """
        await self.shutdown()
        self.config = new_config
        await self.initialize()
```

### Process Plugin JSON Protocol

Process plugins communicate using JSON messages. Each message has a `type` field indicating the operation.

**Request Types**:

```json
// Initialize request (sent once at startup)
{
    "type": "initialize",
    "config": {
        "makefile_path": "./Makefile",
        "targets": "install-*,run-*,test-*"
    }
}

// Get tools request
{
    "type": "get_tools"
}

// Call tool request
{
    "type": "call_tool",
    "tool_name": "make_target",
    "arguments": {
        "target": "install-deps"
    }
}

// Health check request
{
    "type": "health_check"
}

// Shutdown request
{
    "type": "shutdown"
}
```

**Response Types**:

```json
// Initialize response
{
    "type": "initialize_response",
    "success": true
}

// Get tools response
{
    "type": "get_tools_response",
    "tools": [
        {
            "name": "make_target",
            "description": "Execute a Makefile target",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "The make target to execute"
                    }
                },
                "required": ["target"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"}
                }
            }
        }
    ]
}

// Call tool response
{
    "type": "call_tool_response",
    "success": true,
    "data": {
        "stdout": "Installing dependencies...\nDone.",
        "stderr": "",
        "exit_code": 0
    }
}

// Error response (for any request type)
{
    "type": "error",
    "error": "Target 'invalid-target' not found in Makefile"
}
```

### HTTP Plugin API Contract

HTTP plugins expose a REST-like API:

**Endpoints**:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/initialize` | Initialize with configuration |
| GET | `/tools` | List available tools |
| POST | `/tools/{tool_name}` | Invoke a tool |
| GET | `/health` | Health check |

**Request/Response Examples**:

```http
POST /initialize HTTP/1.1
Content-Type: application/json

{
    "config": {
        "language": "python",
        "style_guide": "pep8"
    }
}

---

HTTP/1.1 200 OK
Content-Type: application/json

{
    "success": true,
    "message": "Plugin initialized"
}
```

```http
GET /tools HTTP/1.1

---

HTTP/1.1 200 OK
Content-Type: application/json

{
    "tools": [
        {
            "name": "review_code",
            "description": "Review code for style and best practices",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "filename": {"type": "string"}
                },
                "required": ["code"]
            }
        }
    ]
}
```

**Re-initialization After Communication Errors:**

The HTTP adapter automatically re-initializes the plugin after any communication error (connection refused, timeout, 5xx errors). This handles scenarios where the remote service has restarted and lost its configuration state.

```python
class HTTPAdapter:
    """HTTP plugin adapter with automatic re-initialization."""

    def __init__(self, endpoint: str, config: dict[str, Any]) -> None:
        self.endpoint = endpoint
        self.config = config
        self._initialized = False
        self._client = httpx.AsyncClient()

    async def _ensure_initialized(self) -> None:
        """Ensure the plugin is initialized before making requests."""
        if not self._initialized:
            await self._initialize()

    async def _initialize(self) -> None:
        """Call /initialize on the remote service."""
        response = await self._client.post(
            f"{self.endpoint}/initialize",
            json={"config": self.config}
        )
        response.raise_for_status()
        self._initialized = True

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> ToolResult:
        """Call a tool with automatic re-initialization on error."""
        await self._ensure_initialized()

        try:
            response = await self._client.post(
                f"{self.endpoint}/tools/{tool_name}",
                json=arguments
            )
            response.raise_for_status()
            return ToolResult(success=True, data=response.json())

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            # Connection error - mark as uninitialized for next call
            self._initialized = False
            raise PluginError(
                code=PluginErrorCode.COMMUNICATION_ERROR,
                message=f"Connection error: {e}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                # Server error - may indicate restart, re-initialize next time
                self._initialized = False
            raise PluginError(
                code=PluginErrorCode.COMMUNICATION_ERROR,
                message=f"HTTP error {e.response.status_code}"
            )
```

**Recovery Behavior Summary:**

| Error Type | Action | Next Request |
|------------|--------|--------------|
| Connection refused | Mark uninitialized, raise error | Re-initialize first |
| Timeout | Mark uninitialized, raise error | Re-initialize first |
| 5xx Server Error | Mark uninitialized, raise error | Re-initialize first |
| 4xx Client Error | Raise error (do not re-init) | Use existing state |

```http
POST /tools/review_code HTTP/1.1
Content-Type: application/json

{
    "code": "def foo( x ):\n    return x+1",
    "filename": "example.py"
}

---

HTTP/1.1 200 OK
Content-Type: application/json

{
    "success": true,
    "data": {
        "issues": [
            {
                "line": 1,
                "message": "Whitespace inside parentheses",
                "severity": "warning"
            }
        ],
        "summary": "1 issue found"
    }
}
```

---

## Plugin Lifecycle

### State Machine

```
                              +-------------+
                              |             |
                              |  UNLOADED   |<-----------------+
                              |             |                  |
                              +------+------+                  |
                                     |                         |
                                     | load()                  | unload()
                                     v                         |
                              +------+------+                  |
                              |             |                  |
                              | INITIALIZING|                  |
                              |             |                  |
                              +------+------+                  |
                                     |                         |
                                     | success                 |
                                     v                         |
                              +------+------+                  |
                +------------>|             |                  |
                |             |   ACTIVE    |------------------+
                |  reload()   |             |
                |             +------+------+
                |                    |
                +--------------------+ <------------------+
                                     |                    |
                                     | error /            | success
                                     | health_check fail  |
                                     v                    |
                              +------+------+             |
                              |             |             |
                              |   ERROR     |             |
                              |             |             |
                              +------+------+             |
                                     |                    |
                                     | retry / reload()   |
                                     v                    |
                              +------+------+             |
                              |             |-------------+
                              | RECOVERING  |
                              |             |----+
                              +------+------+    |
                                     |           | max_restarts
                                     |           | exceeded
                                     v           v
                              +-------------+  +---------+
                              | INITIALIZING|  | UNLOADED|
                              +-------------+  +---------+
```

**State Transitions:**

| From State | Event | To State | Notes |
|------------|-------|----------|-------|
| UNLOADED | load() | INITIALIZING | Plugin loading begins |
| INITIALIZING | success | ACTIVE | Plugin ready to serve |
| INITIALIZING | failure | ERROR | Initialization failed |
| ACTIVE | reload() | ACTIVE | Hot reload (in-source) or re-init |
| ACTIVE | error | ERROR | Runtime error occurred |
| ACTIVE | health_check fail | ERROR | Health check failed |
| ACTIVE | unload() | UNLOADED | Clean shutdown |
| ERROR | retry | RECOVERING | Automatic recovery attempt |
| RECOVERING | success | ACTIVE | Recovery succeeded |
| RECOVERING | max_restarts exceeded | UNLOADED | Give up, plugin disabled |

### Lifecycle Methods

```python
class PluginLifecycle:
    """Manages the lifecycle of a single plugin."""

    async def load(self, config: PluginConfig) -> None:
        """Load and initialize a plugin.

        1. Validate configuration
        2. Create appropriate adapter (in-source/process/HTTP)
        3. Call adapter.initialize()
        4. Fetch tool definitions
        5. Register tools with FastMCP
        6. Set state to ACTIVE
        """
        ...

    async def unload(self) -> None:
        """Unload a plugin.

        1. Unregister tools from FastMCP
        2. Call adapter.shutdown()
        3. Clean up resources
        4. Set state to UNLOADED
        """
        ...

    async def reload(self, new_config: PluginConfig | None = None) -> None:
        """Reload a plugin, optionally with new configuration.

        For in-source plugins with on_reload():
            1. Call plugin.on_reload(new_config)
            2. Refresh tool registrations

        For other plugins:
            1. Unload plugin
            2. Load plugin with new config
        """
        ...

    async def health_check(self) -> bool:
        """Check plugin health.

        1. Call adapter.health_check()
        2. If unhealthy, attempt recovery
        3. Return health status
        """
        ...


class HealthMonitor:
    """Periodic health monitoring for all plugins.

    Runs health checks at configured intervals and triggers recovery
    for unhealthy plugins.
    """

    def __init__(
        self,
        plugin_manager: "PluginManager",
        interval: float = 30.0
    ) -> None:
        self.plugin_manager = plugin_manager
        self.interval = interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the health monitoring loop."""
        if self.interval <= 0:
            logger.info("health_monitor_disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            "health_monitor_started",
            interval=self.interval
        )

    async def stop(self) -> None:
        """Stop the health monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        """Main health check loop."""
        while self._running:
            await asyncio.sleep(self.interval)

            for name, lifecycle in self.plugin_manager.plugins.items():
                if lifecycle.state != PluginState.ACTIVE:
                    continue

                try:
                    healthy = await lifecycle.health_check()
                    if not healthy:
                        logger.warning(
                            "plugin_health_check_failed",
                            plugin=name
                        )
                        # Trigger recovery
                        await lifecycle.recover()
                except Exception as e:
                    logger.error(
                        "plugin_health_check_error",
                        plugin=name,
                        error=str(e)
                    )
```

### Plugin Manager

```python
class PluginManager:
    """Manages all plugins and their lifecycles."""

    def __init__(self, mcp: FastMCP, settings_path: str) -> None:
        self.mcp = mcp
        self.settings_path = settings_path
        self.plugins: dict[str, PluginLifecycle] = {}
        self.config_watcher: ConfigWatcher | None = None
        self.health_monitor: HealthMonitor | None = None
        self.tool_registry = ToolRegistry()

    async def start(self) -> None:
        """Start the plugin manager.

        1. Load initial configuration
        2. Validate configuration (including tool collision check)
        3. Start config file watcher
        4. Load all enabled plugins
        5. Start health monitor
        """
        ...

    async def stop(self) -> None:
        """Stop the plugin manager.

        1. Stop health monitor
        2. Stop config watcher
        3. Unload all plugins
        """
        ...

    async def on_config_change(self, new_settings: Settings) -> None:
        """Handle configuration file changes.

        1. Validate new configuration
        2. Diff current vs new configuration
        3. Unload removed plugins
        4. Reload changed plugins (with request barrier)
        5. Load new plugins
        """
        ...
```

---

## Live Reload Mechanism

### Config Watcher

The configuration watcher monitors `settings.yml` for changes and triggers plugin updates.

**File Change Detection Strategy:**

The watcher uses a two-tier approach:

1. **Primary: `watchfiles` (inotify/FSEvents)** - Provides immediate notifications when files change. This is the preferred method and works on most local filesystems.

2. **Fallback: Polling** - Used when `watchfiles` is unavailable or fails (e.g., on network filesystems like NFS/CIFS, or certain containerized environments). The `config_poll_interval` setting controls how frequently the file is checked.

```python
import asyncio
import hashlib
from pathlib import Path
from typing import Callable, Awaitable

from watchfiles import awatch, WatcherType

logger = structlog.get_logger()


class ConfigWatcher:
    """Watches configuration file for changes.

    Uses watchfiles for immediate OS-level notifications where available,
    with automatic fallback to polling for environments where inotify
    is not supported (e.g., network filesystems).
    """

    def __init__(
        self,
        settings_path: str,
        on_change: Callable[[Settings], Awaitable[None]],
        poll_interval: float = 5.0
    ) -> None:
        self.settings_path = settings_path
        self.on_change = on_change
        self.poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_hash: str | None = None

    async def start(self) -> None:
        """Start watching for configuration changes."""
        self._running = True
        self._last_hash = self._compute_hash()

        # Try watchfiles first, fall back to polling if it fails
        try:
            self._task = asyncio.create_task(self._watch_with_watchfiles())
        except Exception as e:
            logger.warning(
                "watchfiles unavailable, falling back to polling",
                error=str(e),
                poll_interval=self.poll_interval
            )
            self._task = asyncio.create_task(self._watch_with_polling())

    async def stop(self) -> None:
        """Stop watching for configuration changes."""
        self._running = False
        if self._task:
            self._task.cancel()

    def _compute_hash(self) -> str:
        """Compute hash of settings file for change detection."""
        content = Path(self.settings_path).read_bytes()
        return hashlib.sha256(content).hexdigest()

    async def _watch_with_watchfiles(self) -> None:
        """Watch using watchfiles (inotify/FSEvents)."""
        logger.info(
            "config_watcher_started",
            method="watchfiles",
            path=self.settings_path
        )
        async for changes in awatch(self.settings_path):
            if not self._running:
                break
            await self._handle_change()

    async def _watch_with_polling(self) -> None:
        """Fallback: watch using periodic polling."""
        logger.info(
            "config_watcher_started",
            method="polling",
            interval=self.poll_interval,
            path=self.settings_path
        )
        while self._running:
            await asyncio.sleep(self.poll_interval)
            current_hash = self._compute_hash()
            if current_hash != self._last_hash:
                self._last_hash = current_hash
                await self._handle_change()

    async def _handle_change(self) -> None:
        """Process a detected configuration change."""
        try:
            new_settings = self._load_settings()
            await self.on_change(new_settings)
        except Exception as e:
            logger.error("config_change_error", error=str(e))

    def _load_settings(self) -> Settings:
        """Load and validate settings from file."""
        import yaml
        with open(self.settings_path) as f:
            data = yaml.safe_load(f)
        return Settings.model_validate(data)
```

### Graceful Reload Strategy

```
                        Config Change Detected
                                 |
                                 v
                    +------------------------+
                    | Parse New Configuration|
                    +------------------------+
                                 |
                                 v
                    +------------------------+
                    | Validate Configuration |
                    +------------------------+
                                 |
           +---------------------+---------------------+
           |                     |                     |
           v                     v                     v
    +-----------+         +-----------+         +-----------+
    |  Plugins  |         |  Plugins  |         |  Plugins  |
    |  Removed  |         |  Changed  |         |   Added   |
    +-----------+         +-----------+         +-----------+
           |                     |                     |
           v                     v                     v
    +-----------+         +-----------+         +-----------+
    |  Unload   |         |  Reload   |         |   Load    |
    |  Graceful |         |  In-Place |         |   Fresh   |
    +-----------+         +-----------+         +-----------+
           |                     |                     |
           +---------------------+---------------------+
                                 |
                                 v
                    +------------------------+
                    | Update Tool Registry   |
                    | (atomic operation)     |
                    +------------------------+
                                 |
                                 v
                    +------------------------+
                    | Log Changes & Notify   |
                    +------------------------+
```

### Reload Considerations by Plugin Type

| Plugin Type | Reload Strategy | Downtime |
|-------------|-----------------|----------|
| In-Source | Hot reload via `importlib.reload()` or full cycle | ~0ms |
| Process | Send reload command or restart process | ~100ms |
| HTTP | Update endpoint/headers in client | ~0ms |

### Request Barrier During Reload

To prevent race conditions during plugin reload, the system implements a request barrier mechanism that ensures in-flight requests complete safely before the reload takes effect.

**Behavior Specification:**

1. **In-flight requests**: All requests that began before the reload signal MUST complete using the OLD plugin instance
2. **New requests during transition**: Requests arriving during reload are QUEUED until the new plugin is ready
3. **Tool Registry update**: The update is atomic - tools are swapped in a single operation after the new plugin is initialized
4. **Timeout handling**: Queued requests have a maximum wait time (default: 5 seconds) before returning an error

**Request Barrier Implementation:**

```python
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class RequestBarrier:
    """Manages request flow during plugin reload.

    Ensures in-flight requests complete before reload and queues
    new requests until the reload is complete.
    """

    def __init__(self, queue_timeout: float = 5.0) -> None:
        self._active_requests: int = 0
        self._lock = asyncio.Lock()
        self._drain_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._reloading = False
        self._queue_timeout = queue_timeout

        # Initially ready (not reloading)
        self._ready_event.set()
        self._drain_event.set()

    @asynccontextmanager
    async def request_scope(self) -> AsyncIterator[None]:
        """Context manager for tracking request lifecycle.

        Usage:
            async with barrier.request_scope():
                result = await plugin.call_tool(...)
        """
        # Wait if a reload is in progress
        try:
            await asyncio.wait_for(
                self._ready_event.wait(),
                timeout=self._queue_timeout
            )
        except asyncio.TimeoutError:
            raise PluginError(
                code=PluginErrorCode.TIMEOUT,
                message="Plugin reload in progress, request timed out"
            )

        async with self._lock:
            self._active_requests += 1
            self._drain_event.clear()

        try:
            yield
        finally:
            async with self._lock:
                self._active_requests -= 1
                if self._active_requests == 0:
                    self._drain_event.set()

    @asynccontextmanager
    async def reload_scope(self) -> AsyncIterator[None]:
        """Context manager for plugin reload operations.

        Usage:
            async with barrier.reload_scope():
                await plugin.reload(new_config)
        """
        # Signal that reload is starting - block new requests
        self._ready_event.clear()
        self._reloading = True

        try:
            # Wait for all in-flight requests to complete
            await self._drain_event.wait()

            yield  # Perform the actual reload

        finally:
            # Reload complete - allow new requests
            self._reloading = False
            self._ready_event.set()


class PluginLifecycleWithBarrier:
    """Plugin lifecycle manager with request barrier support."""

    def __init__(self) -> None:
        self.barrier = RequestBarrier()
        self.plugin: PluginProtocol | None = None

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> ToolResult:
        """Call tool with request barrier protection."""
        async with self.barrier.request_scope():
            if self.plugin is None:
                raise PluginError(
                    code=PluginErrorCode.PLUGIN_UNHEALTHY,
                    message="Plugin not loaded"
                )
            return await self.plugin.call_tool(tool_name, arguments)

    async def reload(self, new_config: PluginConfig) -> None:
        """Reload plugin with request barrier protection."""
        async with self.barrier.reload_scope():
            # At this point, all in-flight requests have completed
            # and new requests are queued

            if self.plugin:
                await self.plugin.shutdown()

            # Initialize new plugin instance
            self.plugin = await self._create_plugin(new_config)
            await self.plugin.initialize(new_config.config)

            # Tool registry update happens here (atomic)
            await self._update_tool_registry()
```

**Sequence Diagram:**

```
Request A    PluginManager    Barrier    Plugin(old)    Plugin(new)
    |              |             |            |              |
    |--call_tool-->|             |            |              |
    |              |--request_scope()-------->|              |
    |              |             |<--ok-------|              |
    |              |--call_tool-------------->|              |
    |              |             |            |              |
    |   [Config change detected]  |            |              |
    |              |--reload_scope()--------->|              |
    |              |             |--wait for  |              |
    |              |             |  drain     |              |
    |              |             |            |              |
Request B         |             |            |              |
    |--call_tool-->|             |            |              |
    |              |--request_scope()-------->|              |
    |              |             |--BLOCKED---|              |
    |              |             |  (queued)  |              |
    |              |             |            |              |
    |              |<--result from A----------|              |
    |              |             |<--drained--|              |
    |              |             |            |              |
    |              |--shutdown()------------->|              |
    |              |--initialize()---------------------->|   |
    |              |--update_tools()         |              |
    |              |             |--READY----|              |
    |              |             |            |              |
    |              |--call_tool (B)--------------------->|   |
    |              |<--result from B---------------------|   |
    |<--result-----|             |            |              |
```

### Tool Name Collision Handling

Tool names must be unique across all loaded plugins. The plugin system uses **namespacing by plugin name** to prevent collisions.

**Namespacing Strategy:**

All tools are registered with a namespace prefix: `{plugin_name}.{tool_name}`

Examples:
- Plugin `makefile` with tool `build` becomes `makefile.build`
- Plugin `git` with tool `status` becomes `git.status`
- Plugin `code_reviewer` with tool `review_code` becomes `code_reviewer.review_code`

**Implementation:**

```python
class ToolRegistry:
    """Registry for all plugin tools with namespace support."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[str, ToolDefinition]] = {}  # fqn -> (plugin, tool)
        self._lock = asyncio.Lock()

    def _make_fqn(self, plugin_name: str, tool_name: str) -> str:
        """Create fully qualified tool name."""
        return f"{plugin_name}.{tool_name}"

    async def register_tools(
        self,
        plugin_name: str,
        tools: list[ToolDefinition]
    ) -> None:
        """Register tools from a plugin with namespace prefix."""
        async with self._lock:
            for tool in tools:
                fqn = self._make_fqn(plugin_name, tool.name)

                # Check for collisions (should not happen with namespacing,
                # but guards against duplicate tool names within a plugin)
                if fqn in self._tools:
                    raise PluginError(
                        code=PluginErrorCode.CONFIG_INVALID,
                        message=f"Duplicate tool name: {fqn}",
                        plugin_name=plugin_name
                    )

                self._tools[fqn] = (plugin_name, tool)

    async def unregister_plugin(self, plugin_name: str) -> None:
        """Remove all tools from a plugin."""
        async with self._lock:
            prefix = f"{plugin_name}."
            to_remove = [
                fqn for fqn in self._tools
                if fqn.startswith(prefix)
            ]
            for fqn in to_remove:
                del self._tools[fqn]

    def get_tool(self, fqn: str) -> tuple[str, ToolDefinition] | None:
        """Look up a tool by fully qualified name."""
        return self._tools.get(fqn)

    def list_tools(self) -> list[tuple[str, ToolDefinition]]:
        """List all registered tools."""
        return [(fqn, tool) for fqn, (_, tool) in self._tools.items()]
```

**Configuration Validation:**

Additionally, at configuration load time, the system validates that no two plugins define tools with identical fully-qualified names:

```python
def validate_no_tool_collisions(settings: Settings) -> None:
    """Validate that no tool name collisions exist.

    Called during configuration parsing, before any plugins are loaded.
    Raises ConfigurationError if collisions are detected.
    """
    # Note: Full collision detection requires loading plugins to discover
    # their tools. This validation catches obvious issues like duplicate
    # plugin names in configuration.
    plugin_names = list(settings.plugins.keys())
    if len(plugin_names) != len(set(plugin_names)):
        duplicates = [
            name for name in plugin_names
            if plugin_names.count(name) > 1
        ]
        raise ConfigurationError(
            f"Duplicate plugin names in configuration: {duplicates}"
        )
```

---

## Example Plugin Implementations

### Example 1: In-Source Makefile Plugin

```python
# src/opencuff/plugins/makefile/plugin.py
"""Makefile plugin for OpenCuff.

Provides tools to discover and execute Makefile targets.
"""
import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any

from opencuff.plugins.base import InSourcePlugin, ToolDefinition, ToolResult


class MakefilePlugin(InSourcePlugin):
    """Plugin that exposes Makefile targets as MCP tools."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.makefile_path = Path(config.get("makefile_path", "./Makefile"))
        self.target_patterns = self._parse_patterns(
            config.get("targets", "*")
        )
        self.allow_parallel = config.get("allow_parallel", True)
        self._discovered_targets: list[str] = []
        # Mapping from tool name to original Makefile target name
        # This avoids lossy bidirectional conversion (e.g., "_" vs "-")
        self._tool_to_target: dict[str, str] = {}

    def _parse_patterns(self, patterns: str) -> list[str]:
        """Parse comma-separated wildcard patterns."""
        return [p.strip() for p in patterns.split(",") if p.strip()]

    def _discover_targets(self) -> list[str]:
        """Discover available targets in the Makefile."""
        if not self.makefile_path.exists():
            return []

        content = self.makefile_path.read_text()
        # Match target definitions (simplified regex)
        target_pattern = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:", re.MULTILINE)
        all_targets = target_pattern.findall(content)

        # Filter by configured patterns
        filtered = []
        for target in all_targets:
            for pattern in self.target_patterns:
                if fnmatch.fnmatch(target, pattern):
                    filtered.append(target)
                    break

        return filtered

    async def initialize(self) -> None:
        """Discover targets on initialization."""
        self._discovered_targets = self._discover_targets()
        # Build the tool name to target mapping
        self._tool_to_target = {}
        for target in self._discovered_targets:
            # Replace characters not allowed in tool names
            tool_name = f"make_{target.replace('-', '_').replace('.', '_')}"
            self._tool_to_target[tool_name] = target

    def get_tools(self) -> list[ToolDefinition]:
        """Return a tool for each discovered target."""
        tools = [
            ToolDefinition(
                name="make_list_targets",
                description="List available Makefile targets",
                parameters={"type": "object", "properties": {}},
                returns={
                    "type": "array",
                    "items": {"type": "string"}
                }
            )
        ]

        for tool_name, target in self._tool_to_target.items():
            tools.append(ToolDefinition(
                name=tool_name,
                description=f"Execute 'make {target}'",
                parameters={
                    "type": "object",
                    "properties": {
                        "extra_args": {
                            "type": "string",
                            "description": "Additional arguments to pass to make"
                        }
                    }
                },
                returns={
                    "type": "object",
                    "properties": {
                        "stdout": {"type": "string"},
                        "stderr": {"type": "string"},
                        "exit_code": {"type": "integer"}
                    }
                }
            ))

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute the requested make target."""
        if tool_name == "make_list_targets":
            return ToolResult(success=True, data=self._discovered_targets)

        # Look up the original target name from the mapping
        target = self._tool_to_target.get(tool_name)

        if target is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: '{tool_name}'"
            )

        # Build command
        cmd = ["make", "-f", str(self.makefile_path), target]
        if self.allow_parallel:
            cmd.extend(["-j", str(self._get_cpu_count())])

        extra_args = arguments.get("extra_args", "")
        if extra_args:
            cmd.extend(extra_args.split())

        # Execute
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            return ToolResult(
                success=result.returncode == 0,
                data={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode
                }
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error="Command timed out after 300 seconds"
            )

    def _get_cpu_count(self) -> int:
        """Get CPU count for parallel execution."""
        import os
        return os.cpu_count() or 1
```

**Configuration**:
```yaml
plugins:
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./Makefile
      targets: "install-*,run-*,test-*,build-*"
      allow_parallel: true
```

### Example 2: Process Plugin (Python Script)

```python
#!/usr/bin/env python3
# plugins/git-helper/git_helper.py
"""Git helper process plugin for OpenCuff.

Provides tools for common git operations.
"""
import json
import subprocess
import sys
from typing import Any


def get_tools() -> list[dict[str, Any]]:
    """Return available tools."""
    return [
        {
            "name": "git_status",
            "description": "Get the current git status",
            "parameters": {
                "type": "object",
                "properties": {
                    "short": {
                        "type": "boolean",
                        "description": "Use short format",
                        "default": False
                    }
                }
            },
            "returns": {
                "type": "object",
                "properties": {
                    "output": {"type": "string"},
                    "branch": {"type": "string"}
                }
            }
        },
        {
            "name": "git_diff",
            "description": "Show changes in working directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "Show staged changes only",
                        "default": False
                    },
                    "file": {
                        "type": "string",
                        "description": "Specific file to diff"
                    }
                }
            },
            "returns": {
                "type": "object",
                "properties": {
                    "diff": {"type": "string"}
                }
            }
        }
    ]


def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute the requested tool."""
    if tool_name == "git_status":
        return git_status(arguments)
    elif tool_name == "git_diff":
        return git_diff(arguments)
    else:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}


def git_status(args: dict[str, Any]) -> dict[str, Any]:
    """Execute git status."""
    cmd = ["git", "status"]
    if args.get("short"):
        cmd.append("--short")

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Get current branch
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True,
        text=True
    )

    return {
        "success": result.returncode == 0,
        "data": {
            "output": result.stdout or result.stderr,
            "branch": branch_result.stdout.strip()
        }
    }


def git_diff(args: dict[str, Any]) -> dict[str, Any]:
    """Execute git diff."""
    cmd = ["git", "diff"]
    if args.get("staged"):
        cmd.append("--staged")
    if args.get("file"):
        cmd.append(args["file"])

    result = subprocess.run(cmd, capture_output=True, text=True)

    return {
        "success": result.returncode == 0,
        "data": {"diff": result.stdout or result.stderr}
    }


def main() -> None:
    """Main loop: read JSON from stdin, write JSON to stdout."""
    config: dict[str, Any] = {}

    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            request_type = request.get("type")

            if request_type == "initialize":
                config = request.get("config", {})
                response = {"type": "initialize_response", "success": True}

            elif request_type == "get_tools":
                response = {
                    "type": "get_tools_response",
                    "tools": get_tools()
                }

            elif request_type == "call_tool":
                result = call_tool(
                    request["tool_name"],
                    request.get("arguments", {})
                )
                response = {"type": "call_tool_response", **result}

            elif request_type == "health_check":
                response = {"type": "health_check_response", "healthy": True}

            elif request_type == "shutdown":
                response = {"type": "shutdown_response", "success": True}
                print(json.dumps(response), flush=True)
                break

            else:
                response = {
                    "type": "error",
                    "error": f"Unknown request type: {request_type}"
                }

            print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            print(json.dumps({
                "type": "error",
                "error": f"Invalid JSON: {e}"
            }), flush=True)
        except Exception as e:
            print(json.dumps({
                "type": "error",
                "error": str(e)
            }), flush=True)


if __name__ == "__main__":
    main()
```

**Configuration**:
```yaml
plugins:
  git_helper:
    type: process
    enabled: true
    command: python3
    args: ["./plugins/git-helper/git_helper.py"]
    config: {}
    process_settings:
      restart_on_crash: true
      max_restarts: 5
```

### Example 3: HTTP Plugin (FastAPI Service)

```python
# external-plugins/code-reviewer/main.py
"""Code review HTTP plugin for OpenCuff.

A standalone FastAPI service that provides code review capabilities.
"""
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Code Reviewer Plugin")

# Global config
config: dict[str, Any] = {}


class InitRequest(BaseModel):
    config: dict[str, Any]


class ToolCallRequest(BaseModel):
    code: str
    filename: str | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    returns: dict[str, Any] | None = None


class ToolResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None


@app.post("/initialize")
async def initialize(request: InitRequest) -> dict[str, Any]:
    """Initialize the plugin with configuration."""
    global config
    config = request.config
    return {"success": True, "message": "Plugin initialized"}


@app.get("/tools")
async def get_tools() -> dict[str, list[ToolDefinition]]:
    """Return available tools."""
    return {
        "tools": [
            ToolDefinition(
                name="review_code",
                description="Review code for style issues and best practices",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "The code to review"
                        },
                        "filename": {
                            "type": "string",
                            "description": "Optional filename for context"
                        }
                    },
                    "required": ["code"]
                },
                returns={
                    "type": "object",
                    "properties": {
                        "issues": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "line": {"type": "integer"},
                                    "message": {"type": "string"},
                                    "severity": {"type": "string"}
                                }
                            }
                        },
                        "summary": {"type": "string"}
                    }
                }
            )
        ]
    }


@app.post("/tools/review_code")
async def review_code(request: ToolCallRequest) -> ToolResponse:
    """Review the provided code."""
    issues = []
    lines = request.code.split("\n")

    for i, line in enumerate(lines, 1):
        # Simple style checks (in production, use ruff/pylint)
        if len(line) > 88:
            issues.append({
                "line": i,
                "message": f"Line too long ({len(line)} > 88 characters)",
                "severity": "warning"
            })

        if line.rstrip() != line:
            issues.append({
                "line": i,
                "message": "Trailing whitespace",
                "severity": "info"
            })

        if "  " in line and not line.strip().startswith("#"):
            # Check for multiple spaces (not in comments)
            issues.append({
                "line": i,
                "message": "Multiple consecutive spaces",
                "severity": "info"
            })

    return ToolResponse(
        success=True,
        data={
            "issues": issues,
            "summary": f"{len(issues)} issue(s) found"
        }
    )


@app.get("/health")
async def health_check() -> dict[str, bool]:
    """Health check endpoint."""
    return {"healthy": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

**Configuration**:
```yaml
plugins:
  code_reviewer:
    type: http
    enabled: true
    endpoint: http://localhost:8080
    config:
      style_guide: pep8
    http_settings:
      timeout: 30
      retry_count: 3
```

---

## Security Considerations

### 1. Configuration Security

**Environment Variable Expansion**:
```yaml
# Secrets should NEVER be hardcoded
# Use environment variable references
http_settings:
  headers:
    Authorization: "Bearer ${API_KEY}"  # Resolved at runtime
```

**Implementation**:
```python
import os
import re


def expand_env_vars(value: str) -> str:
    """Expand ${VAR} patterns with environment variables."""
    pattern = re.compile(r'\$\{([^}]+)\}')

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable '{var_name}' not set")
        return env_value

    return pattern.sub(replacer, value)
```

### 2. In-Source Plugin Security

| Risk | Mitigation |
|------|------------|
| Arbitrary code execution | Only load plugins from `opencuff.plugins` namespace |
| Import hijacking | Validate module paths before loading |
| Resource exhaustion | Implement timeouts and resource limits |

```python
def validate_module_path(module: str) -> bool:
    """Ensure module is within allowed namespace."""
    allowed_prefixes = ["opencuff.plugins."]
    return any(module.startswith(prefix) for prefix in allowed_prefixes)
```

### 3. Process Plugin Security

| Risk | Mitigation |
|------|------------|
| Command injection | Never pass user input directly to shell |
| Privilege escalation | Run plugins with minimal permissions |
| Resource exhaustion | Set memory/CPU limits via cgroups |
| Hanging processes | Implement communication timeouts |

```python
import resource


def apply_resource_limits() -> None:
    """Apply resource limits to plugin process."""
    # Limit memory to 512MB
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    # Limit CPU time to 60 seconds
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
```

### 4. HTTP Plugin Security

| Risk | Mitigation |
|------|------------|
| Man-in-the-middle | Enforce HTTPS for non-localhost endpoints |
| Data leakage | Validate and sanitize all data sent to plugins |
| Authentication bypass | Use secure authentication mechanisms |
| SSRF attacks | Validate and whitelist allowed endpoints |

```python
from urllib.parse import urlparse


def validate_endpoint(endpoint: str) -> bool:
    """Validate HTTP plugin endpoint."""
    parsed = urlparse(endpoint)

    # Require HTTPS for non-localhost
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        if parsed.scheme != "https":
            raise ValueError("Non-localhost endpoints must use HTTPS")

    # Block internal network ranges (optional, configurable)
    # This prevents SSRF to internal services

    return True
```

### 5. Configuration File Security

| Risk | Mitigation |
|------|------------|
| Unauthorized modification | Set restrictive file permissions (600) |
| Injection via YAML | Use safe YAML loader |
| Path traversal | Validate all file paths |

```python
import yaml
import os
import stat


def load_settings_safely(path: str) -> dict:
    """Load settings with security checks."""
    # Check file permissions
    file_stat = os.stat(path)
    mode = file_stat.st_mode

    # Warn if file is world-readable
    if mode & stat.S_IROTH:
        logger.warning(
            f"Settings file {path} is world-readable. "
            "Consider restricting permissions."
        )

    # Use safe loader to prevent arbitrary code execution
    with open(path) as f:
        return yaml.safe_load(f)
```

### 6. Audit Logging

All plugin operations should be logged for security audit purposes:

```python
import structlog

logger = structlog.get_logger()


async def call_tool_with_audit(
    plugin_name: str,
    tool_name: str,
    arguments: dict[str, Any]
) -> ToolResult:
    """Call tool with audit logging."""
    logger.info(
        "tool_invocation_started",
        plugin=plugin_name,
        tool=tool_name,
        # Don't log sensitive arguments
        argument_keys=list(arguments.keys())
    )

    try:
        result = await plugin.call_tool(tool_name, arguments)
        logger.info(
            "tool_invocation_completed",
            plugin=plugin_name,
            tool=tool_name,
            success=result.success
        )
        return result
    except Exception as e:
        logger.error(
            "tool_invocation_failed",
            plugin=plugin_name,
            tool=tool_name,
            error=str(e)
        )
        raise
```

---

## Error Handling

### Error Categories

```python
from enum import Enum


class PluginErrorCode(str, Enum):
    """Error codes for plugin operations."""
    # Configuration errors
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISSING = "CONFIG_MISSING"

    # Lifecycle errors
    LOAD_FAILED = "LOAD_FAILED"
    INIT_FAILED = "INIT_FAILED"
    SHUTDOWN_FAILED = "SHUTDOWN_FAILED"

    # Runtime errors
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TIMEOUT = "TIMEOUT"

    # Communication errors (process/HTTP)
    COMMUNICATION_ERROR = "COMMUNICATION_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"

    # Health errors
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    PLUGIN_UNHEALTHY = "PLUGIN_UNHEALTHY"


class PluginError(Exception):
    """Base exception for plugin errors."""

    def __init__(
        self,
        code: PluginErrorCode,
        message: str,
        plugin_name: str | None = None,
        cause: Exception | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.plugin_name = plugin_name
        self.cause = cause
        super().__init__(f"[{code}] {message}")
```

### Recovery Strategies

```
            Error Detected
                  |
                  v
        +------------------+
        | Categorize Error |
        +------------------+
                  |
    +-------------+-------------+
    |             |             |
    v             v             v
Transient    Recoverable   Fatal
    |             |             |
    v             v             v
  Retry      Restart       Unload
  (3x)       Plugin        Plugin
    |             |             |
    +-------------+-------------+
                  |
                  v
        +------------------+
        | Log & Notify     |
        +------------------+
```

---

## Future Considerations

### Potential Enhancements (Not in Scope for v1)

1. **Plugin Discovery Service**
   - Central registry for discovering community plugins
   - Version compatibility checking
   - Automatic updates

2. **Plugin Sandboxing**
   - Container-based isolation for untrusted plugins
   - Capability-based security model
   - Fine-grained permission system

3. **Plugin Composition**
   - Allow plugins to depend on other plugins
   - Cross-plugin communication channels
   - Shared state management

4. **Metrics and Observability**
   - Prometheus metrics for plugin performance
   - Distributed tracing support
   - Real-time monitoring dashboard

5. **Plugin Testing Framework**
   - Mock infrastructure for plugin development
   - Automated compatibility testing
   - Performance benchmarking tools

---

## Appendix A: Recommended Dependencies

For implementing this plugin system, the following packages are recommended:

| Package | Purpose | Version |
|---------|---------|---------|
| `pydantic` | Configuration validation | >=2.0 |
| `pyyaml` | YAML parsing | >=6.0 |
| `watchfiles` | File change detection | >=0.21 |
| `httpx` | Async HTTP client | >=0.27 |
| `structlog` | Structured logging | >=24.0 |

---

## Appendix B: Configuration File Locations

The plugin system searches for `settings.yml` in the following order:

1. Path specified via `--config` CLI argument
2. `./settings.yml` (current working directory)
3. `~/.opencuff/settings.yml` (user home directory)
4. `/etc/opencuff/settings.yml` (system-wide)

The first file found is used. Files are NOT merged.

---

## Appendix C: Full Settings Schema (JSON Schema)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "OpenCuff Plugin Settings",
  "type": "object",
  "properties": {
    "version": {
      "type": "string",
      "const": "1"
    },
    "plugin_settings": {
      "type": "object",
      "properties": {
        "config_poll_interval": {
          "type": "number",
          "minimum": 1,
          "default": 5,
          "description": "Fallback polling interval (seconds) when watchfiles is unavailable"
        },
        "default_timeout": {
          "type": "number",
          "minimum": 1,
          "default": 30
        },
        "live_reload": {
          "type": "boolean",
          "default": true
        },
        "health_check_interval": {
          "type": "number",
          "minimum": 0,
          "default": 30,
          "description": "Interval for periodic health checks (0 to disable)"
        }
      }
    },
    "plugins": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["type"],
        "properties": {
          "type": {
            "type": "string",
            "enum": ["in_source", "process", "http"]
          },
          "enabled": {
            "type": "boolean",
            "default": true
          },
          "module": {
            "type": "string"
          },
          "command": {
            "type": "string"
          },
          "args": {
            "type": "array",
            "items": {"type": "string"}
          },
          "endpoint": {
            "type": "string",
            "format": "uri"
          },
          "config": {
            "type": "object"
          },
          "process_settings": {
            "type": "object",
            "properties": {
              "restart_on_crash": {"type": "boolean"},
              "max_restarts": {"type": "integer"},
              "restart_delay": {"type": "number"},
              "env": {"type": "object"}
            }
          },
          "http_settings": {
            "type": "object",
            "properties": {
              "timeout": {"type": "number"},
              "headers": {"type": "object"},
              "retry_count": {"type": "integer"},
              "retry_delay": {"type": "number"},
              "verify_ssl": {"type": "boolean"}
            }
          }
        }
      }
    }
  }
}
```
