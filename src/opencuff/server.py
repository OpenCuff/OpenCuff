"""OpenCuff MCP Server with Plugin Support.

This module provides the main MCP server for OpenCuff, integrating FastMCP
with the plugin system for extensibility.

The server:
    - Initializes the PluginManager on startup
    - Registers plugin tools with FastMCP
    - Routes tool calls through the plugin system
    - Supports live reload of plugin configuration

Usage:
    # Start the server directly
    python -m opencuff.server

    # Or import and use programmatically
    from opencuff import mcp
    async with mcp:
        # server is running
        pass
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastmcp import FastMCP

from opencuff.plugins.config import OpenCuffSettings
from opencuff.plugins.fastmcp_bridge import FastMCPBridge
from opencuff.plugins.manager import PluginManager

logger = structlog.get_logger()

# Global plugin manager instance
# Note: This global is retained for backward compatibility with existing code
# that uses get_plugin_manager(). Use _reset_for_testing() in tests to reset state.
_plugin_manager: PluginManager | None = None

# Global FastMCP bridge instance for dynamic tool registration
_fastmcp_bridge: FastMCPBridge | None = None


def find_settings_path() -> Path | None:
    """Find the settings file in standard locations.

    Searches for settings.yml in:
        1. OPENCUFF_SETTINGS environment variable (if set)
        2. Current working directory
        3. ~/.opencuff/settings.yml
        4. /etc/opencuff/settings.yml

    Returns:
        Path to the settings file if found, None otherwise.
    """
    import os

    # Check environment variable first
    env_path = os.environ.get("OPENCUFF_SETTINGS")
    if env_path:
        path = Path(env_path)
        if path.exists():
            logger.info("settings_found_via_env", path=str(path))
            return path
        logger.warning(
            "settings_env_path_not_found",
            path=env_path,
            message="OPENCUFF_SETTINGS path does not exist, falling back to search",
        )

    search_paths = [
        Path.cwd() / "settings.yml",
        Path.home() / ".opencuff" / "settings.yml",
        Path("/etc/opencuff/settings.yml"),
    ]

    for path in search_paths:
        if path.exists():
            return path

    return None


async def initialize_plugins(
    settings_path: str | Path | None = None,
    settings: OpenCuffSettings | None = None,
) -> PluginManager:
    """Initialize the plugin manager and load plugins.

    Also sets up the FastMCPBridge to dynamically register plugin tools
    with FastMCP, making them visible as first-class MCP tools.

    Args:
        settings_path: Path to settings.yml file (optional).
        settings: Pre-loaded settings (optional, for testing).

    Returns:
        The initialized PluginManager instance.
    """
    global _plugin_manager, _fastmcp_bridge

    if _plugin_manager is not None:
        logger.warning("plugins_already_initialized")
        return _plugin_manager

    # Find settings file if not provided
    if settings_path is None and settings is None:
        settings_path = find_settings_path()

    # Create the plugin manager (but don't start yet)
    _plugin_manager = PluginManager(
        settings_path=str(settings_path) if settings_path else None,
        settings=settings,
    )

    # Create the FastMCP bridge and wire up callbacks
    # This must happen BEFORE plugin manager starts so tools are registered
    # as they load
    _fastmcp_bridge = FastMCPBridge(
        mcp=mcp,
        tool_registry=_plugin_manager.tool_registry,
        call_handler=_plugin_manager.call_tool,
    )

    # Set callbacks on the registry to sync with FastMCP
    _plugin_manager.tool_registry.set_callbacks(
        on_registered=_fastmcp_bridge.sync_tools,
        on_unregistered=_fastmcp_bridge.remove_plugin_tools,
    )

    # Now start the plugin manager (this will load plugins and trigger callbacks)
    await _plugin_manager.start()

    logger.info(
        "plugins_initialized",
        plugin_count=len(_plugin_manager.plugins),
        tool_count=len(_plugin_manager.tool_registry),
    )

    return _plugin_manager


async def shutdown_plugins() -> None:
    """Shutdown the plugin manager and unload plugins."""
    global _plugin_manager

    if _plugin_manager is not None:
        await _plugin_manager.stop()
        _plugin_manager = None
        logger.info("plugins_shutdown")


@asynccontextmanager
async def _server_lifespan(server: FastMCP):
    """Lifespan context manager for the MCP server.

    Initializes plugins on startup and shuts them down on shutdown.
    """
    await initialize_plugins()
    yield
    await shutdown_plugins()


# Create the FastMCP server instance with lifespan
mcp = FastMCP("OpenCuff", lifespan=_server_lifespan)


def get_plugin_manager() -> PluginManager | None:
    """Get the global plugin manager instance.

    Returns:
        The PluginManager instance if initialized, None otherwise.
    """
    return _plugin_manager


async def _reset_for_testing() -> None:
    """Reset global state for testing purposes.

    This function is intended for use in test fixtures to ensure a clean
    state between tests. It shuts down any running plugin manager and
    clears global references including the FastMCP bridge.

    Warning:
        This function is for testing only. Do not use in production code.
    """
    global _plugin_manager, _fastmcp_bridge

    if _plugin_manager is not None:
        await _plugin_manager.stop()
        _plugin_manager = None

    _fastmcp_bridge = None


# Built-in tool that's always available
@mcp.tool()
def hello() -> str:
    """Return a greeting to verify the server is running.

    This is a simple built-in tool that can be used to check that the
    OpenCuff MCP server is operational and responding to tool calls.

    Returns:
        A greeting string from OpenCuff.
    """
    return "Hello from OpenCuff!"


@mcp.tool()
def list_plugins() -> dict[str, Any]:
    """List all loaded plugins and their status.

    Returns:
        Dictionary with plugin information including:
            - plugins: Dict mapping plugin names to their status
            - total_tools: Total number of registered tools
    """
    if _plugin_manager is None:
        return {"plugins": {}, "total_tools": 0}

    plugins_info = {}
    for name, lifecycle in _plugin_manager.plugins.items():
        plugins_info[name] = {
            "state": lifecycle.state.value,
            "tools": [
                fqn
                for fqn, _ in _plugin_manager.tool_registry.get_tools_for_plugin(name)
            ],
        }

    return {
        "plugins": plugins_info,
        "total_tools": len(_plugin_manager.tool_registry),
    }


@mcp.tool()
async def call_plugin_tool(
    tool_name: str, arguments: dict[str, Any] | None = None
) -> Any:
    """Call a plugin tool by its fully qualified name.

    This tool allows calling any plugin tool using the format:
    plugin_name.tool_name

    Args:
        tool_name: Fully qualified tool name (e.g., "dummy.echo")
        arguments: Arguments to pass to the tool (optional)

    Returns:
        The result from the plugin tool

    Raises:
        RuntimeError: If plugin manager is not initialized or tool fails
    """
    if _plugin_manager is None:
        raise RuntimeError("Plugin manager not initialized")

    args = arguments or {}
    result = await _plugin_manager.call_tool(tool_name, args)

    if not result.success:
        raise RuntimeError(result.error or "Tool execution failed")

    return result.data


# For testing: helper to create a server with specific settings
async def create_test_server(settings: OpenCuffSettings) -> FastMCP:
    """Create a test server with the given settings.

    This function is intended for use in tests to create a fully
    configured server instance with specific plugin settings. It
    automatically resets any existing global state before initialization.

    Args:
        settings: The OpenCuffSettings instance to use for the plugin manager.

    Returns:
        The FastMCP server instance configured with the provided settings.

    Note:
        This function modifies global state. Use _reset_for_testing()
        in test teardown to ensure clean state between tests.
    """
    await _reset_for_testing()
    await initialize_plugins(settings=settings)
    return mcp
