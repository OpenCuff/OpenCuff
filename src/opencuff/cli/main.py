"""OpenCuff CLI entry point.

This module provides the main Typer application and entry point for the `cuff` CLI.
It registers core commands (init, status, doctor, run, version) and dynamically
registers plugin commands with appropriate error handling.

Usage:
    cuff init [options]       - Initialize settings.yml
    cuff status [options]     - Show plugin status
    cuff doctor [options]     - Run diagnostics
    cuff run [options]        - Run the MCP server
    cuff version [options]    - Show version information
    cuff <plugin> <action>    - Plugin-specific commands
"""

import logging

import typer

from opencuff.cli.commands import doctor, init, run, status, version

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="cuff",
    help="OpenCuff CLI - Controlled operations for coding agents",
    no_args_is_help=True,
)

# Register core commands
app.command(name="init")(init.init_command)
app.command(name="status")(status.status_command)
app.command(name="doctor")(doctor.doctor_command)
app.command(name="run")(run.run_command)
app.command(name="version")(version.version_command)


def register_plugin_commands(app: typer.Typer) -> None:
    """Dynamically register plugin CLI commands.

    Discovers all plugins that provide CLI commands via get_cli_commands()
    and registers them as subcommands under the plugin name.

    Args:
        app: The main Typer application to register commands on.

    Note:
        This function logs warnings but does not raise exceptions to ensure
        core commands remain functional even if plugin registration fails.
    """
    try:
        # Lazy import to avoid circular dependencies and speed up CLI startup
        from opencuff.plugins.discovery_registry import get_discoverable_plugins

        plugins = get_discoverable_plugins()

        for name, plugin_cls in plugins.items():
            try:
                cli_commands = plugin_cls.get_cli_commands()
                if not cli_commands:
                    continue

                # Create a sub-app for this plugin
                plugin_app = typer.Typer(
                    name=name,
                    help=f"{name} plugin commands",
                )

                for cmd in cli_commands:
                    # Register each command on the plugin sub-app
                    plugin_app.command(name=cmd.name, help=cmd.help)(cmd.callback)

                # Add the plugin sub-app to the main app
                app.add_typer(plugin_app, name=name)

            except Exception as e:
                logger.warning(
                    "Failed to register CLI commands for plugin %s: %s",
                    name,
                    e,
                )

    except Exception as e:
        logger.warning("Failed to load plugin registry for CLI commands: %s", e)


# Dynamically register plugin commands with error handling
try:
    register_plugin_commands(app)
except Exception as e:
    # Log but don't crash - core commands should still work
    logger.warning("Failed to register some plugin commands: %s", e)


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
