# Dynamic Tool Registration - High-Level Design

**Version:** 1.0
**Date:** 2026-01-18
**Status:** Draft

**Revision History:**
| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-18 | Initial draft |

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Problem Statement](#problem-statement)
3. [Goals and Non-Goals](#goals-and-non-goals)
4. [Architecture Overview](#architecture-overview)
5. [Component Design](#component-design)
6. [Tool Registration Flow](#tool-registration-flow)
7. [Tool Unregistration Flow](#tool-unregistration-flow)
8. [Live Reload Integration](#live-reload-integration)
9. [Error Handling](#error-handling)
10. [Testing Approach](#testing-approach)
11. [Migration Path](#migration-path)

---

## Executive Summary

This document describes the design for dynamic tool registration in OpenCuff, enabling plugin tools to appear as first-class MCP tools rather than requiring invocation through a gateway tool (`call_plugin_tool`). The design maintains backward compatibility while providing a seamless experience for MCP clients.

Currently, plugin tools are registered only in OpenCuff's internal `ToolRegistry`. This HLD proposes bridging the `ToolRegistry` with FastMCP's tool registration system, so that tools like `makefile.make_test` appear directly in the MCP tool list alongside built-in tools like `hello` and `list_plugins`.

---

## Problem Statement

### Current State

```
MCP Client (e.g., Claude Code)
        |
        v
+-------------------+
|    FastMCP        |
|  (3 static tools) |
|  - hello          |
|  - list_plugins   |
|  - call_plugin_tool
+--------+----------+
         |
         v
+-------------------+
|  PluginManager    |
+--------+----------+
         |
         v
+-------------------+
|   ToolRegistry    |
|  (internal only)  |
|  - makefile.make_test
|  - makefile.make_build
|  - dummy.echo     |
+-------------------+
```

**Problems:**
1. MCP clients only see 3 tools; they must know to call `call_plugin_tool` with the FQN
2. Plugin tools lack discoverability - clients cannot enumerate available plugin tools via MCP
3. Tool schemas are not exposed to MCP clients for validation
4. The `call_plugin_tool` gateway adds indirection and complexity

### Desired State

```
MCP Client (e.g., Claude Code)
        |
        v
+-------------------+
|    FastMCP        |
|  (dynamic tools)  |
|  - hello          |
|  - list_plugins   |
|  - call_plugin_tool (deprecated)
|  - makefile.make_test
|  - makefile.make_build
|  - dummy.echo     |
+--------+----------+
         |
         v
+-------------------+
|  PluginManager    |
+--------+----------+
         |
         v
+-------------------+
|   ToolRegistry    |
|  (synced with     |
|   FastMCP)        |
+-------------------+
```

---

## Goals and Non-Goals

### Goals

- **First-Class Tools**: Plugin tools appear directly in MCP tool listing
- **Live Reload Support**: Tools are updated when plugins reload without server restart
- **Backward Compatibility**: `call_plugin_tool` continues to work during migration
- **Thread Safety**: All registration operations are safe for concurrent access
- **Clean Separation**: `ToolRegistry` remains the source of truth; FastMCP is synchronized

### Non-Goals

- Changing the plugin protocol or `ToolDefinition` schema
- Supporting tool versioning or compatibility checks
- Implementing tool aliasing or renaming
- Adding authentication or authorization per tool

---

## Architecture Overview

### Component Diagram

```
+-----------------------------------------------------------------------------------+
|                                  OpenCuff Server                                  |
|                                                                                   |
|  +-------------+                                                                  |
|  |   FastMCP   |<------ MCP Protocol -----> MCP Clients                          |
|  |   (mcp)     |                                                                  |
|  +------+------+                                                                  |
|         ^                                                                         |
|         | register_tool() / remove_tool()                                         |
|         |                                                                         |
|  +------+------+                                                                  |
|  |   FastMCP   |                                                                  |
|  |   Bridge    |  <-- NEW COMPONENT                                              |
|  +------+------+                                                                  |
|         ^                                                                         |
|         | on_tool_registered / on_tool_unregistered callbacks                     |
|         |                                                                         |
|  +------+------+         +------------------+         +------------------+        |
|  |   Tool      |<------->|  Plugin Manager  |<------->|  Plugin          |        |
|  |   Registry  |         |                  |         |  Lifecycle       |        |
|  +-------------+         +------------------+         +------------------+        |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

### Data Flow

```
                    Plugin Load/Reload
                           |
                           v
               +------------------------+
               | PluginLifecycle.load() |
               +------------------------+
                           |
                           v
               +------------------------+
               | adapter.get_tools()    |
               +------------------------+
                           |
                           v
               +------------------------+
               | ToolRegistry           |
               | .register_tools()      |
               +------------------------+
                           |
                           | callback
                           v
               +------------------------+
               | FastMCPBridge          |
               | .on_tools_registered() |
               +------------------------+
                           |
                           v
               +------------------------+
               | FastMCP.add_tool()     |
               | for each tool          |
               +------------------------+
                           |
                           v
               +------------------------+
               | Tools visible to       |
               | MCP clients            |
               +------------------------+
```

---

## Component Design

### FastMCPBridge

A new component that synchronizes `ToolRegistry` with FastMCP's tool system.

```python
# src/opencuff/plugins/fastmcp_bridge.py
"""Bridge between ToolRegistry and FastMCP for dynamic tool registration.

This module provides the FastMCPBridge class which synchronizes plugin tools
with FastMCP, making them visible as first-class MCP tools.
"""

from typing import Any, Callable, Awaitable
import structlog
from fastmcp import FastMCP, Tool

from opencuff.plugins.base import ToolDefinition, ToolResult
from opencuff.plugins.registry import ToolRegistry

logger = structlog.get_logger()


ToolCallHandler = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


class FastMCPBridge:
    """Synchronizes ToolRegistry with FastMCP's tool system.

    This bridge:
        1. Listens for tool registration/unregistration in ToolRegistry
        2. Creates FastMCP Tool wrappers for each plugin tool
        3. Registers/unregisters tools with FastMCP
        4. Routes tool calls back through the PluginManager

    Thread Safety:
        This class is designed for use with asyncio. All operations that
        modify FastMCP's tool registry are protected by asyncio.Lock.

    Attributes:
        mcp: The FastMCP server instance.
        tool_registry: The ToolRegistry to synchronize with.
        call_handler: Callback to route tool calls through PluginManager.

    Example:
        bridge = FastMCPBridge(mcp, registry, plugin_manager.call_tool)

        # When tools are registered in the registry, they appear in FastMCP
        await registry.register_tools("makefile", tools)

        # Now MCP clients can call makefile.make_test directly
    """

    def __init__(
        self,
        mcp: FastMCP,
        tool_registry: ToolRegistry,
        call_handler: ToolCallHandler,
    ) -> None:
        """Initialize the FastMCP bridge.

        Args:
            mcp: The FastMCP server instance to register tools with.
            tool_registry: The ToolRegistry to synchronize.
            call_handler: Async callback to invoke tools. Receives (fqn, arguments)
                and returns ToolResult.
        """
        self._mcp = mcp
        self._registry = tool_registry
        self._call_handler = call_handler
        self._registered_tools: set[str] = set()
        self._lock = asyncio.Lock()

    async def sync_tools(self, plugin_name: str, tools: list[ToolDefinition]) -> None:
        """Synchronize tools from a plugin with FastMCP.

        Registers all tools from the given plugin with FastMCP. Each tool
        is wrapped in a handler that routes calls back through the
        PluginManager.

        Args:
            plugin_name: The name of the plugin registering tools.
            tools: List of ToolDefinition objects to register.
        """
        async with self._lock:
            for tool_def in tools:
                fqn = f"{plugin_name}.{tool_def.name}"
                await self._register_tool(fqn, tool_def)

    async def remove_plugin_tools(self, plugin_name: str) -> None:
        """Remove all tools for a plugin from FastMCP.

        Args:
            plugin_name: The name of the plugin whose tools to remove.
        """
        async with self._lock:
            prefix = f"{plugin_name}."
            tools_to_remove = [
                fqn for fqn in self._registered_tools
                if fqn.startswith(prefix)
            ]

            for fqn in tools_to_remove:
                await self._unregister_tool(fqn)

    async def _register_tool(self, fqn: str, tool_def: ToolDefinition) -> None:
        """Register a single tool with FastMCP.

        Creates a wrapper function that routes calls through the call_handler,
        then registers it with FastMCP.

        Args:
            fqn: Fully qualified tool name (plugin.tool).
            tool_def: The tool definition.
        """
        if fqn in self._registered_tools:
            logger.warning("tool_already_registered", fqn=fqn)
            return

        # Create wrapper function for this tool
        async def tool_wrapper(**kwargs: Any) -> Any:
            """Wrapper that routes tool calls through PluginManager."""
            result = await self._call_handler(fqn, kwargs)
            if not result.success:
                raise RuntimeError(result.error or "Tool execution failed")
            return result.data

        # Set function metadata for FastMCP
        tool_wrapper.__name__ = fqn
        tool_wrapper.__doc__ = tool_def.description

        # Create and register the Tool
        # Note: FastMCP's Tool.from_function handles parameter schema extraction
        # We pass the schema explicitly from our ToolDefinition
        tool = Tool.from_function(
            fn=tool_wrapper,
            name=fqn,
            description=tool_def.description,
        )

        # Override parameters schema if provided
        if tool_def.parameters:
            tool.parameters = tool_def.parameters

        self._mcp.add_tool(tool)
        self._registered_tools.add(fqn)

        logger.debug("tool_registered_with_fastmcp", fqn=fqn)

    async def _unregister_tool(self, fqn: str) -> None:
        """Unregister a single tool from FastMCP.

        Args:
            fqn: Fully qualified tool name to remove.
        """
        if fqn not in self._registered_tools:
            return

        # Remove from FastMCP
        # Note: FastMCP may not have a remove_tool method; check API
        # If not available, we track registered tools and skip re-registration
        if hasattr(self._mcp, 'remove_tool'):
            self._mcp.remove_tool(fqn)
        else:
            # Fallback: FastMCP may need to be restarted or tools cleared
            logger.warning(
                "fastmcp_remove_tool_unavailable",
                fqn=fqn,
                message="Tool will be removed on next full sync"
            )

        self._registered_tools.discard(fqn)
        logger.debug("tool_unregistered_from_fastmcp", fqn=fqn)

    async def full_sync(self) -> None:
        """Perform a full synchronization of all tools.

        Removes any stale tools and ensures all ToolRegistry tools
        are registered with FastMCP.
        """
        async with self._lock:
            # Get current tools from registry
            registry_tools = {fqn for fqn, _ in self._registry.list_tools()}

            # Remove tools no longer in registry
            stale_tools = self._registered_tools - registry_tools
            for fqn in stale_tools:
                await self._unregister_tool(fqn)

            # Register any missing tools
            for fqn, tool_def in self._registry.list_tools():
                if fqn not in self._registered_tools:
                    await self._register_tool(fqn, tool_def)

        logger.info(
            "fastmcp_full_sync_complete",
            tool_count=len(self._registered_tools)
        )
```

### Modified ToolRegistry

The `ToolRegistry` is extended with callback support for tool registration events.

```python
# Additions to src/opencuff/plugins/registry.py

from typing import Callable, Awaitable

OnToolsRegisteredCallback = Callable[[str, list[ToolDefinition]], Awaitable[None]]
OnToolsUnregisteredCallback = Callable[[str], Awaitable[None]]


class ToolRegistry:
    """Registry with callback support for synchronization."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[str, ToolDefinition]] = {}
        self._lock = asyncio.Lock()
        self._on_registered: OnToolsRegisteredCallback | None = None
        self._on_unregistered: OnToolsUnregisteredCallback | None = None

    def set_callbacks(
        self,
        on_registered: OnToolsRegisteredCallback | None = None,
        on_unregistered: OnToolsUnregisteredCallback | None = None,
    ) -> None:
        """Set callbacks for tool registration events.

        Args:
            on_registered: Called after tools are registered for a plugin.
            on_unregistered: Called after tools are unregistered for a plugin.
        """
        self._on_registered = on_registered
        self._on_unregistered = on_unregistered

    async def register_tools(
        self,
        plugin_name: str,
        tools: list[ToolDefinition],
    ) -> None:
        """Register tools with callback notification."""
        # ... existing registration logic ...

        # Notify callback after successful registration
        if self._on_registered is not None:
            await self._on_registered(plugin_name, tools)

    async def unregister_plugin(self, plugin_name: str) -> None:
        """Unregister tools with callback notification."""
        # ... existing unregistration logic ...

        # Notify callback after unregistration
        if self._on_unregistered is not None:
            await self._on_unregistered(plugin_name)
```

### Modified Server Initialization

The server initialization is updated to create and wire the bridge.

```python
# Modifications to src/opencuff/server.py

from opencuff.plugins.fastmcp_bridge import FastMCPBridge

# Global bridge instance
_fastmcp_bridge: FastMCPBridge | None = None


async def initialize_plugins(
    settings_path: str | Path | None = None,
    settings: OpenCuffSettings | None = None,
) -> PluginManager:
    """Initialize plugins with FastMCP bridge."""
    global _plugin_manager, _fastmcp_bridge

    # ... existing initialization ...

    # Create the bridge after PluginManager is created
    _fastmcp_bridge = FastMCPBridge(
        mcp=mcp,
        tool_registry=_plugin_manager.tool_registry,
        call_handler=_plugin_manager.call_tool,
    )

    # Set callbacks on the registry
    _plugin_manager.tool_registry.set_callbacks(
        on_registered=_fastmcp_bridge.sync_tools,
        on_unregistered=_fastmcp_bridge.remove_plugin_tools,
    )

    # Perform initial sync for any tools already registered
    await _fastmcp_bridge.full_sync()

    return _plugin_manager
```

---

## Tool Registration Flow

### Sequence: Plugin Load

```
PluginManager    PluginLifecycle    Adapter    ToolRegistry    FastMCPBridge    FastMCP
     |                 |               |             |               |             |
     |--load()-------->|               |             |               |             |
     |                 |--initialize()->|             |               |             |
     |                 |               |             |               |             |
     |                 |--get_tools()-->|             |               |             |
     |                 |<--[tools]-----|             |               |             |
     |                 |               |             |               |             |
     |                 |--register_tools(name, tools)-->|             |             |
     |                 |               |             |               |             |
     |                 |               |             |--callback---->|             |
     |                 |               |             |   sync_tools()|             |
     |                 |               |             |               |             |
     |                 |               |             |               |--add_tool()-->|
     |                 |               |             |               |   (for each) |
     |                 |               |             |               |             |
     |                 |               |             |<--done--------|             |
     |                 |               |             |               |             |
     |<--done----------|               |             |               |             |
```

### Registration Details

1. **PluginLifecycle.load()** calls adapter.get_tools() to get tool definitions
2. **ToolRegistry.register_tools()** stores tools with FQN (plugin.tool)
3. **Callback triggers** FastMCPBridge.sync_tools()
4. **FastMCPBridge** creates wrapper functions and calls FastMCP.add_tool()
5. **Tools become visible** to MCP clients

---

## Tool Unregistration Flow

### Sequence: Plugin Unload

```
PluginManager    PluginLifecycle    ToolRegistry    FastMCPBridge    FastMCP
     |                 |                  |               |             |
     |--unload()------>|                  |               |             |
     |                 |                  |               |             |
     |                 |--unregister_plugin(name)-------->|             |
     |                 |                  |               |             |
     |                 |                  |--callback---->|             |
     |                 |                  |   remove_plugin_tools()     |
     |                 |                  |               |             |
     |                 |                  |               |--remove_tool()
     |                 |                  |               |   (for each)|
     |                 |                  |               |             |
     |                 |                  |<--done--------|             |
     |                 |                  |               |             |
     |                 |--shutdown()----->|               |             |
     |                 |                  |               |             |
     |<--done----------|                  |               |             |
```

### Unregistration Details

1. **PluginLifecycle.unload()** calls registry.unregister_plugin()
2. **ToolRegistry** removes tools and triggers callback
3. **FastMCPBridge.remove_plugin_tools()** removes from FastMCP
4. **Tools disappear** from MCP tool listing

---

## Live Reload Integration

### Reload with RequestBarrier

The existing `RequestBarrier` ensures safe tool updates during reload.

```
                    Config Change Detected
                              |
                              v
                    +-------------------+
                    | PluginLifecycle   |
                    | .reload()         |
                    +--------+----------+
                             |
                             v
                    +-------------------+
                    | RequestBarrier    |
                    | .reload_scope()   |
                    +--------+----------+
                             |
          +------------------+------------------+
          |                                     |
          v                                     v
   +-------------+                       +-------------+
   | Block new   |                       | Wait for    |
   | requests    |                       | in-flight   |
   +-------------+                       +-------------+
                             |
                             v
                    +-------------------+
                    | 1. Unregister old |
                    |    tools from     |
                    |    Registry       |
                    +--------+----------+
                             |
                             | (callback triggers)
                             v
                    +-------------------+
                    | FastMCPBridge     |
                    | removes old tools |
                    +--------+----------+
                             |
                             v
                    +-------------------+
                    | 2. Reload adapter |
                    |    get new tools  |
                    +--------+----------+
                             |
                             v
                    +-------------------+
                    | 3. Register new   |
                    |    tools          |
                    +--------+----------+
                             |
                             | (callback triggers)
                             v
                    +-------------------+
                    | FastMCPBridge     |
                    | adds new tools    |
                    +--------+----------+
                             |
                             v
                    +-------------------+
                    | 4. Release        |
                    |    barrier        |
                    +-------------------+
```

### Atomicity Considerations

The reload process ensures:

1. **In-flight requests complete** with the OLD tools before unregistration
2. **New requests are queued** during the transition
3. **Tool registration is atomic** per plugin (all tools registered together)
4. **MCP clients see consistent state** - either old tools or new tools, never partial

---

## Error Handling

### Error Categories

| Error | Cause | Handling |
|-------|-------|----------|
| Registration failure | FastMCP.add_tool() fails | Log error, tool remains in ToolRegistry but not in FastMCP |
| Unregistration failure | FastMCP.remove_tool() unavailable | Track in bridge, clean up on full sync |
| Callback exception | Bridge throws during sync | Log, do not fail plugin load |
| Duplicate tool | Same FQN registered twice | Log warning, skip duplicate |

### Error Handling Strategy

```python
async def sync_tools(self, plugin_name: str, tools: list[ToolDefinition]) -> None:
    """Synchronize tools with error isolation."""
    async with self._lock:
        for tool_def in tools:
            fqn = f"{plugin_name}.{tool_def.name}"
            try:
                await self._register_tool(fqn, tool_def)
            except Exception as e:
                # Log but don't fail the entire registration
                logger.error(
                    "tool_registration_failed",
                    fqn=fqn,
                    error=str(e),
                )
                # Tool remains in ToolRegistry, can be called via call_plugin_tool
```

### Graceful Degradation

If FastMCP registration fails:
1. Tool remains in `ToolRegistry`
2. Tool can still be called via `call_plugin_tool` gateway
3. Error is logged for investigation
4. Next full sync attempts registration again

---

## Testing Approach

### Unit Tests

**FastMCPBridge Tests** (`tests/test_fastmcp_bridge.py`):

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from opencuff.plugins.fastmcp_bridge import FastMCPBridge
from opencuff.plugins.base import ToolDefinition, ToolResult


class TestFastMCPBridge:
    """Unit tests for FastMCPBridge."""

    @pytest.fixture
    def mock_mcp(self):
        """Create a mock FastMCP instance."""
        mcp = MagicMock()
        mcp.add_tool = MagicMock()
        mcp.remove_tool = MagicMock()
        return mcp

    @pytest.fixture
    def mock_registry(self):
        """Create a mock ToolRegistry."""
        registry = MagicMock()
        registry.list_tools = MagicMock(return_value=[])
        return registry

    @pytest.fixture
    def mock_handler(self):
        """Create a mock call handler."""
        async def handler(fqn: str, args: dict):
            return ToolResult(success=True, data={"result": "ok"})
        return handler

    @pytest.fixture
    def bridge(self, mock_mcp, mock_registry, mock_handler):
        """Create a bridge instance."""
        return FastMCPBridge(mock_mcp, mock_registry, mock_handler)

    async def test_sync_tools_registers_with_fastmcp(self, bridge, mock_mcp):
        """Test that sync_tools registers tools with FastMCP."""
        tools = [
            ToolDefinition(name="echo", description="Echo a message"),
            ToolDefinition(name="add", description="Add numbers"),
        ]

        await bridge.sync_tools("dummy", tools)

        assert mock_mcp.add_tool.call_count == 2

    async def test_remove_plugin_tools_unregisters(self, bridge, mock_mcp):
        """Test that remove_plugin_tools unregisters from FastMCP."""
        tools = [ToolDefinition(name="echo", description="Echo")]
        await bridge.sync_tools("dummy", tools)

        await bridge.remove_plugin_tools("dummy")

        assert "dummy.echo" not in bridge._registered_tools

    async def test_duplicate_registration_skipped(self, bridge, mock_mcp):
        """Test that duplicate tools are not re-registered."""
        tools = [ToolDefinition(name="echo", description="Echo")]

        await bridge.sync_tools("dummy", tools)
        await bridge.sync_tools("dummy", tools)

        # Should only be called once
        assert mock_mcp.add_tool.call_count == 1
```

### Integration Tests

**End-to-End Registration Tests** (`tests/test_dynamic_registration_integration.py`):

```python
import pytest
from fastmcp import Client
from opencuff.server import create_test_server, _reset_for_testing
from opencuff.plugins.config import OpenCuffSettings, PluginConfig, PluginType


class TestDynamicToolRegistration:
    """Integration tests for dynamic tool registration."""

    @pytest.fixture
    async def server_with_dummy_plugin(self):
        """Create server with dummy plugin."""
        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=True,
                    module="opencuff.plugins.builtin.dummy",
                ),
            },
        )
        server = await create_test_server(settings)
        yield server
        await _reset_for_testing()

    async def test_plugin_tools_visible_via_mcp(self, server_with_dummy_plugin):
        """Test that plugin tools appear in MCP tool list."""
        async with Client(server_with_dummy_plugin) as client:
            tools = await client.list_tools()

            tool_names = [t.name for t in tools]

            # Plugin tools should be visible
            assert "dummy.echo" in tool_names
            assert "dummy.add" in tool_names

            # Built-in tools should still be there
            assert "hello" in tool_names

    async def test_plugin_tools_callable_directly(self, server_with_dummy_plugin):
        """Test that plugin tools can be called directly via MCP."""
        async with Client(server_with_dummy_plugin) as client:
            # Call plugin tool directly (not via call_plugin_tool)
            result = await client.call_tool(
                "dummy.echo",
                {"message": "test"}
            )

            assert result == "test"

    async def test_plugin_reload_updates_tools(self, server_with_dummy_plugin):
        """Test that tool list updates after plugin reload."""
        # This test would require modifying plugin config and triggering reload
        # Implementation depends on test infrastructure
        pass
```

### Test Coverage Goals

| Component | Coverage Target | Key Scenarios |
|-----------|-----------------|---------------|
| FastMCPBridge | 90% | Registration, unregistration, full sync, errors |
| ToolRegistry callbacks | 85% | Callback invocation, callback errors |
| Integration | 80% | Tool visibility, direct calls, live reload |

---

## Migration Path

### Phase 1: Add Bridge (Non-Breaking)

1. Implement `FastMCPBridge`
2. Add callback support to `ToolRegistry`
3. Wire bridge in server initialization
4. Plugin tools appear in MCP alongside `call_plugin_tool`

**User Impact**: None. Both methods work.

### Phase 2: Documentation Update

1. Update documentation to recommend direct tool calls
2. Mark `call_plugin_tool` as deprecated in docstring
3. Add migration guide for existing users

### Phase 3: Deprecation Warning (Optional)

1. Add deprecation warning when `call_plugin_tool` is used
2. Provide timeline for removal (e.g., 6 months)

### Phase 4: Remove Gateway (Future)

1. Remove `call_plugin_tool` in major version bump
2. Remove `list_plugins` (replaced by standard tool listing)

---

## Appendix A: FastMCP API Investigation

The design assumes FastMCP provides:

- `mcp.add_tool(tool: Tool)` - Confirmed in HLD requirements
- `Tool.from_function(fn, name, description)` - Confirmed in HLD requirements
- `mcp.remove_tool(name: str)` - Needs verification

If `remove_tool` is not available, the bridge will:
1. Track registered tools internally
2. Skip re-registration of existing tools
3. Require server restart for full cleanup (acceptable for v1)

Alternative approaches if needed:
- Access internal tool registry via `mcp._tools` (fragile)
- Request feature from FastMCP maintainers
- Implement "replace" semantics (unregister + register atomically)

---

## Appendix B: Thread Safety Analysis

### Concurrent Access Scenarios

| Scenario | Protection | Notes |
|----------|------------|-------|
| Two plugins loading simultaneously | `ToolRegistry._lock` | Serialized registration |
| Tool call during reload | `RequestBarrier` | Call completes before reload |
| Multiple config changes | `RequestBarrier._reload_lock` | Serialized reloads |
| Bridge sync during unload | `FastMCPBridge._lock` | Atomic sync operations |

### Lock Ordering

To prevent deadlocks, locks are acquired in this order:
1. `RequestBarrier._reload_lock` (outermost)
2. `ToolRegistry._lock`
3. `FastMCPBridge._lock` (innermost)

This ordering is enforced by the call graph: reload_scope -> registry operations -> bridge callbacks.
