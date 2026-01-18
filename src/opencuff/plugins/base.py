"""Core plugin interfaces and base classes.

This module defines the fundamental building blocks of the OpenCuff plugin system:
    - PluginState: Enumeration of plugin lifecycle states
    - ToolDefinition: Describes a tool provided by a plugin
    - ToolResult: Result of a tool invocation
    - PluginProtocol: Abstract base class that all plugin adapters must implement
    - InSourcePlugin: Base class for in-source Python plugins
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PluginState(str, Enum):
    """Lifecycle states for a plugin.

    State transitions:
        UNLOADED -> INITIALIZING: load() called
        INITIALIZING -> ACTIVE: initialization succeeds
        INITIALIZING -> ERROR: initialization fails
        ACTIVE -> ACTIVE: reload() with hot reload
        ACTIVE -> ERROR: runtime error or health check failure
        ACTIVE -> UNLOADED: unload() called
        ERROR -> RECOVERING: retry triggered
        RECOVERING -> ACTIVE: recovery succeeds
        RECOVERING -> UNLOADED: max restarts exceeded
    """

    UNLOADED = "unloaded"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    ERROR = "error"
    RECOVERING = "recovering"


@dataclass(frozen=True)
class ToolDefinition:
    """Describes a tool provided by a plugin.

    Attributes:
        name: Unique identifier for the tool within the plugin.
        description: Human-readable description of what the tool does.
        parameters: JSON Schema describing the tool's input parameters.
        returns: JSON Schema describing the tool's return value.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    returns: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result of a tool invocation.

    Attributes:
        success: Whether the tool executed successfully.
        data: The result data if successful (can be any JSON-serializable value).
        error: Error message if the tool failed.
    """

    success: bool
    data: Any | None = None
    error: str | None = None


class PluginProtocol(ABC):
    """Protocol that all plugin adapters must implement.

    This abstract base class defines the interface for communicating with plugins
    regardless of their type (in-source, process, or HTTP).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique identifier for this plugin."""
        ...

    @abstractmethod
    async def initialize(self, config: dict[str, Any]) -> None:
        """Initialize the plugin with its configuration.

        Args:
            config: Plugin-specific configuration dictionary.

        Raises:
            PluginError: If initialization fails.
        """
        ...

    @abstractmethod
    async def get_tools(self) -> list[ToolDefinition]:
        """Return list of tools provided by this plugin.

        Returns:
            List of ToolDefinition objects describing available tools.
        """
        ...

    @abstractmethod
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Invoke a tool with the given arguments.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Dictionary of arguments to pass to the tool.

        Returns:
            ToolResult containing success status and data or error.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the plugin is healthy and responsive.

        Returns:
            True if the plugin is healthy, False otherwise.
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources and shut down the plugin."""
        ...


class InSourcePlugin(ABC):
    """Abstract base class for in-source plugins.

    In-source plugins are Python modules within the opencuff.plugins namespace
    that are loaded via importlib. They provide the fastest execution with no
    IPC overhead.

    This is an abstract class - subclasses MUST implement:
        - get_tools(): Return list of tools provided by this plugin
        - call_tool(): Handle tool invocations

    Subclasses may override:
        - initialize(): Called when plugin is loaded
        - shutdown(): Called when plugin is unloaded
        - health_check(): Called periodically to check plugin health
        - on_config_reload(): Called when configuration changes

    Example:
        class MyPlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return [
                    ToolDefinition(
                        name="my_tool",
                        description="Does something useful",
                        parameters={"type": "object"},
                        returns={"type": "string"},
                    )
                ]

            async def call_tool(
                self, tool_name: str, arguments: dict
            ) -> ToolResult:
                if tool_name == "my_tool":
                    return ToolResult(success=True, data="result")
                return ToolResult(success=False, error="Unknown tool")
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize with plugin-specific configuration.

        Args:
            config: Plugin-specific configuration dictionary from settings.yml.
        """
        self.config = config

    @abstractmethod
    def get_tools(self) -> list[ToolDefinition]:
        """Return tools provided by this plugin.

        Subclasses MUST implement this method to define their tools.

        Returns:
            List of ToolDefinition objects describing available tools.
        """
        ...

    @abstractmethod
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Handle tool invocation.

        Subclasses MUST implement this method to handle tool calls.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Dictionary of arguments to pass to the tool.

        Returns:
            ToolResult containing success status and data or error.
        """
        ...

    async def initialize(self) -> None:  # noqa: B027
        """Called when plugin is loaded.

        Override this method to perform initialization tasks such as:
            - Establishing connections
            - Loading resources
            - Validating configuration

        The default implementation does nothing.
        """

    async def shutdown(self) -> None:  # noqa: B027
        """Called when plugin is unloaded.

        Override this method to perform cleanup tasks such as:
            - Closing connections
            - Releasing resources
            - Flushing buffers

        The default implementation does nothing.
        """

    async def health_check(self) -> bool:
        """Check if the plugin is healthy and responsive.

        Override this method to implement custom health checks.

        Returns:
            True if the plugin is healthy, False otherwise.
            Default implementation always returns True.
        """
        return True

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Called when plugin configuration changes.

        Default behavior performs a full shutdown/initialize cycle.
        Override this method for graceful configuration updates without
        full restart.

        Args:
            new_config: The new configuration dictionary.
        """
        await self.shutdown()
        self.config = new_config
        await self.initialize()
