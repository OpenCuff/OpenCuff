"""Init command for OpenCuff CLI.

This module provides the `cuff init` command that discovers applicable plugins
and generates a settings.yml configuration file.

Exit codes:
    0: Success
    1: No plugins discovered
    2: Output file exists (without --force)
    3: Write error
"""

from pathlib import Path
from typing import Annotated

import typer
import yaml

from opencuff.cli.discovery import DiscoveryCoordinator
from opencuff.plugins.discovery_registry import (
    get_discoverable_plugins,
    get_module_paths,
)


def init_command(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output path for settings.yml",
        ),
    ] = Path("./settings.yml"),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite existing settings.yml",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be generated without writing",
        ),
    ] = False,
    plugins: Annotated[
        str | None,
        typer.Option(
            "--plugins",
            help="Comma-separated list of plugins to include",
        ),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(
            "--exclude",
            help="Comma-separated list of plugins to exclude",
        ),
    ] = None,
) -> None:
    """Initialize a new settings.yml based on discovered plugins.

    Scans the current directory for applicable plugins (Makefile, package.json, etc.)
    and generates a configuration file.
    """
    # Check if output file exists
    if output.exists() and not force and not dry_run:
        msg = f"Error: {output} already exists. Use --force to overwrite."
        typer.echo(msg, err=True)
        raise typer.Exit(2)

    # Parse include/exclude filters
    include_list = _parse_comma_list(plugins)
    exclude_list = _parse_comma_list(exclude)

    # Get the directory to scan (parent of output path, or current dir)
    scan_dir = output.parent.resolve() if output.parent != Path(".") else Path.cwd()

    # Create discovery coordinator
    coordinator = DiscoveryCoordinator(
        plugins=get_discoverable_plugins(),
        module_paths=get_module_paths(),
    )

    # Discover plugins
    typer.echo("Discovering plugins...")
    results = coordinator.discover_all(scan_dir)

    # Show discovery results
    applicable_count = 0
    for name, result in results.items():
        if result.applicable:
            applicable_count += 1
            prefix = "[+]"
            items_info = ""
            if result.discovered_items:
                items_preview = ", ".join(result.discovered_items[:3])
                if len(result.discovered_items) > 3:
                    items_preview += ", ..."
                items_info = f" ({items_preview})"
            typer.echo(f"  {prefix} {name}: {result.description}{items_info}")
        else:
            prefix = "[-]"
            typer.echo(f"  {prefix} {name}: {result.description}")

        # Show warnings
        for warning in result.warnings:
            typer.echo(f"      Warning: {warning}")

    # Check if any plugins were discovered
    if applicable_count == 0:
        typer.echo("\nNo plugins discovered. Nothing to generate.", err=True)
        raise typer.Exit(1)

    # Generate settings
    settings = coordinator.generate_settings(
        scan_dir,
        include=include_list,
        exclude=exclude_list,
    )

    # Check if we have any plugins after filtering
    if not settings["plugins"]:
        msg = "\nNo plugins remaining after filtering. Nothing to generate."
        typer.echo(msg, err=True)
        raise typer.Exit(1)

    # Generate YAML content
    yaml_content = yaml.dump(settings, default_flow_style=False, sort_keys=False)

    if dry_run:
        typer.echo("\n--- Generated settings.yml (dry run) ---")
        typer.echo(yaml_content)
        typer.echo("--- End ---")
        typer.echo(f"\nWould write to: {output}")
        return

    # Write the file
    try:
        output.write_text(yaml_content)
    except OSError as e:
        typer.echo(f"Error: Failed to write {output}: {e}", err=True)
        raise typer.Exit(3) from e

    # Show summary
    plugin_count = len(settings["plugins"])
    typer.echo(f"\nGenerated {output} with {plugin_count} plugin(s):")
    for name in settings["plugins"]:
        typer.echo(f"  - {name}")

    typer.echo("\nRun 'cuff status' to verify the configuration.")


def _parse_comma_list(value: str | None) -> list[str] | None:
    """Parse a comma-separated string into a list.

    Args:
        value: Comma-separated string or None.

    Returns:
        List of stripped strings, or None if input was None.
    """
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]
