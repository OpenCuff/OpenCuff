"""Discovery coordinator for plugin auto-detection.

This module provides the DiscoveryCoordinator class that coordinates plugin discovery
across all registered plugins and generates settings.yml configuration.

Classes:
    DiscoveryCoordinator: Coordinates plugin discovery and settings generation.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from opencuff.plugins.base import DiscoveryResult

if TYPE_CHECKING:
    from opencuff.plugins.base import InSourcePlugin


class DiscoveryCoordinator:
    """Coordinates plugin discovery across all registered plugins.

    This class is responsible for:
        - Running discovery for all registered plugins
        - Filtering discovery results based on include/exclude lists
        - Generating settings.yml content from discovery results

    Example:
        >>> from opencuff.plugins.discovery_registry import (
        ...     get_discoverable_plugins,
        ...     get_module_paths,
        ... )
        >>> coordinator = DiscoveryCoordinator(
        ...     plugins=get_discoverable_plugins(),
        ...     module_paths=get_module_paths(),
        ... )
        >>> results = coordinator.discover_all(Path("."))
        >>> settings = coordinator.generate_settings(Path("."))
    """

    def __init__(
        self,
        plugins: dict[str, type["InSourcePlugin"]],
        module_paths: dict[str, str],
    ) -> None:
        """Initialize the discovery coordinator.

        Args:
            plugins: Mapping of plugin names to plugin classes.
            module_paths: Mapping of plugin names to their module paths.
        """
        self._plugins = plugins
        self._module_paths = module_paths

    def discover_all(self, directory: Path) -> dict[str, DiscoveryResult]:
        """Run discovery for all registered plugins.

        Scans the given directory using each plugin's discover() method
        and returns all results (both applicable and non-applicable).

        Args:
            directory: The directory to scan for plugin applicability.

        Returns:
            Mapping of plugin names to their DiscoveryResult objects.

        Raises:
            ValueError: If directory does not exist or is not a directory.
        """
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {directory}")

        if not directory.is_dir():
            raise ValueError(f"Path is not a directory: {directory}")

        results: dict[str, DiscoveryResult] = {}

        for name, plugin_cls in self._plugins.items():
            try:
                result = plugin_cls.discover(directory)
                results[name] = result
            except Exception:
                # If discovery fails, treat as not applicable
                results[name] = DiscoveryResult(
                    applicable=False,
                    confidence=0.0,
                    suggested_config={},
                    description=f"Discovery failed for {name}",
                )

        return results

    def generate_settings(
        self,
        directory: Path,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate settings.yml content from discovery results.

        Discovers applicable plugins and generates a complete settings
        dictionary that can be serialized to YAML.

        Args:
            directory: The directory to scan.
            include: Optional list of plugin names to include (others excluded).
            exclude: Optional list of plugin names to exclude.

        Returns:
            Dictionary representing the settings.yml content.
        """
        results = self.discover_all(directory)

        # Filter by include/exclude
        filtered_plugins: dict[str, DiscoveryResult] = {}
        for name, result in results.items():
            # Skip non-applicable plugins
            if not result.applicable:
                continue

            # Apply include filter
            if include is not None and name not in include:
                continue

            # Apply exclude filter
            if exclude is not None and name in exclude:
                continue

            filtered_plugins[name] = result

        # Build settings structure
        plugins_config: dict[str, dict[str, Any]] = {}
        for name, result in filtered_plugins.items():
            default_module = f"opencuff.plugins.builtin.{name}"
            module_path = self._module_paths.get(name, default_module)
            plugins_config[name] = {
                "enabled": True,
                "type": "in_source",
                "module": module_path,
                "config": result.suggested_config,
            }

        return {
            "version": "1",
            "plugin_settings": {
                "health_check_interval": 30,
                "live_reload": True,
                "default_timeout": 30,
            },
            "plugins": plugins_config,
        }
