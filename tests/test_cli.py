"""Tests for the OpenCuff CLI core structure.

Tests cover:
    - CLI app creation and command registration
    - DiscoveryCoordinator functionality
    - Init command behavior
    - Status command behavior
    - Doctor command behavior
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from opencuff.plugins.base import (
    DiscoveryResult,
    InSourcePlugin,
    ToolDefinition,
    ToolResult,
)


class TestDiscoveryCoordinator:
    """Tests for DiscoveryCoordinator class."""

    def test_discover_all_with_no_plugins_returns_empty(self, tmp_path: Path) -> None:
        """Verify discover_all returns empty dict when no plugins registered."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        coordinator = DiscoveryCoordinator(plugins={}, module_paths={})
        results = coordinator.discover_all(tmp_path)

        assert results == {}

    def test_discover_all_discovers_applicable_plugins(self, tmp_path: Path) -> None:
        """Verify discover_all returns results for applicable plugins."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        # Create a mock plugin class that is applicable
        class ApplicablePlugin(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={"key": "value"},
                    description="Found something",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        plugins = {"test_plugin": ApplicablePlugin}
        module_paths = {"test_plugin": "test.module.path"}
        coordinator = DiscoveryCoordinator(plugins=plugins, module_paths=module_paths)

        results = coordinator.discover_all(tmp_path)

        assert "test_plugin" in results
        assert results["test_plugin"].applicable is True
        assert results["test_plugin"].confidence == 1.0

    def test_discover_all_excludes_non_applicable_plugins(self, tmp_path: Path) -> None:
        """Verify discover_all includes non-applicable plugins in results."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        class NotApplicablePlugin(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=False,
                    confidence=0.0,
                    suggested_config={},
                    description="Not applicable",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        plugins = {"test_plugin": NotApplicablePlugin}
        module_paths = {"test_plugin": "test.module.path"}
        coordinator = DiscoveryCoordinator(plugins=plugins, module_paths=module_paths)

        results = coordinator.discover_all(tmp_path)

        # Non-applicable plugins are still in results (for reporting purposes)
        assert "test_plugin" in results
        assert results["test_plugin"].applicable is False

    def test_discover_all_raises_on_nonexistent_directory(self) -> None:
        """Verify discover_all raises error for non-existent directory."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        coordinator = DiscoveryCoordinator(plugins={}, module_paths={})

        with pytest.raises(ValueError, match="does not exist"):
            coordinator.discover_all(Path("/nonexistent/directory/path"))

    def test_discover_all_raises_on_file_instead_of_directory(
        self, tmp_path: Path
    ) -> None:
        """Verify discover_all raises error when given a file instead of directory."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        # Create a file
        file_path = tmp_path / "file.txt"
        file_path.write_text("test")

        coordinator = DiscoveryCoordinator(plugins={}, module_paths={})

        with pytest.raises(ValueError, match="not a directory"):
            coordinator.discover_all(file_path)

    def test_generate_settings_with_discovered_plugins(self, tmp_path: Path) -> None:
        """Verify generate_settings creates correct settings structure."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        class ApplicablePlugin(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={"option": "value"},
                    description="Found",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        plugins = {"test_plugin": ApplicablePlugin}
        module_paths = {"test_plugin": "test.module.path"}
        coordinator = DiscoveryCoordinator(plugins=plugins, module_paths=module_paths)

        settings = coordinator.generate_settings(tmp_path)

        assert "version" in settings
        assert "plugins" in settings
        assert "test_plugin" in settings["plugins"]
        assert settings["plugins"]["test_plugin"]["enabled"] is True
        assert settings["plugins"]["test_plugin"]["type"] == "in_source"
        assert settings["plugins"]["test_plugin"]["module"] == "test.module.path"
        assert settings["plugins"]["test_plugin"]["config"]["option"] == "value"

    def test_generate_settings_respects_include_filter(self, tmp_path: Path) -> None:
        """Verify generate_settings respects include filter."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        class Plugin1(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={},
                    description="Found",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        class Plugin2(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={},
                    description="Found",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        plugins = {"plugin1": Plugin1, "plugin2": Plugin2}
        module_paths = {"plugin1": "mod1", "plugin2": "mod2"}
        coordinator = DiscoveryCoordinator(plugins=plugins, module_paths=module_paths)

        settings = coordinator.generate_settings(tmp_path, include=["plugin1"])

        assert "plugin1" in settings["plugins"]
        assert "plugin2" not in settings["plugins"]

    def test_generate_settings_respects_exclude_filter(self, tmp_path: Path) -> None:
        """Verify generate_settings respects exclude filter."""
        from opencuff.cli.discovery import DiscoveryCoordinator

        class Plugin1(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={},
                    description="Found",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        class Plugin2(InSourcePlugin):
            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                return DiscoveryResult(
                    applicable=True,
                    confidence=1.0,
                    suggested_config={},
                    description="Found",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        plugins = {"plugin1": Plugin1, "plugin2": Plugin2}
        module_paths = {"plugin1": "mod1", "plugin2": "mod2"}
        coordinator = DiscoveryCoordinator(plugins=plugins, module_paths=module_paths)

        settings = coordinator.generate_settings(tmp_path, exclude=["plugin1"])

        assert "plugin1" not in settings["plugins"]
        assert "plugin2" in settings["plugins"]


class TestInitCommand:
    """Tests for the init command."""

    def test_init_creates_settings_file(self, tmp_path: Path) -> None:
        """Verify init command creates settings.yml file."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"

        # Create a Makefile so we have something to discover
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app, ["init", "--output", str(output_path)], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert output_path.exists()

    def test_init_respects_dry_run(self, tmp_path: Path) -> None:
        """Verify init --dry-run does not create file."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"

        # Create a Makefile for discovery
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app,
            ["init", "--output", str(output_path), "--dry-run"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert not output_path.exists()
        # Should show what would be generated
        assert "version" in result.output or "plugins" in result.output

    def test_init_fails_if_file_exists_without_force(self, tmp_path: Path) -> None:
        """Verify init fails if settings.yml exists without --force."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"
        output_path.write_text("existing content")

        result = runner.invoke(
            app, ["init", "--output", str(output_path)], catch_exceptions=False
        )

        # Should fail with exit code 2 (file exists)
        assert result.exit_code == 2
        # Original content should be unchanged
        assert output_path.read_text() == "existing content"

    def test_init_overwrites_with_force_flag(self, tmp_path: Path) -> None:
        """Verify init --force overwrites existing file."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"
        output_path.write_text("existing content")

        # Create a Makefile for discovery
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app,
            ["init", "--output", str(output_path), "--force"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        # Content should have changed
        new_content = output_path.read_text()
        assert new_content != "existing content"
        assert "version" in new_content

    def test_init_with_plugins_filter(self, tmp_path: Path) -> None:
        """Verify init --plugins filters which plugins to include."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"

        # Create both Makefile and package.json
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')

        result = runner.invoke(
            app,
            ["init", "--output", str(output_path), "--plugins", "makefile"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        content = yaml.safe_load(output_path.read_text())
        assert "makefile" in content.get("plugins", {})
        # packagejson should not be included
        assert "packagejson" not in content.get("plugins", {})

    def test_init_with_exclude_filter(self, tmp_path: Path) -> None:
        """Verify init --exclude filters out specified plugins."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"

        # Create both Makefile and package.json
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')

        result = runner.invoke(
            app,
            ["init", "--output", str(output_path), "--exclude", "makefile"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        content = yaml.safe_load(output_path.read_text())
        # makefile should be excluded
        assert "makefile" not in content.get("plugins", {})


class TestStatusCommand:
    """Tests for the status command."""

    def test_status_shows_plugin_info(self, tmp_path: Path) -> None:
        """Verify status command shows plugin information."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Create a minimal settings file
        settings_path = tmp_path / "settings.yml"
        settings_content = {
            "version": "1",
            "plugins": {
                "makefile": {
                    "enabled": True,
                    "type": "in_source",
                    "module": "opencuff.plugins.builtin.makefile",
                    "config": {"makefile_path": "./Makefile"},
                }
            },
        }
        settings_path.write_text(yaml.dump(settings_content))

        # Create a Makefile
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app, ["status", "--config", str(settings_path)], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert "makefile" in result.output.lower()

    def test_status_with_json_output(self, tmp_path: Path) -> None:
        """Verify status --json returns valid JSON."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Create a minimal settings file
        settings_path = tmp_path / "settings.yml"
        settings_content = {
            "version": "1",
            "plugins": {
                "makefile": {
                    "enabled": True,
                    "type": "in_source",
                    "module": "opencuff.plugins.builtin.makefile",
                    "config": {"makefile_path": "./Makefile"},
                }
            },
        }
        settings_path.write_text(yaml.dump(settings_content))

        # Create a Makefile
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app,
            ["status", "--config", str(settings_path), "--json"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        # Output should be valid JSON
        data = json.loads(result.output)
        assert "plugins" in data or "status" in data

    def test_status_fails_without_config(self, tmp_path: Path) -> None:
        """Verify status fails gracefully when config not found."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Use a non-existent config path
        result = runner.invoke(
            app,
            ["status", "--config", str(tmp_path / "nonexistent.yml")],
            catch_exceptions=False,
        )

        # Should fail with appropriate error
        assert result.exit_code != 0


class TestDoctorCommand:
    """Tests for the doctor command."""

    def test_doctor_checks_settings_file_exists(self, tmp_path: Path) -> None:
        """Verify doctor checks if settings file exists."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Create a valid settings file
        settings_path = tmp_path / "settings.yml"
        settings_content = {"version": "1", "plugins": {}}
        settings_path.write_text(yaml.dump(settings_content))

        result = runner.invoke(
            app, ["doctor", "--config", str(settings_path)], catch_exceptions=False
        )

        assert result.exit_code == 0
        # Should show PASS for settings file check
        assert "pass" in result.output.lower() or "ok" in result.output.lower()

    def test_doctor_checks_yaml_validity(self, tmp_path: Path) -> None:
        """Verify doctor checks if settings file is valid YAML."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Create an invalid YAML file
        settings_path = tmp_path / "settings.yml"
        settings_path.write_text("invalid: yaml: content: [[[")

        result = runner.invoke(
            app, ["doctor", "--config", str(settings_path)], catch_exceptions=False
        )

        # Should show failure for YAML check
        assert "fail" in result.output.lower() or "error" in result.output.lower()

    def test_doctor_checks_referenced_files_exist(self, tmp_path: Path) -> None:
        """Verify doctor checks if referenced files exist."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Create settings referencing a non-existent Makefile
        settings_path = tmp_path / "settings.yml"
        settings_content = {
            "version": "1",
            "plugins": {
                "makefile": {
                    "enabled": True,
                    "type": "in_source",
                    "module": "opencuff.plugins.builtin.makefile",
                    "config": {"makefile_path": "./Makefile"},
                }
            },
        }
        settings_path.write_text(yaml.dump(settings_content))

        # Don't create the Makefile - it should be missing

        result = runner.invoke(
            app, ["doctor", "--config", str(settings_path)], catch_exceptions=False
        )

        # Should warn about missing Makefile
        assert (
            "makefile" in result.output.lower()
            or "warn" in result.output.lower()
            or "fail" in result.output.lower()
        )

    def test_doctor_reports_all_checks(self, tmp_path: Path) -> None:
        """Verify doctor reports results of all checks."""
        from opencuff.cli.main import app

        runner = CliRunner()

        # Create a valid settings file with Makefile plugin
        settings_path = tmp_path / "settings.yml"
        settings_content = {
            "version": "1",
            "plugins": {
                "makefile": {
                    "enabled": True,
                    "type": "in_source",
                    "module": "opencuff.plugins.builtin.makefile",
                    "config": {"makefile_path": "./Makefile"},
                }
            },
        }
        settings_path.write_text(yaml.dump(settings_content))

        # Create the Makefile
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app, ["doctor", "--config", str(settings_path)], catch_exceptions=False
        )

        assert result.exit_code == 0
        # Should mention settings file check
        assert "settings" in result.output.lower() or "yaml" in result.output.lower()


class TestCLIAppStructure:
    """Tests for CLI app structure and command registration."""

    def test_app_has_init_command(self) -> None:
        """Verify CLI app has init command registered."""
        from opencuff.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert "init" in result.output

    def test_app_has_status_command(self) -> None:
        """Verify CLI app has status command registered."""
        from opencuff.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert "status" in result.output

    def test_app_has_doctor_command(self) -> None:
        """Verify CLI app has doctor command registered."""
        from opencuff.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert "doctor" in result.output

    def test_app_name_is_cuff(self) -> None:
        """Verify CLI app name is 'cuff'."""
        from opencuff.cli.main import app

        assert app.info.name == "cuff"

    def test_app_shows_help_with_no_args(self) -> None:
        """Verify app shows help when invoked with no arguments."""
        from opencuff.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, [])

        # Should show help (no_args_is_help=True)
        assert "Usage" in result.output or "Commands" in result.output


class TestInitCommandExitCodes:
    """Tests for init command exit codes."""

    def test_init_returns_zero_on_success(self, tmp_path: Path) -> None:
        """Verify init returns exit code 0 on success."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"

        # Create a Makefile for discovery
        (tmp_path / "Makefile").write_text("build:\n\techo build\n")

        result = runner.invoke(
            app, ["init", "--output", str(output_path)], catch_exceptions=False
        )

        assert result.exit_code == 0

    def test_init_returns_one_when_no_plugins_discovered(self, tmp_path: Path) -> None:
        """Verify init returns exit code 1 when no plugins discovered."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"

        # Empty directory - no Makefile or package.json

        result = runner.invoke(
            app, ["init", "--output", str(output_path)], catch_exceptions=False
        )

        # Exit code 1 = no plugins discovered
        assert result.exit_code == 1

    def test_init_returns_two_when_file_exists(self, tmp_path: Path) -> None:
        """Verify init returns exit code 2 when file exists without --force."""
        from opencuff.cli.main import app

        runner = CliRunner()
        output_path = tmp_path / "settings.yml"
        output_path.write_text("existing")

        result = runner.invoke(
            app, ["init", "--output", str(output_path)], catch_exceptions=False
        )

        assert result.exit_code == 2
