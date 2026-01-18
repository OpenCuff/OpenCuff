"""Registry of plugins that support discovery.

This module provides functions to access and register plugins that can be
automatically discovered based on project files. It is used by the CLI's
`cuff init` command to generate configuration.

Note: Plugin registration should only occur during module import time.
The registry is not thread-safe for concurrent modifications.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opencuff.plugins.base import InSourcePlugin

# Lazy imports to avoid circular dependencies
_registry: dict[str, type["InSourcePlugin"]] | None = None
_module_paths: dict[str, str] = {}


def get_discoverable_plugins() -> dict[str, type["InSourcePlugin"]]:
    """Return all plugins that support discovery.

    This function initializes the registry on first call with built-in plugins,
    and returns the current registry on subsequent calls.

    Returns:
        Mapping of plugin names to plugin classes.
    """
    global _registry
    if _registry is None:
        from opencuff.plugins.builtin.makefile import Plugin as MakefilePlugin

        _registry = {
            "makefile": MakefilePlugin,
        }
        _module_paths.update(
            {
                "makefile": "opencuff.plugins.builtin.makefile",
            }
        )

        # Try to import packagejson plugin if available
        try:
            from opencuff.plugins.builtin.packagejson import (
                Plugin as PackageJsonPlugin,
            )

            _registry["packagejson"] = PackageJsonPlugin
            _module_paths["packagejson"] = "opencuff.plugins.builtin.packagejson"
        except ImportError:
            # packagejson plugin not available, skip
            pass

        # Try to import scripts plugin if available
        try:
            from opencuff.plugins.builtin.scripts import (
                Plugin as ScriptsPlugin,
            )

            _registry["scripts"] = ScriptsPlugin
            _module_paths["scripts"] = "opencuff.plugins.builtin.scripts"
        except ImportError:
            # scripts plugin not available, skip
            pass

    return _registry


def get_module_paths() -> dict[str, str]:
    """Return mapping of plugin names to their module paths.

    This ensures the registry is initialized and returns a copy of the
    module paths dictionary.

    Returns:
        Dictionary mapping plugin names to their full module paths.
    """
    get_discoverable_plugins()  # Ensure initialized
    return _module_paths.copy()


def register_plugin(
    name: str,
    plugin_cls: type["InSourcePlugin"],
    module_path: str,
) -> None:
    """Register a plugin for discovery.

    This function adds a plugin to the discovery registry, making it available
    for automatic detection during `cuff init`.

    Note: This function should only be called during module import time.
    It is not thread-safe for concurrent modifications.

    Args:
        name: Plugin name (used in settings.yml).
        plugin_cls: The plugin class.
        module_path: Full module path for the plugin (e.g., "mypackage.plugins.custom").
    """
    global _registry
    if _registry is None:
        get_discoverable_plugins()  # Initialize
    _registry[name] = plugin_cls
    _module_paths[name] = module_path
