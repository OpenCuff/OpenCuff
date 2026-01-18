"""Tool registry with namespace support.

This module provides a thread-safe registry for managing plugin tools with
automatic namespacing to prevent collisions.

Classes:
    - ToolRegistry: Registry for all plugin tools with namespace support

Type aliases:
    - OnToolsRegisteredCallback: Async callback invoked after tools are registered
    - OnToolsUnregisteredCallback: Async callback invoked after tools are unregistered

Tool names are stored with a fully qualified name (FQN) format:
    {plugin_name}.{tool_name}

Example:
    - Plugin "makefile" with tool "build" becomes "makefile.build"
    - Plugin "git" with tool "status" becomes "git.status"
"""

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from opencuff.plugins.base import ToolDefinition
from opencuff.plugins.errors import PluginError, PluginErrorCode

logger = structlog.get_logger()


# Type aliases for callbacks
OnToolsRegisteredCallback = Callable[[str, list[ToolDefinition]], Awaitable[None]]
OnToolsUnregisteredCallback = Callable[[str], Awaitable[None]]


class ToolRegistry:
    """Registry for all plugin tools with namespace support.

    Provides thread-safe registration and lookup of tools using fully
    qualified names (FQN) in the format: {plugin_name}.{tool_name}

    Attributes:
        _tools: Internal dictionary mapping FQN to (plugin_name, ToolDefinition).
        _lock: Async lock for thread-safe operations.

    Example:
        registry = ToolRegistry()

        # Register tools from a plugin
        await registry.register_tools("my_plugin", [tool1, tool2])

        # Look up a tool by FQN
        result = registry.get_tool("my_plugin.tool1")
        if result:
            plugin_name, tool = result

        # List all tools
        for fqn, tool in registry.list_tools():
            print(f"{fqn}: {tool.description}")

        # Unregister a plugin
        await registry.unregister_plugin("my_plugin")
    """

    def __init__(self) -> None:
        """Initialize an empty tool registry."""
        # Maps FQN -> (plugin_name, ToolDefinition)
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

        Callbacks are invoked after successful registration/unregistration.
        If a callback raises an exception, it is logged but does not affect
        the registration operation.

        Args:
            on_registered: Called after tools are registered for a plugin.
                Receives (plugin_name, tools) arguments.
            on_unregistered: Called after tools are unregistered for a plugin.
                Receives (plugin_name) argument.
        """
        self._on_registered = on_registered
        self._on_unregistered = on_unregistered

    def _make_fqn(self, plugin_name: str, tool_name: str) -> str:
        """Create a fully qualified tool name.

        Args:
            plugin_name: The name of the plugin.
            tool_name: The name of the tool within the plugin.

        Returns:
            Fully qualified name in format: {plugin_name}.{tool_name}
        """
        return f"{plugin_name}.{tool_name}"

    async def register_tools(
        self,
        plugin_name: str,
        tools: list[ToolDefinition],
    ) -> None:
        """Register tools from a plugin with namespace prefix.

        All tools are registered with their fully qualified name (FQN):
        {plugin_name}.{tool_name}

        After successful registration, the on_registered callback (if set) is
        invoked with the plugin name and list of tools. Callback errors are
        logged but do not affect the registration.

        Args:
            plugin_name: The name of the plugin registering the tools.
            tools: List of ToolDefinition objects to register.

        Raises:
            PluginError: If a tool with the same FQN already exists
                (either from duplicate tool names within the plugin or
                from a previously registered plugin that wasn't unregistered).
        """
        async with self._lock:
            # First pass: check for duplicates
            seen_names: set[str] = set()
            for tool in tools:
                fqn = self._make_fqn(plugin_name, tool.name)

                # Check for duplicate within this batch
                if tool.name in seen_names:
                    raise PluginError(
                        code=PluginErrorCode.CONFIG_INVALID,
                        message=f"Duplicate tool name: {fqn}",
                        plugin_name=plugin_name,
                    )
                seen_names.add(tool.name)

                # Check for collision with existing tools
                if fqn in self._tools:
                    raise PluginError(
                        code=PluginErrorCode.CONFIG_INVALID,
                        message=f"Duplicate tool name: {fqn}",
                        plugin_name=plugin_name,
                    )

            # Second pass: register all tools
            for tool in tools:
                fqn = self._make_fqn(plugin_name, tool.name)
                self._tools[fqn] = (plugin_name, tool)

        # Notify callback after successful registration (outside lock)
        if self._on_registered is not None:
            try:
                await self._on_registered(plugin_name, tools)
            except Exception as e:
                logger.error(
                    "on_registered_callback_failed",
                    plugin_name=plugin_name,
                    error=str(e),
                )

    async def unregister_plugin(self, plugin_name: str) -> None:
        """Remove all tools from a plugin.

        After successful unregistration, the on_unregistered callback (if set)
        is invoked with the plugin name only if tools were actually removed.
        Callback errors are logged but do not affect the unregistration.

        Args:
            plugin_name: The name of the plugin to unregister.

        Note:
            This operation is idempotent - calling it for a plugin that
            isn't registered (or was already unregistered) is safe.
        """
        tools_removed = False
        async with self._lock:
            prefix = f"{plugin_name}."
            to_remove = [fqn for fqn in self._tools if fqn.startswith(prefix)]
            for fqn in to_remove:
                del self._tools[fqn]
            tools_removed = len(to_remove) > 0

        # Notify callback after unregistration (outside lock)
        # Only call if tools were actually removed
        if tools_removed and self._on_unregistered is not None:
            try:
                await self._on_unregistered(plugin_name)
            except Exception as e:
                logger.error(
                    "on_unregistered_callback_failed",
                    plugin_name=plugin_name,
                    error=str(e),
                )

    def get_tool(self, fqn: str) -> tuple[str, ToolDefinition] | None:
        """Look up a tool by fully qualified name.

        Args:
            fqn: Fully qualified tool name (e.g., "my_plugin.my_tool").

        Returns:
            Tuple of (plugin_name, ToolDefinition) if found, None otherwise.

        Thread Safety:
            This method is safe to call without acquiring the lock because:
            1. Python's GIL ensures dict.get() is atomic
            2. We only read a single reference from the dictionary
            3. The returned tuple is immutable (ToolDefinition is frozen)

            However, note that the registry's contents may change between
            this call and subsequent operations if other tasks modify it.
        """
        return self._tools.get(fqn)

    def list_tools(self) -> list[tuple[str, ToolDefinition]]:
        """List all registered tools.

        Returns:
            List of tuples containing (fqn, ToolDefinition) for each tool.
            The list is a snapshot of the current registry state.
        """
        return [(fqn, tool) for fqn, (_, tool) in self._tools.items()]

    def get_tools_for_plugin(
        self, plugin_name: str
    ) -> list[tuple[str, ToolDefinition]]:
        """Get all tools registered by a specific plugin.

        Args:
            plugin_name: The name of the plugin.

        Returns:
            List of tuples containing (fqn, ToolDefinition) for each tool
            registered by the specified plugin.
        """
        prefix = f"{plugin_name}."
        return [
            (fqn, tool)
            for fqn, (pname, tool) in self._tools.items()
            if fqn.startswith(prefix)
        ]

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)

    def __contains__(self, fqn: str) -> bool:
        """Check if a tool is registered by FQN."""
        return fqn in self._tools
