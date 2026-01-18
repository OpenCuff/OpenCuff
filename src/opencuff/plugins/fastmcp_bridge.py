"""Bridge between ToolRegistry and FastMCP for dynamic tool registration.

This module provides the FastMCPBridge class which synchronizes plugin tools
with FastMCP, making them visible as first-class MCP tools to clients.

Classes:
    - FastMCPBridge: Synchronizes ToolRegistry with FastMCP's tool system

The bridge:
    1. Listens for tool registration/unregistration events
    2. Creates FastMCP Tool wrappers for each plugin tool
    3. Registers/unregisters tools with FastMCP
    4. Routes tool calls back through the PluginManager

Example:
    bridge = FastMCPBridge(mcp, registry, plugin_manager.call_tool)
    await bridge.sync_tools("dummy", tools)
    # Now MCP clients can call dummy.echo directly
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools import FunctionTool

from opencuff.plugins.base import ToolDefinition, ToolResult
from opencuff.plugins.registry import ToolRegistry

logger = structlog.get_logger()


ToolCallHandler = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


class FastMCPBridge:
    """Synchronizes ToolRegistry with FastMCP's tool system.

    This bridge creates wrapper functions for plugin tools and registers them
    with FastMCP, making them visible to MCP clients as first-class tools.

    Thread Safety:
        All operations that modify the registered tools set are protected
        by an asyncio.Lock to ensure thread-safe concurrent access.

    Attributes:
        registered_tools: Set of fully qualified tool names registered with FastMCP.

    Example:
        bridge = FastMCPBridge(mcp, registry, plugin_manager.call_tool)

        # Sync tools from a plugin
        await bridge.sync_tools("makefile", tools)

        # Now MCP clients can call makefile.make_test directly

        # Remove tools when plugin unloads
        await bridge.remove_plugin_tools("makefile")
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
                and returns ToolResult. Typically this is PluginManager.call_tool.
        """
        self._mcp = mcp
        self._registry = tool_registry
        self._call_handler = call_handler
        self._registered_tools: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def registered_tools(self) -> set[str]:
        """Return a copy of the set of registered tool FQNs.

        Returns:
            Set of fully qualified tool names currently registered with FastMCP.
        """
        return self._registered_tools.copy()

    async def sync_tools(
        self,
        plugin_name: str,
        tools: list[ToolDefinition],
    ) -> None:
        """Synchronize tools from a plugin with FastMCP.

        Registers all tools from the given plugin with FastMCP. Each tool
        is wrapped in a handler that routes calls back through the
        PluginManager.

        Args:
            plugin_name: The name of the plugin registering tools.
            tools: List of ToolDefinition objects to register.

        Note:
            Registration errors for individual tools are logged but do not
            fail the entire sync operation. Tools that fail to register
            can still be called via the call_plugin_tool gateway.
        """
        async with self._lock:
            for tool_def in tools:
                fqn = f"{plugin_name}.{tool_def.name}"
                try:
                    await self._register_tool(fqn, tool_def)
                except Exception as e:
                    logger.error(
                        "tool_registration_failed",
                        fqn=fqn,
                        error=str(e),
                    )

    async def remove_plugin_tools(self, plugin_name: str) -> None:
        """Remove all tools for a plugin from FastMCP.

        Args:
            plugin_name: The name of the plugin whose tools to remove.
        """
        async with self._lock:
            prefix = f"{plugin_name}."
            tools_to_remove = [
                fqn for fqn in self._registered_tools if fqn.startswith(prefix)
            ]

            for fqn in tools_to_remove:
                await self._unregister_tool(fqn)

    async def full_sync(self) -> None:
        """Perform a full synchronization of all tools.

        Removes any stale tools (registered with FastMCP but no longer in
        ToolRegistry) and ensures all ToolRegistry tools are registered
        with FastMCP.

        This is typically called during initialization to sync any tools
        that were registered before the bridge was created.
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
                    try:
                        await self._register_tool(fqn, tool_def)
                    except Exception as e:
                        logger.error(
                            "tool_registration_failed_during_sync",
                            fqn=fqn,
                            error=str(e),
                        )

        logger.info(
            "fastmcp_full_sync_complete",
            tool_count=len(self._registered_tools),
        )

    async def _register_tool(self, fqn: str, tool_def: ToolDefinition) -> None:
        """Register a single tool with FastMCP.

        Creates a wrapper function that routes calls through the call_handler,
        then registers it with FastMCP using FunctionTool directly.

        We use FunctionTool directly instead of Tool.from_function because:
        1. from_function doesn't support **kwargs (which we need for dynamic args)
        2. FunctionTool allows us to specify the parameters schema explicitly
        3. FastMCP validates arguments against our schema, not the function signature

        Args:
            fqn: Fully qualified tool name (plugin.tool).
            tool_def: The tool definition.

        Note:
            This method must be called while holding self._lock.
        """
        if fqn in self._registered_tools:
            logger.warning("tool_already_registered", fqn=fqn)
            return

        # Create wrapper function for this tool
        # We capture fqn in the closure to route calls correctly
        captured_fqn = fqn
        call_handler = self._call_handler

        async def tool_wrapper(**kwargs: Any) -> Any:
            """Wrapper that routes tool calls through PluginManager."""
            result = await call_handler(captured_fqn, kwargs)
            if not result.success:
                raise RuntimeError(result.error or "Tool execution failed")
            return result.data

        # Set function metadata
        tool_wrapper.__name__ = fqn
        tool_wrapper.__doc__ = tool_def.description

        # Build parameters schema - default to empty object if not provided
        parameters = tool_def.parameters or {"type": "object", "properties": {}}

        # Create FunctionTool directly to bypass the **kwargs restriction
        # in from_function. FastMCP's FunctionTool.run() validates arguments
        # against our schema, then calls our function with **kwargs.
        tool = FunctionTool(
            name=fqn,
            description=tool_def.description,
            parameters=parameters,
            fn=tool_wrapper,
            task_config=TaskConfig(mode="forbidden"),
            tags=set(),
            enabled=True,
        )

        self._mcp.add_tool(tool)
        self._registered_tools.add(fqn)

        logger.debug("tool_registered_with_fastmcp", fqn=fqn)

    async def _unregister_tool(self, fqn: str) -> None:
        """Unregister a single tool from FastMCP.

        Args:
            fqn: Fully qualified tool name to remove.

        Note:
            This method must be called while holding self._lock.
        """
        if fqn not in self._registered_tools:
            return

        self._mcp.remove_tool(fqn)
        self._registered_tools.discard(fqn)

        logger.debug("tool_unregistered_from_fastmcp", fqn=fqn)
