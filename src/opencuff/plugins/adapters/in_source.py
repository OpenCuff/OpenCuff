"""In-source plugin adapter.

This module provides the adapter for loading Python modules as plugins
using importlib. In-source plugins are the fastest type (no IPC overhead)
and have full access to the Python ecosystem.

Classes:
    - InSourceAdapter: Loads and manages in-source Python plugins
"""

import importlib
import importlib.util
from typing import Any

import structlog

from opencuff.plugins.base import (
    InSourcePlugin,
    PluginProtocol,
    ToolDefinition,
    ToolResult,
)
from opencuff.plugins.errors import PluginError, PluginErrorCode

logger = structlog.get_logger()


class InSourceAdapter(PluginProtocol):
    """Adapter for in-source Python plugins.

    Loads Python modules via importlib and wraps them to implement
    the PluginProtocol interface.

    The adapter expects modules to contain a class that inherits from
    InSourcePlugin. By default, it looks for a class named "Plugin",
    but this can be configured.

    Attributes:
        _name: The plugin name.
        _module_path: The Python module path (e.g., "opencuff.plugins.dummy").
        _plugin_class_name: The name of the plugin class in the module.
        _config: Plugin-specific configuration.
        _plugin: The instantiated plugin instance.
        _allowed_prefixes: List of allowed module path prefixes for security.

    Example:
        adapter = InSourceAdapter(
            name="my_plugin",
            module_path="opencuff.plugins.builtin.dummy",
            config={"key": "value"},
        )
        await adapter.initialize({})
        tools = await adapter.get_tools()
        result = await adapter.call_tool("echo", {"message": "hello"})
        await adapter.shutdown()
    """

    # Default allowed module path prefixes for security
    DEFAULT_ALLOWED_PREFIXES: list[str] = ["opencuff.plugins."]

    def __init__(
        self,
        name: str,
        module_path: str,
        config: dict[str, Any] | None = None,
        plugin_class_name: str = "Plugin",
        allowed_prefixes: list[str] | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            name: The name of the plugin.
            module_path: Python module path to load.
            config: Plugin-specific configuration to pass to the plugin.
            plugin_class_name: Name of the plugin class in the module.
            allowed_prefixes: List of allowed module path prefixes for security.
                Defaults to DEFAULT_ALLOWED_PREFIXES if not provided.

        Raises:
            PluginError: If the module path is not in an allowed namespace.
        """
        self._name = name
        self._module_path = module_path
        self._plugin_class_name = plugin_class_name
        self._config = config or {}
        self._plugin: InSourcePlugin | None = None
        self._module: Any = None
        self._allowed_prefixes = (
            allowed_prefixes
            if allowed_prefixes is not None
            else self.DEFAULT_ALLOWED_PREFIXES
        )

        # Validate module path
        if not self._validate_module_path(module_path):
            raise PluginError(
                code=PluginErrorCode.CONFIG_INVALID,
                message=f"Module path not in allowed namespace: {module_path}",
                plugin_name=name,
            )

    @property
    def name(self) -> str:
        """Return the plugin name."""
        return self._name

    def _validate_module_path(self, module_path: str) -> bool:
        """Validate that the module path is in an allowed namespace.

        Args:
            module_path: The module path to validate.

        Returns:
            True if the module path is allowed, False otherwise.
        """
        return any(module_path.startswith(prefix) for prefix in self._allowed_prefixes)

    async def initialize(self, config: dict[str, Any]) -> None:
        """Load the module and initialize the plugin.

        Args:
            config: Configuration passed from the plugin manager.
                This is merged with the config passed to __init__.

        Raises:
            PluginError: If the module cannot be loaded or the plugin
                class cannot be found or instantiated.
        """
        # Merge configs (init config takes precedence)
        merged_config = {**config, **self._config}

        try:
            # Load the module
            self._module = importlib.import_module(self._module_path)

            # Get the plugin class
            if not hasattr(self._module, self._plugin_class_name):
                raise PluginError(
                    code=PluginErrorCode.LOAD_FAILED,
                    message=f"Module does not have class '{self._plugin_class_name}'",
                    plugin_name=self._name,
                )

            plugin_class = getattr(self._module, self._plugin_class_name)

            # Validate it's a proper plugin class
            if not issubclass(plugin_class, InSourcePlugin):
                raise PluginError(
                    code=PluginErrorCode.LOAD_FAILED,
                    message=f"Class '{self._plugin_class_name}' is not InSourcePlugin",
                    plugin_name=self._name,
                )

            # Instantiate and initialize the plugin
            self._plugin = plugin_class(merged_config)
            await self._plugin.initialize()

            logger.info(
                "plugin_loaded",
                plugin=self._name,
                module=self._module_path,
            )

        except PluginError:
            raise
        except ImportError as e:
            raise PluginError(
                code=PluginErrorCode.LOAD_FAILED,
                message=f"Failed to import module: {e}",
                plugin_name=self._name,
                cause=e,
            ) from e
        except Exception as e:
            raise PluginError(
                code=PluginErrorCode.INIT_FAILED,
                message=f"Failed to initialize plugin: {e}",
                plugin_name=self._name,
                cause=e,
            ) from e

    async def get_tools(self) -> list[ToolDefinition]:
        """Return the tools provided by this plugin.

        Returns:
            List of ToolDefinition objects.

        Raises:
            PluginError: If the plugin is not initialized.
        """
        if self._plugin is None:
            raise PluginError(
                code=PluginErrorCode.PLUGIN_UNHEALTHY,
                message="Plugin not initialized",
                plugin_name=self._name,
            )

        return self._plugin.get_tools()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Invoke a tool on the plugin.

        Args:
            tool_name: The name of the tool to invoke (without namespace).
            arguments: Arguments to pass to the tool.

        Returns:
            ToolResult containing success status and data or error.

        Raises:
            PluginError: If the plugin is not initialized.
        """
        if self._plugin is None:
            raise PluginError(
                code=PluginErrorCode.PLUGIN_UNHEALTHY,
                message="Plugin not initialized",
                plugin_name=self._name,
            )

        return await self._plugin.call_tool(tool_name, arguments)

    async def health_check(self) -> bool:
        """Check if the plugin is healthy.

        Returns:
            True if the plugin is healthy, False otherwise.
        """
        if self._plugin is None:
            return False

        try:
            return await self._plugin.health_check()
        except Exception as e:
            logger.warning(
                "plugin_health_check_failed",
                plugin=self._name,
                error=str(e),
            )
            return False

    async def shutdown(self) -> None:
        """Shut down the plugin and release resources."""
        if self._plugin is not None:
            try:
                await self._plugin.shutdown()
                logger.info("plugin_shutdown", plugin=self._name)
            except Exception as e:
                logger.error(
                    "plugin_shutdown_error",
                    plugin=self._name,
                    error=str(e),
                )
            finally:
                self._plugin = None

    async def reload(self, new_config: dict[str, Any] | None = None) -> None:
        """Reload the plugin with new configuration.

        For in-source plugins, this calls the plugin's on_config_reload
        method if available, or performs a full shutdown/initialize cycle.

        Args:
            new_config: New configuration to apply (optional).
        """
        if self._plugin is None:
            raise PluginError(
                code=PluginErrorCode.PLUGIN_UNHEALTHY,
                message="Plugin not initialized",
                plugin_name=self._name,
            )

        config = new_config if new_config is not None else self._config

        try:
            # Try to use on_config_reload for graceful reload
            await self._plugin.on_config_reload(config)
            self._config = config
            logger.info("plugin_reloaded", plugin=self._name)
        except Exception as e:
            logger.error(
                "plugin_reload_error",
                plugin=self._name,
                error=str(e),
            )
            raise PluginError(
                code=PluginErrorCode.INIT_FAILED,
                message=f"Failed to reload plugin: {e}",
                plugin_name=self._name,
                cause=e,
            ) from e

    async def reload_module(self) -> None:
        """Reload the Python module (hot reload).

        This is useful during development to pick up code changes
        without restarting the server.

        Note: Module reloading has limitations and may not work
        correctly in all cases. Use with caution.
        """
        if self._module is None:
            raise PluginError(
                code=PluginErrorCode.PLUGIN_UNHEALTHY,
                message="Plugin not loaded",
                plugin_name=self._name,
            )

        # Shutdown existing plugin
        if self._plugin is not None:
            await self._plugin.shutdown()

        # Reload the module
        try:
            self._module = importlib.reload(self._module)

            # Re-instantiate the plugin
            plugin_class = getattr(self._module, self._plugin_class_name)
            self._plugin = plugin_class(self._config)
            await self._plugin.initialize()

            logger.info(
                "plugin_module_reloaded",
                plugin=self._name,
                module=self._module_path,
            )
        except Exception as e:
            raise PluginError(
                code=PluginErrorCode.LOAD_FAILED,
                message=f"Failed to reload module: {e}",
                plugin_name=self._name,
                cause=e,
            ) from e
