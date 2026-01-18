"""OpenCuff Plugin System.

This package provides the plugin architecture for OpenCuff, enabling extensibility
through three plugin types: in-source, process, and HTTP plugins.

Core Components:
    - base: Plugin interfaces and base classes (PluginProtocol, InSourcePlugin)
    - config: Configuration models (PluginConfig, PluginSettings, OpenCuffSettings)
    - registry: Tool registry with namespace support (ToolRegistry)
    - barrier: Request barrier for live reload (RequestBarrier)
    - manager: Plugin lifecycle management (PluginManager)
    - watcher: Configuration file watcher (ConfigWatcher)
"""

from opencuff.plugins.base import (
    InSourcePlugin,
    PluginProtocol,
    PluginState,
    ToolDefinition,
    ToolResult,
)

# Lazy imports to avoid circular dependencies during development
# These will be populated as modules are implemented
__all__ = [
    "InSourcePlugin",
    "PluginProtocol",
    "PluginState",
    "ToolDefinition",
    "ToolResult",
]


def __getattr__(name: str):
    """Lazy import for config and other modules."""
    if name in ("OpenCuffSettings", "PluginConfig", "PluginSettings", "PluginType"):
        from opencuff.plugins import config

        return getattr(config, name)
    if name == "ToolRegistry":
        from opencuff.plugins import registry

        return registry.ToolRegistry
    if name == "RequestBarrier":
        from opencuff.plugins import barrier

        return barrier.RequestBarrier
    if name == "PluginManager":
        from opencuff.plugins import manager

        return manager.PluginManager
    if name == "ConfigWatcher":
        from opencuff.plugins import watcher

        return watcher.ConfigWatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
