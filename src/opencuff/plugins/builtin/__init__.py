"""Built-in plugins for OpenCuff.

This package contains plugins that are distributed with OpenCuff:
    - dummy: A test plugin with simple tools for testing the plugin system
    - makefile: Discovers and exposes Makefile targets as MCP tools
"""

from opencuff.plugins.builtin.makefile import (
    ExtractorStrategy,
    MakefilePluginConfig,
    MakeTarget,
    Plugin,
)

# Alias for explicit naming when needed
MakefilePlugin = Plugin

__all__ = [
    "ExtractorStrategy",
    "MakefilePlugin",
    "MakefilePluginConfig",
    "MakeTarget",
    "Plugin",
]
