"""Plugin lifecycle management.

This module provides the core plugin management functionality:
    - PluginLifecycle: Manages the state and lifecycle of a single plugin
    - HealthMonitor: Periodic health checking for all plugins
    - PluginManager: Coordinates all plugins, registry, and config watching

Classes:
    - PluginLifecycle: Individual plugin state and lifecycle management
    - HealthMonitor: Background health check scheduler
    - PluginManager: Main orchestrator for the plugin system
"""

import asyncio
import contextlib
from typing import Any

import structlog

from opencuff.plugins.adapters.in_source import InSourceAdapter
from opencuff.plugins.barrier import RequestBarrier
from opencuff.plugins.base import (
    PluginProtocol,
    PluginState,
    ToolDefinition,
    ToolResult,
)
from opencuff.plugins.config import (
    OpenCuffSettings,
    PluginConfig,
    PluginType,
    load_settings,
)
from opencuff.plugins.errors import PluginError, PluginErrorCode
from opencuff.plugins.registry import ToolRegistry
from opencuff.plugins.watcher import ConfigWatcher

logger = structlog.get_logger()


class PluginLifecycle:
    """Manages the lifecycle of a single plugin.

    Tracks the plugin's state and provides methods for state transitions:
    load, unload, reload, and health checks.

    Attributes:
        name: The plugin name.
        config: The plugin configuration.
        state: Current lifecycle state.
        adapter: The protocol adapter for this plugin.
        barrier: Request barrier for safe reloading.

    Example:
        lifecycle = PluginLifecycle("my_plugin", config, registry)
        await lifecycle.load()
        result = await lifecycle.call_tool("echo", {"message": "hi"})
        await lifecycle.unload()
    """

    def __init__(
        self,
        name: str,
        config: PluginConfig,
        registry: ToolRegistry,
    ) -> None:
        """Initialize the plugin lifecycle.

        Args:
            name: The plugin name.
            config: The plugin configuration.
            registry: The tool registry for registering tools.
        """
        self.name = name
        self.config = config
        self._registry = registry
        self._state = PluginState.UNLOADED
        self._adapter: PluginProtocol | None = None
        self._barrier = RequestBarrier()
        self._restart_count = 0

    @property
    def state(self) -> PluginState:
        """Return the current plugin state."""
        return self._state

    async def load(self) -> None:
        """Load and initialize the plugin.

        Transitions: UNLOADED -> INITIALIZING -> ACTIVE (or ERROR)

        Raises:
            PluginError: If loading or initialization fails.
        """
        if self._state != PluginState.UNLOADED:
            raise PluginError(
                code=PluginErrorCode.LOAD_FAILED,
                message=f"Cannot load plugin in state {self._state.value}",
                plugin_name=self.name,
            )

        self._state = PluginState.INITIALIZING

        try:
            # Create the appropriate adapter
            self._adapter = await self._create_adapter()

            # Initialize the adapter
            await self._adapter.initialize(self.config.config)

            # Get tools and register them
            tools = await self._adapter.get_tools()
            await self._registry.register_tools(self.name, tools)

            self._state = PluginState.ACTIVE
            logger.info(
                "plugin_loaded",
                plugin=self.name,
                tools=len(tools),
            )

        except Exception as e:
            self._state = PluginState.ERROR
            logger.error(
                "plugin_load_failed",
                plugin=self.name,
                error=str(e),
            )
            raise

    async def unload(self) -> None:
        """Unload the plugin and clean up resources.

        Transitions: * -> UNLOADED
        """
        try:
            # Unregister tools
            await self._registry.unregister_plugin(self.name)

            # Shutdown adapter
            if self._adapter:
                await self._adapter.shutdown()
                self._adapter = None

            self._state = PluginState.UNLOADED
            logger.info("plugin_unloaded", plugin=self.name)

        except Exception as e:
            logger.error(
                "plugin_unload_error",
                plugin=self.name,
                error=str(e),
            )
            # Still set to unloaded even on error
            self._state = PluginState.UNLOADED

    async def reload(self, new_config: PluginConfig | None = None) -> None:
        """Reload the plugin with optional new configuration.

        Uses the request barrier to ensure in-flight requests complete
        with the old plugin before the reload.

        Args:
            new_config: New configuration to apply (optional).
        """
        async with self._barrier.reload_scope():
            # Unregister old tools
            await self._registry.unregister_plugin(self.name)

            # Apply new config if provided
            if new_config:
                self.config = new_config

            try:
                if self._adapter:
                    # Get tools from reloaded adapter
                    if hasattr(self._adapter, "reload"):
                        await self._adapter.reload(self.config.config)
                    else:
                        await self._adapter.shutdown()
                        self._adapter = await self._create_adapter()
                        await self._adapter.initialize(self.config.config)

                    tools = await self._adapter.get_tools()
                    await self._registry.register_tools(self.name, tools)

                self._state = PluginState.ACTIVE
                logger.info("plugin_reloaded", plugin=self.name)

            except Exception as e:
                self._state = PluginState.ERROR
                logger.error(
                    "plugin_reload_failed",
                    plugin=self.name,
                    error=str(e),
                )
                raise

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Call a tool on this plugin with barrier protection.

        Args:
            tool_name: The tool name (without namespace prefix).
            arguments: Arguments for the tool.

        Returns:
            ToolResult from the tool invocation.

        Raises:
            PluginError: If the plugin is not active or call fails.
        """
        async with self._barrier.request_scope():
            if self._adapter is None or self._state != PluginState.ACTIVE:
                raise PluginError(
                    code=PluginErrorCode.PLUGIN_UNHEALTHY,
                    message="Plugin not active",
                    plugin_name=self.name,
                )

            return await self._adapter.call_tool(tool_name, arguments)

    async def health_check(self) -> bool:
        """Check if the plugin is healthy.

        Returns:
            True if healthy, False otherwise.
        """
        if self._adapter is None or self._state != PluginState.ACTIVE:
            return False

        try:
            return await self._adapter.health_check()
        except Exception as e:
            logger.warning(
                "plugin_health_check_failed",
                plugin=self.name,
                error=str(e),
            )
            return False

    async def recover(self) -> bool:
        """Attempt to recover a failed plugin.

        Returns:
            True if recovery succeeded, False otherwise.
        """
        if self._state != PluginState.ERROR:
            return True

        self._state = PluginState.RECOVERING
        self._restart_count += 1

        # Check max restarts
        max_restarts = 3
        if self.config.process_settings:
            max_restarts = self.config.process_settings.max_restarts

        if self._restart_count > max_restarts:
            logger.error(
                "plugin_max_restarts_exceeded",
                plugin=self.name,
                count=self._restart_count,
            )
            self._state = PluginState.UNLOADED
            return False

        try:
            # Full reload
            await self.reload()
            self._restart_count = 0
            return True

        except Exception as e:
            logger.error(
                "plugin_recovery_failed",
                plugin=self.name,
                error=str(e),
            )
            self._state = PluginState.ERROR
            return False

    def _create_in_source_adapter(self) -> PluginProtocol:
        """Create an in-source adapter for this plugin.

        Returns:
            An InSourceAdapter instance.

        Raises:
            PluginError: If the module field is not set.
        """
        if not self.config.module:
            raise PluginError(
                code=PluginErrorCode.CONFIG_INVALID,
                message="In-source plugin requires 'module' field",
                plugin_name=self.name,
            )
        return InSourceAdapter(
            name=self.name,
            module_path=self.config.module,
            config=self.config.config,
        )

    def _create_process_adapter(self) -> PluginProtocol:
        """Create a process adapter for this plugin.

        Raises:
            PluginError: Process plugins are not yet implemented.
        """
        raise PluginError(
            code=PluginErrorCode.CONFIG_INVALID,
            message="Process plugins not yet implemented",
            plugin_name=self.name,
        )

    def _create_http_adapter(self) -> PluginProtocol:
        """Create an HTTP adapter for this plugin.

        Raises:
            PluginError: HTTP plugins are not yet implemented.
        """
        raise PluginError(
            code=PluginErrorCode.CONFIG_INVALID,
            message="HTTP plugins not yet implemented",
            plugin_name=self.name,
        )

    async def _create_adapter(self) -> PluginProtocol:
        """Create the appropriate adapter for this plugin type.

        Uses a factory pattern with dictionary dispatch to select the
        appropriate adapter constructor based on plugin type.

        Returns:
            A PluginProtocol adapter instance.

        Raises:
            PluginError: If the plugin type is not supported.
        """
        # Factory dispatch table mapping plugin types to their constructors
        adapter_factories = {
            PluginType.IN_SOURCE: self._create_in_source_adapter,
            PluginType.PROCESS: self._create_process_adapter,
            PluginType.HTTP: self._create_http_adapter,
        }

        factory = adapter_factories.get(self.config.type)
        if factory is None:
            raise PluginError(
                code=PluginErrorCode.CONFIG_INVALID,
                message=f"Unknown plugin type: {self.config.type}",
                plugin_name=self.name,
            )

        return factory()


class HealthMonitor:
    """Periodic health monitoring for all plugins.

    Runs health checks at configured intervals and triggers recovery
    for unhealthy plugins.

    Attributes:
        plugin_manager: The plugin manager to monitor.
        interval: Health check interval in seconds (0 to disable).
    """

    def __init__(
        self,
        plugin_manager: "PluginManager",
        interval: float = 30.0,
    ) -> None:
        """Initialize the health monitor.

        Args:
            plugin_manager: The plugin manager to monitor.
            interval: Health check interval in seconds (0 to disable).
        """
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
        logger.info("health_monitor_started", interval=self.interval)

    async def stop(self) -> None:
        """Stop the health monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("health_monitor_stopped")

    async def _monitor_loop(self) -> None:
        """Main health check loop."""
        while self._running:
            await asyncio.sleep(self.interval)

            if not self._running:
                break

            for name, lifecycle in self.plugin_manager.plugins.items():
                if lifecycle.state != PluginState.ACTIVE:
                    continue

                try:
                    healthy = await lifecycle.health_check()
                    if not healthy:
                        logger.warning(
                            "plugin_health_check_failed",
                            plugin=name,
                        )
                        # Trigger recovery
                        await lifecycle.recover()

                except Exception as e:
                    logger.error(
                        "plugin_health_check_error",
                        plugin=name,
                        error=str(e),
                    )


class PluginManager:
    """Manages all plugins and their lifecycles.

    The central coordinator for the plugin system. Handles:
        - Loading initial configuration
        - Starting/stopping all plugins
        - Configuration file watching
        - Health monitoring
        - Tool routing

    Design Note:
        The constructor accepts either a settings_path or a pre-loaded
        OpenCuffSettings object. This deviates from the HLD's singleton
        pattern intentionally to improve testability. Tests can inject
        settings directly without requiring filesystem access.

    Attributes:
        settings_path: Path to the settings.yml file.
        plugins: Dictionary of plugin names to their lifecycles.
        tool_registry: The tool registry.

    Example:
        manager = PluginManager("./settings.yml")
        await manager.start()

        # Call a tool
        result = await manager.call_tool("dummy.echo", {"message": "hi"})

        await manager.stop()
    """

    def __init__(
        self,
        settings_path: str | None = None,
        settings: OpenCuffSettings | None = None,
    ) -> None:
        """Initialize the plugin manager.

        Args:
            settings_path: Path to the settings.yml file (optional).
            settings: Pre-loaded settings (optional, for testing).

        Note:
            Either settings_path or settings must be provided.
            Providing settings directly is the preferred approach for testing.
        """
        self._settings_path = settings_path
        self._settings = settings
        self.plugins: dict[str, PluginLifecycle] = {}
        self.tool_registry = ToolRegistry()
        self._config_watcher: ConfigWatcher | None = None
        self._health_monitor: HealthMonitor | None = None
        self._started = False

    async def start(self) -> None:
        """Start the plugin manager.

        1. Load initial configuration
        2. Start config file watcher (if path provided)
        3. Load all enabled plugins
        4. Start health monitor
        """
        if self._started:
            logger.warning("plugin_manager_already_started")
            return

        # Load configuration
        if self._settings is None and self._settings_path:
            self._settings = load_settings(self._settings_path)
        elif self._settings is None:
            self._settings = OpenCuffSettings()

        # Start config watcher
        if self._settings_path and self._settings.plugin_settings.live_reload:
            self._config_watcher = ConfigWatcher(
                settings_path=self._settings_path,
                on_change=self._on_config_change,
                poll_interval=self._settings.plugin_settings.config_poll_interval,
            )
            await self._config_watcher.start()

        # Load all enabled plugins
        for name, config in self._settings.plugins.items():
            if config.enabled:
                lifecycle = PluginLifecycle(name, config, self.tool_registry)
                self.plugins[name] = lifecycle
                try:
                    await lifecycle.load()
                except Exception as e:
                    logger.error(
                        "plugin_load_failed_on_start",
                        plugin=name,
                        error=str(e),
                    )

        # Start health monitor
        self._health_monitor = HealthMonitor(
            self,
            interval=self._settings.plugin_settings.health_check_interval,
        )
        await self._health_monitor.start()

        self._started = True
        logger.info(
            "plugin_manager_started",
            plugins=len(self.plugins),
            tools=len(self.tool_registry),
        )

    async def stop(self) -> None:
        """Stop the plugin manager.

        1. Stop health monitor
        2. Stop config watcher
        3. Unload all plugins
        """
        if not self._started:
            return

        # Stop health monitor
        if self._health_monitor:
            await self._health_monitor.stop()
            self._health_monitor = None

        # Stop config watcher
        if self._config_watcher:
            await self._config_watcher.stop()
            self._config_watcher = None

        # Unload all plugins
        for name, lifecycle in list(self.plugins.items()):
            try:
                await lifecycle.unload()
            except Exception as e:
                logger.error(
                    "plugin_unload_error_on_stop",
                    plugin=name,
                    error=str(e),
                )

        self.plugins.clear()
        self._started = False
        logger.info("plugin_manager_stopped")

    async def call_tool(
        self,
        tool_fqn: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Call a tool by its fully qualified name.

        Args:
            tool_fqn: Fully qualified tool name (plugin_name.tool_name).
            arguments: Arguments for the tool.

        Returns:
            ToolResult from the tool invocation.

        Raises:
            PluginError: If the tool is not found or call fails.
        """
        # Look up the tool
        result = self.tool_registry.get_tool(tool_fqn)
        if result is None:
            raise PluginError(
                code=PluginErrorCode.TOOL_NOT_FOUND,
                message=f"Tool not found: {tool_fqn}",
            )

        plugin_name, tool = result

        # Get the plugin lifecycle
        lifecycle = self.plugins.get(plugin_name)
        if lifecycle is None:
            raise PluginError(
                code=PluginErrorCode.PLUGIN_UNHEALTHY,
                message=f"Plugin not loaded: {plugin_name}",
            )

        # Call the tool (using the tool's base name, not FQN)
        return await lifecycle.call_tool(tool.name, arguments)

    def get_all_tools(self) -> list[tuple[str, ToolDefinition]]:
        """Get all registered tools with their FQNs.

        Returns:
            List of (fqn, ToolDefinition) tuples.
        """
        return self.tool_registry.list_tools()

    async def _unload_removed_plugins(self, to_unload: set[str]) -> None:
        """Unload plugins that were removed from configuration.

        Args:
            to_unload: Set of plugin names to unload.
        """
        for name in to_unload:
            logger.info("plugin_config_removed", plugin=name)
            lifecycle = self.plugins.pop(name, None)
            if lifecycle:
                await lifecycle.unload()

    async def _reload_changed_plugins(
        self,
        to_reload: set[str],
        new_settings: OpenCuffSettings,
    ) -> None:
        """Reload plugins whose configuration has changed.

        Args:
            to_reload: Set of plugin names to potentially reload.
            new_settings: The new settings containing updated configurations.
        """
        for name in to_reload:
            old_config = self.plugins[name].config
            new_config = new_settings.plugins[name]

            # Check if config actually changed
            if old_config != new_config:
                logger.info("plugin_config_changed", plugin=name)
                await self.plugins[name].reload(new_config)

    async def _load_new_plugins(
        self,
        to_load: set[str],
        new_settings: OpenCuffSettings,
    ) -> None:
        """Load plugins that were newly added to configuration.

        Args:
            to_load: Set of plugin names to load.
            new_settings: The new settings containing plugin configurations.
        """
        for name in to_load:
            logger.info("plugin_config_added", plugin=name)
            config = new_settings.plugins[name]
            lifecycle = PluginLifecycle(name, config, self.tool_registry)
            self.plugins[name] = lifecycle
            try:
                await lifecycle.load()
            except Exception as e:
                logger.error(
                    "plugin_load_failed_on_config_change",
                    plugin=name,
                    error=str(e),
                )

    async def _update_health_monitor_if_needed(
        self,
        new_settings: OpenCuffSettings,
    ) -> None:
        """Update health monitor if the check interval has changed.

        Args:
            new_settings: The new settings to check against.
        """
        if self._health_monitor:
            new_interval = new_settings.plugin_settings.health_check_interval
            if new_interval != self._health_monitor.interval:
                await self._health_monitor.stop()
                self._health_monitor = HealthMonitor(self, interval=new_interval)
                await self._health_monitor.start()

    async def _on_config_change(self, new_settings: OpenCuffSettings) -> None:
        """Handle configuration file changes.

        Compares old and new configuration to determine:
            - Plugins to unload (removed from config)
            - Plugins to reload (config changed)
            - Plugins to load (newly added)

        Args:
            new_settings: The new settings from the config file.
        """
        old_plugins = set(self.plugins.keys())
        new_plugins = {
            name for name, cfg in new_settings.plugins.items() if cfg.enabled
        }

        # Process plugin changes
        await self._unload_removed_plugins(old_plugins - new_plugins)
        await self._reload_changed_plugins(old_plugins & new_plugins, new_settings)
        await self._load_new_plugins(new_plugins - old_plugins, new_settings)

        # Update settings reference
        self._settings = new_settings

        # Update health monitor if needed
        await self._update_health_monitor_if_needed(new_settings)

    async def load_plugin(self, name: str, config: PluginConfig) -> None:
        """Manually load a plugin.

        Args:
            name: The plugin name.
            config: The plugin configuration.

        Raises:
            PluginError: If loading fails.
        """
        if name in self.plugins:
            raise PluginError(
                code=PluginErrorCode.CONFIG_INVALID,
                message=f"Plugin already loaded: {name}",
                plugin_name=name,
            )

        lifecycle = PluginLifecycle(name, config, self.tool_registry)
        self.plugins[name] = lifecycle
        await lifecycle.load()

    async def unload_plugin(self, name: str) -> None:
        """Manually unload a plugin.

        Args:
            name: The plugin name.
        """
        lifecycle = self.plugins.pop(name, None)
        if lifecycle:
            await lifecycle.unload()

    async def reload_plugin(
        self,
        name: str,
        new_config: PluginConfig | None = None,
    ) -> None:
        """Manually reload a plugin.

        Args:
            name: The plugin name.
            new_config: New configuration (optional).

        Raises:
            PluginError: If the plugin is not loaded.
        """
        lifecycle = self.plugins.get(name)
        if lifecycle is None:
            raise PluginError(
                code=PluginErrorCode.PLUGIN_UNHEALTHY,
                message=f"Plugin not loaded: {name}",
                plugin_name=name,
            )

        await lifecycle.reload(new_config)
