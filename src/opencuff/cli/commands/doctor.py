"""Doctor command for OpenCuff CLI.

This module provides the `cuff doctor` command that diagnoses common issues
and suggests fixes for the OpenCuff configuration.

Checks performed:
    1. Settings file exists and is valid YAML
    2. All referenced files exist (Makefile, package.json, etc.)
    3. Plugin modules can be imported
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
import yaml


@dataclass
class CheckResult:
    """Result of a diagnostic check.

    Attributes:
        name: Short name of the check.
        passed: Whether the check passed.
        message: Descriptive message about the result.
        suggestion: Optional suggestion for fixing failures.
    """

    name: str
    passed: bool
    message: str
    suggestion: str | None = None


def doctor_command(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to settings.yml",
        ),
    ] = Path("./settings.yml"),
) -> None:
    """Diagnose common issues and suggest fixes.

    Runs a series of diagnostic checks on the OpenCuff configuration
    and reports the results.
    """
    typer.echo("Running diagnostics...\n")

    checks: list[CheckResult] = []
    settings_data: dict | None = None

    # Check 1: Settings file exists
    file_exists_check = _check_settings_file_exists(config)
    checks.append(file_exists_check)

    if file_exists_check.passed:
        # Check 2: Valid YAML
        yaml_check, settings_data = _check_yaml_validity(config)
        checks.append(yaml_check)

        if yaml_check.passed and settings_data is not None:
            # Check 3: Referenced files exist
            file_checks = _check_referenced_files(config, settings_data)
            checks.extend(file_checks)

            # Check 4: Plugin modules can be imported
            module_checks = _check_plugin_modules(settings_data)
            checks.extend(module_checks)

    # Display results
    _display_results(checks)

    # Set exit code based on results
    has_failures = any(not check.passed for check in checks)
    if has_failures:
        raise typer.Exit(1)


def _check_settings_file_exists(config: Path) -> CheckResult:
    """Check if the settings file exists.

    Args:
        config: Path to the settings file.

    Returns:
        CheckResult indicating pass/fail.
    """
    if config.exists():
        return CheckResult(
            name="Settings file",
            passed=True,
            message=f"Found: {config}",
        )
    return CheckResult(
        name="Settings file",
        passed=False,
        message=f"Not found: {config}",
        suggestion="Run 'cuff init' to create a configuration file.",
    )


def _check_yaml_validity(config: Path) -> tuple[CheckResult, dict | None]:
    """Check if the settings file contains valid YAML.

    Args:
        config: Path to the settings file.

    Returns:
        Tuple of (CheckResult, parsed data or None).
    """
    try:
        content = config.read_text()
        data = yaml.safe_load(content)
        if data is None:
            data = {}
        return (
            CheckResult(
                name="YAML syntax",
                passed=True,
                message="Valid YAML",
            ),
            data,
        )
    except yaml.YAMLError as e:
        return (
            CheckResult(
                name="YAML syntax",
                passed=False,
                message=f"Invalid YAML: {e}",
                suggestion="Check the YAML syntax and fix any formatting errors.",
            ),
            None,
        )


def _check_referenced_files(config: Path, settings_data: dict) -> list[CheckResult]:
    """Check if files referenced in plugin configs exist.

    Args:
        config: Path to the settings file (for resolving relative paths).
        settings_data: Parsed settings dictionary.

    Returns:
        List of CheckResults for each referenced file.
    """
    results: list[CheckResult] = []
    base_dir = config.parent

    plugins = settings_data.get("plugins", {})

    for name, plugin_config in plugins.items():
        if not isinstance(plugin_config, dict):
            continue

        if not plugin_config.get("enabled", True):
            continue

        config_section = plugin_config.get("config", {})

        # Check for Makefile reference
        if name == "makefile" or "makefile_path" in config_section:
            makefile_path = config_section.get("makefile_path", "./Makefile")
            full_path = base_dir / makefile_path
            if full_path.exists():
                results.append(
                    CheckResult(
                        name=f"{name} plugin",
                        passed=True,
                        message=f"File exists: {makefile_path}",
                    )
                )
            else:
                suggestion = (
                    f"Create {makefile_path} or update the path in settings.yml."
                )
                results.append(
                    CheckResult(
                        name=f"{name} plugin",
                        passed=False,
                        message=f"File not found: {makefile_path}",
                        suggestion=suggestion,
                    )
                )

        # Check for package.json reference
        if name == "packagejson" or "package_json_path" in config_section:
            pkg_json = config_section.get("package_json_path", "./package.json")
            package_json_path = pkg_json
            full_path = base_dir / package_json_path
            if full_path.exists():
                results.append(
                    CheckResult(
                        name=f"{name} plugin",
                        passed=True,
                        message=f"File exists: {package_json_path}",
                    )
                )
            else:
                suggestion = (
                    f"Create {package_json_path} or update the path in settings.yml."
                )
                results.append(
                    CheckResult(
                        name=f"{name} plugin",
                        passed=False,
                        message=f"File not found: {package_json_path}",
                        suggestion=suggestion,
                    )
                )

    return results


def _check_plugin_modules(settings_data: dict) -> list[CheckResult]:
    """Check if plugin modules can be imported.

    Args:
        settings_data: Parsed settings dictionary.

    Returns:
        List of CheckResults for each module import check.
    """
    results: list[CheckResult] = []
    plugins = settings_data.get("plugins", {})

    for name, plugin_config in plugins.items():
        if not isinstance(plugin_config, dict):
            continue

        if not plugin_config.get("enabled", True):
            continue

        module_path = plugin_config.get("module")
        if not module_path:
            continue

        try:
            __import__(module_path)
            results.append(
                CheckResult(
                    name=f"Module {name}",
                    passed=True,
                    message=f"Import successful: {module_path}",
                )
            )
        except ImportError as e:
            suggestion = (
                "Check that the module path is correct and the module is installed."
            )
            results.append(
                CheckResult(
                    name=f"Module {name}",
                    passed=False,
                    message=f"Import failed: {module_path} ({e})",
                    suggestion=suggestion,
                )
            )

    return results


def _display_results(checks: list[CheckResult]) -> None:
    """Display check results in a human-readable format.

    Args:
        checks: List of CheckResults to display.
    """
    passed_count = 0
    failed_count = 0

    for check in checks:
        if check.passed:
            passed_count += 1
            prefix = "[PASS]"
        else:
            failed_count += 1
            prefix = "[FAIL]"

        typer.echo(f"{prefix} {check.name}: {check.message}")

        if check.suggestion:
            typer.echo(f"       Suggestion: {check.suggestion}")

    typer.echo()
    typer.echo(f"Summary: {passed_count} passed, {failed_count} errors")
