"""Status command for OpenCuff CLI.

This module provides the `cuff status` command that displays the current status
of all configured plugins.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from opencuff.plugins.config import PluginConfig, PluginType, load_settings


def status_command(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to settings.yml",
        ),
    ] = Path("./settings.yml"),
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed information",
        ),
    ] = False,
) -> None:
    """Show status of all configured plugins.

    Loads the settings.yml file and displays information about each
    configured plugin including its state and available tools.
    """
    # Check if config file exists
    if not config.exists():
        typer.echo(f"Error: Settings file not found: {config}", err=True)
        typer.echo("\nRun 'cuff init' to create a configuration file.", err=True)
        raise typer.Exit(1)

    # Load settings
    try:
        settings = load_settings(config)
    except yaml.YAMLError as e:
        typer.echo(f"Error: Invalid YAML in {config}: {e}", err=True)
        raise typer.Exit(2) from e
    except Exception as e:
        typer.echo(f"Error: Failed to load settings: {e}", err=True)
        raise typer.Exit(2) from e

    # Build status data
    status_data = _build_status_data(settings, config, verbose)

    if json_output:
        typer.echo(json.dumps(status_data, indent=2))
        return

    # Display human-readable output
    _display_status(status_data, verbose)


def _build_status_data(
    settings: Any, config_path: Path, verbose: bool
) -> dict[str, Any]:
    """Build status data dictionary.

    Args:
        settings: The loaded OpenCuffSettings object.
        config_path: Path to the settings file.
        verbose: Whether to include detailed information.

    Returns:
        Dictionary containing status information.
    """
    enabled_count = 0
    disabled_count = 0
    plugins_status: list[dict] = []

    for name, plugin_config in settings.plugins.items():
        if plugin_config.enabled:
            enabled_count += 1
        else:
            disabled_count += 1

        plugin_info = {
            "name": name,
            "enabled": plugin_config.enabled,
            "type": plugin_config.type.value,
            "state": "active" if plugin_config.enabled else "disabled",
        }

        # Add module info for in_source plugins
        if plugin_config.type == PluginType.IN_SOURCE and plugin_config.module:
            plugin_info["module"] = plugin_config.module

        # Get tools using plugin discovery mechanism
        tools = _get_plugin_tools_via_discovery(name, plugin_config, config_path.parent)
        plugin_info["tools"] = tools
        plugin_info["tool_count"] = len(tools)

        if verbose:
            plugin_info["config"] = plugin_config.config

        plugins_status.append(plugin_info)

    return {
        "settings_path": str(config_path),
        "enabled_count": enabled_count,
        "disabled_count": disabled_count,
        "plugins": plugins_status,
    }


def _get_plugin_tools_via_discovery(
    name: str, plugin_config: PluginConfig, base_dir: Path
) -> list[str]:
    """Get the list of tools a plugin would expose using discovery mechanism.

    Uses the plugin's discover() method to get the discovered items,
    which represent the tools the plugin would expose.

    Args:
        name: Plugin name.
        plugin_config: Plugin configuration.
        base_dir: Base directory for resolving relative paths.

    Returns:
        List of tool names the plugin would expose.
    """
    if not plugin_config.enabled:
        return []

    try:
        # Lazy import to avoid circular dependencies
        from opencuff.plugins.discovery_registry import get_discoverable_plugins

        plugins = get_discoverable_plugins()
        plugin_cls = plugins.get(name)

        if plugin_cls is None:
            return []

        # Use the plugin's discover method
        result = plugin_cls.discover(base_dir)

        if not result.applicable:
            return []

        return result.discovered_items

    except Exception:
        return []


def _display_status(status_data: dict, verbose: bool) -> None:
    """Display status in human-readable format.

    Args:
        status_data: Status data dictionary.
        verbose: Whether to show detailed information.
    """
    typer.echo("OpenCuff Status")
    typer.echo("=" * 15)
    typer.echo()

    typer.echo(f"Settings: {status_data['settings_path']}")
    typer.echo(
        f"Plugins: {status_data['enabled_count']} enabled, "
        f"{status_data['disabled_count']} disabled"
    )
    typer.echo()

    for plugin in status_data["plugins"]:
        state_str = "active" if plugin["enabled"] else "disabled"
        typer.echo(f"{plugin['name']} ({state_str})")

        if plugin["enabled"]:
            typer.echo(f"  Type: {plugin['type']}")

            if "module" in plugin:
                typer.echo(f"  Module: {plugin['module']}")

            tools = plugin.get("tools", [])
            if tools:
                typer.echo(f"  Tools: {len(tools)}")
                for tool in tools:
                    typer.echo(f"    - {tool}")
            else:
                typer.echo("  Tools: none")

        if verbose and "config" in plugin:
            typer.echo("  Config:")
            for key, value in plugin["config"].items():
                typer.echo(f"    {key}: {value}")

        typer.echo()
