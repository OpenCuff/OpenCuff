"""Core plugin interfaces and base classes.

This module defines the fundamental building blocks of the OpenCuff plugin system:
    - PluginState: Enumeration of plugin lifecycle states
    - ToolDefinition: Describes a tool provided by a plugin
    - ToolResult: Result of a tool invocation
    - DiscoveryResult: Result of plugin discovery for a directory
    - CLIArgument: Definition of a positional CLI argument
    - CLIOption: Definition of a CLI option/flag
    - CLICommand: Definition of a CLI command exposed by a plugin
    - PluginProtocol: Abstract base class that all plugin adapters must implement
    - InSourcePlugin: Base class for in-source Python plugins
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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


@dataclass
class DiscoveryResult:
    """Result of plugin discovery for a directory.

    Attributes:
        applicable: Whether this plugin is applicable to the directory.
        confidence: Confidence score between 0.0 and 1.0.
            Use this to indicate certainty of applicability:
            - 1.0: Definitive match (e.g., Makefile exists for makefile plugin)
            - 0.8: Strong match (e.g., file exists but may not be the intended use)
            - 0.5: Possible match (e.g., partial indicators found)
            - 0.0: No match
        suggested_config: Suggested plugin configuration based on discovery.
            This should be a complete, working configuration that can be used
            directly in settings.yml.
        description: Human-readable description of what was discovered.
        warnings: Warnings about the discovery (e.g., configuration conflicts).
        discovered_items: List of discovered items (targets, scripts, etc.) for display.
    """

    applicable: bool
    confidence: float
    suggested_config: dict[str, Any]
    description: str
    warnings: list[str] = field(default_factory=list)
    discovered_items: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate field values after initialization."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {self.confidence}"
            )


@dataclass
class CLIArgument:
    """Definition of a positional CLI argument.

    Attributes:
        name: Argument name.
        help: Help text for the argument.
        required: Whether the argument is required (default: True).
        default: Default value if not required (default: None).
    """

    name: str
    help: str
    required: bool = True
    default: Any = None


@dataclass
class CLIOption:
    """Definition of a CLI option/flag.

    Attributes:
        name: Option name (e.g., '--dry-run' or '-d').
        help: Help text for the option.
        is_flag: True if this is a boolean flag (default: False).
        default: Default value (default: None).
        type: Expected type for the option value (default: str).
    """

    name: str
    help: str
    is_flag: bool = False
    default: Any = None
    type: type = str


@dataclass
class CLICommand:
    """Definition of a CLI command exposed by a plugin.

    Attributes:
        name: Command name (e.g., 'list-targets').
            This becomes the subcommand: cuff <plugin> <name>
        help: Help text displayed for the command.
        callback: The function to call when the command is invoked.
            Should be a classmethod or staticmethod that can run without
            an instantiated plugin.
        arguments: Positional arguments for the command.
        options: Optional flags for the command.
    """

    name: str
    help: str
    callback: Callable[..., Any]
    arguments: list[CLIArgument] = field(default_factory=list)
    options: list[CLIOption] = field(default_factory=list)


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

    @classmethod
    def discover(cls, directory: Path) -> DiscoveryResult:
        """Discover if this plugin is applicable to the given directory.

        This is a CLASS METHOD that runs WITHOUT instantiating the plugin.
        It should:
        1. Check for the presence of relevant files
        2. Parse files minimally to extract useful information
        3. Return a suggested configuration

        The discovery should be:
        - Fast: Avoid expensive operations
        - Safe: Never execute code or modify files
        - Informative: Provide useful descriptions and warnings

        Args:
            directory: The directory to scan for applicable files.

        Returns:
            DiscoveryResult indicating applicability and suggested config.

        Example:
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                makefile = directory / "Makefile"
                if not makefile.exists():
                    return DiscoveryResult(
                        applicable=False,
                        confidence=0.0,
                        suggested_config={},
                        description="No Makefile found",
                    )

                targets = cls._extract_targets_static(makefile)
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={
                        "makefile_path": "./Makefile",
                        "targets": "*",
                    },
                    description=f"Found Makefile with {len(targets)} targets",
                    discovered_items=targets[:10],
                )
        """
        # Default implementation: plugin does not support discovery
        return DiscoveryResult(
            applicable=False,
            confidence=0.0,
            suggested_config={},
            description="This plugin does not support automatic discovery",
        )

    @classmethod
    def get_cli_commands(cls) -> list[CLICommand]:
        """Return CLI commands this plugin provides.

        Override to expose plugin-specific CLI subcommands.
        These commands are registered under:
            cuff <plugin-name> <command-name>

        Example:
            @classmethod
            def get_cli_commands(cls) -> list[CLICommand]:
                return [
                    CLICommand(
                        name="list-targets",
                        help="List available Makefile targets",
                        callback=cls._cli_list_targets,
                    ),
                ]

        Returns:
            List of CLICommand definitions, or empty list if no CLI support.
        """
        return []

    @classmethod
    def get_plugin_metadata(cls) -> dict[str, Any]:
        """Return metadata about this plugin for CLI display.

        Returns:
            Dictionary with plugin metadata:
            - name: Human-readable plugin name
            - description: Short description
            - version: Plugin version (optional)
        """
        return {
            "name": cls.__name__,
            "description": cls.__doc__ or "No description",
        }
