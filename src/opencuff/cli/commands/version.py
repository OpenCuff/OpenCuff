"""Version command for OpenCuff CLI.

This module provides the `cuff version` command that displays version information.
"""

import sys
from typing import Annotated

import typer


def get_version() -> str:
    """Get the installed OpenCuff version.

    Returns:
        Version string or 'unknown' if not found.
    """
    try:
        from importlib.metadata import version

        return version("opencuff")
    except Exception:
        return "unknown"


def version_command(
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed version information",
        ),
    ] = False,
) -> None:
    """Show OpenCuff version information.

    Displays the installed version of OpenCuff and optionally
    additional environment information.
    """
    opencuff_version = get_version()

    if not verbose:
        typer.echo(f"opencuff {opencuff_version}")
        return

    # Verbose output
    typer.echo(f"OpenCuff version: {opencuff_version}")
    typer.echo(f"Python version: {sys.version}")
    typer.echo(f"Python executable: {sys.executable}")

    # Show key dependency versions
    typer.echo("\nDependencies:")
    dependencies = ["fastmcp", "pydantic", "typer", "pyyaml"]
    for dep in dependencies:
        try:
            from importlib.metadata import version

            dep_version = version(dep)
            typer.echo(f"  {dep}: {dep_version}")
        except Exception:
            typer.echo(f"  {dep}: not found")
