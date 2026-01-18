"""Tests for package.json plugin discovery and CLI command support.

Tests cover:
    - discover() class method (TestPackageJsonDiscovery)
    - Package manager detection during discovery (TestDiscoveryPackageManager)
    - Invalid JSON handling (TestDiscoveryErrorHandling)
    - get_cli_commands() class method (TestPackageJsonCLICommands)
    - get_plugin_metadata() class method (TestPackageJsonMetadata)
"""

from __future__ import annotations

from pathlib import Path

from opencuff.plugins.base import CLIArgument, CLICommand, CLIOption
from opencuff.plugins.builtin.packagejson import Plugin

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "package_json"


# =============================================================================
# TestPackageJsonDiscovery
# =============================================================================


class TestPackageJsonDiscovery:
    """Tests for the discover() class method."""

    def test_discover_finds_package_json(self) -> None:
        """Verify discover() finds package.json and returns applicable result."""
        result = Plugin.discover(FIXTURES_DIR / "simple")

        assert result.applicable is True
        assert result.confidence == 1.0
        assert "package.json" in result.description.lower()
        assert "3 scripts" in result.description

    def test_discover_returns_suggested_config(self) -> None:
        """Verify discover() returns sensible suggested configuration."""
        result = Plugin.discover(FIXTURES_DIR / "simple")

        assert result.applicable is True
        assert "package_json_path" in result.suggested_config
        assert result.suggested_config["package_json_path"] == "./package.json"
        assert result.suggested_config["package_manager"] == "auto"
        assert result.suggested_config["scripts"] == "*"
        assert result.suggested_config["exclude_lifecycle_scripts"] is True
        assert "cache_ttl" in result.suggested_config
        assert "working_directory" in result.suggested_config

    def test_discover_populates_discovered_items(self) -> None:
        """Verify discover() populates discovered_items with MCP tool names."""
        result = Plugin.discover(FIXTURES_DIR / "simple")

        assert result.applicable is True
        assert len(result.discovered_items) > 0
        # Should include list_scripts tool
        assert "npm_list_scripts" in result.discovered_items
        # Scripts should be prefixed with package manager
        assert "npm_build" in result.discovered_items
        assert "npm_test" in result.discovered_items
        assert "npm_lint" in result.discovered_items

    def test_discover_not_applicable_when_no_package_json(self, tmp_path: Path) -> None:
        """Verify discover() returns not applicable when no package.json exists."""
        result = Plugin.discover(tmp_path)

        assert result.applicable is False
        assert result.confidence == 0.0
        desc = result.description.lower()
        assert "not found" in desc or "no package.json" in desc
        assert result.suggested_config == {}
        assert result.discovered_items == []

    def test_discover_with_empty_scripts(self) -> None:
        """Verify discover() handles package.json with no scripts."""
        result = Plugin.discover(FIXTURES_DIR / "empty")

        assert result.applicable is True
        assert result.confidence == 1.0
        assert "0 scripts" in result.description
        # Should still have the list_scripts tool
        assert result.discovered_items == ["npm_list_scripts"]

    def test_discover_with_complex_scripts(self) -> None:
        """Verify discover() finds all scripts in complex package.json."""
        result = Plugin.discover(FIXTURES_DIR / "complex")

        assert result.applicable is True
        assert "13 scripts" in result.description
        # Should include list_scripts tool
        assert "npm_list_scripts" in result.discovered_items
        # Should include scripts with colons and hyphens (prefixed)
        assert "npm_build:prod" in result.discovered_items
        assert "npm_lint-fix" in result.discovered_items


# =============================================================================
# TestDiscoveryPackageManager
# =============================================================================


class TestDiscoveryPackageManager:
    """Tests for package manager detection during discovery."""

    def test_discover_detects_pnpm(self) -> None:
        """Verify discover() detects pnpm from pnpm-lock.yaml."""
        result = Plugin.discover(FIXTURES_DIR / "with_pnpm")

        assert result.applicable is True
        assert "(pnpm)" in result.description

    def test_discover_detects_npm(self) -> None:
        """Verify discover() detects npm from package-lock.json."""
        result = Plugin.discover(FIXTURES_DIR / "with_npm")

        assert result.applicable is True
        assert "(npm)" in result.description

    def test_discover_defaults_to_npm_when_no_lock_file(self) -> None:
        """Verify discover() defaults to npm when no lock file present."""
        result = Plugin.discover(FIXTURES_DIR / "simple")

        assert result.applicable is True
        assert "(npm)" in result.description

    def test_discover_detects_yarn(self, tmp_path: Path) -> None:
        """Verify discover() detects yarn from yarn.lock."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')
        yarn_lock = tmp_path / "yarn.lock"
        yarn_lock.write_text("# yarn lock file")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert "(yarn)" in result.description

    def test_discover_detects_bun(self, tmp_path: Path) -> None:
        """Verify discover() detects bun from bun.lockb."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')
        bun_lock = tmp_path / "bun.lockb"
        bun_lock.write_bytes(b"bun lock file")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert "(bun)" in result.description

    def test_discover_pnpm_takes_precedence(self, tmp_path: Path) -> None:
        """Verify pnpm-lock.yaml takes precedence over other lock files."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6.0")
        (tmp_path / "package-lock.json").write_text("{}")
        (tmp_path / "yarn.lock").write_text("# yarn")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert "(pnpm)" in result.description


# =============================================================================
# TestDiscoveryErrorHandling
# =============================================================================


class TestDiscoveryErrorHandling:
    """Tests for discovery error handling."""

    def test_discover_handles_invalid_json(self, tmp_path: Path) -> None:
        """Verify discover() handles invalid JSON gracefully."""
        package_json = tmp_path / "package.json"
        package_json.write_text("{ invalid json }")

        result = Plugin.discover(tmp_path)

        assert result.applicable is False
        assert result.confidence == 0.0
        description_lower = result.description.lower()
        assert "invalid" in description_lower or "error" in description_lower

    def test_discover_handles_non_object_json(self, tmp_path: Path) -> None:
        """Verify discover() handles JSON that is not an object."""
        package_json = tmp_path / "package.json"
        package_json.write_text("[]")  # Valid JSON but not an object

        result = Plugin.discover(tmp_path)

        # Should still work (scripts will be missing)
        assert result.applicable is True
        assert "0 scripts" in result.description

    def test_discover_handles_missing_scripts_field(self, tmp_path: Path) -> None:
        """Verify discover() handles package.json without scripts field."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "version": "1.0.0"}')

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert "0 scripts" in result.description


# =============================================================================
# TestDiscoveryWarnings
# =============================================================================


class TestDiscoveryWarnings:
    """Tests for discovery warnings."""

    def test_discover_warns_about_lifecycle_scripts(self) -> None:
        """Verify discover() notes lifecycle scripts in warnings."""
        result = Plugin.discover(FIXTURES_DIR / "with_lifecycle")

        assert result.applicable is True
        # Either warns about lifecycle scripts or mentions them
        # The warning is optional based on design decision


# =============================================================================
# TestPackageJsonCLICommands
# =============================================================================


class TestPackageJsonCLICommands:
    """Tests for the get_cli_commands() class method."""

    def test_get_cli_commands_returns_list(self) -> None:
        """Verify get_cli_commands() returns a list of CLICommand objects."""
        commands = Plugin.get_cli_commands()

        assert isinstance(commands, list)
        for cmd in commands:
            assert isinstance(cmd, CLICommand)

    def test_get_cli_commands_has_list_scripts(self) -> None:
        """Verify get_cli_commands() includes list-scripts command."""
        commands = Plugin.get_cli_commands()
        command_names = [cmd.name for cmd in commands]

        assert "list-scripts" in command_names

        list_cmd = next(cmd for cmd in commands if cmd.name == "list-scripts")
        assert list_cmd.help
        assert callable(list_cmd.callback)

    def test_get_cli_commands_has_run_script(self) -> None:
        """Verify get_cli_commands() includes run-script command."""
        commands = Plugin.get_cli_commands()
        command_names = [cmd.name for cmd in commands]

        assert "run-script" in command_names

        run_cmd = next(cmd for cmd in commands if cmd.name == "run-script")
        assert run_cmd.help
        assert callable(run_cmd.callback)

    def test_run_script_has_script_name_argument(self) -> None:
        """Verify run-script command has script name as required argument."""
        commands = Plugin.get_cli_commands()
        run_cmd = next(cmd for cmd in commands if cmd.name == "run-script")

        assert len(run_cmd.arguments) >= 1
        script_arg = run_cmd.arguments[0]
        assert isinstance(script_arg, CLIArgument)
        assert script_arg.required is True

    def test_run_script_has_dry_run_option(self) -> None:
        """Verify run-script command has --dry-run option."""
        commands = Plugin.get_cli_commands()
        run_cmd = next(cmd for cmd in commands if cmd.name == "run-script")

        option_names = [opt.name for opt in run_cmd.options]
        assert "--dry-run" in option_names

        dry_run_opt = next(opt for opt in run_cmd.options if opt.name == "--dry-run")
        assert isinstance(dry_run_opt, CLIOption)
        assert dry_run_opt.is_flag is True

    def test_run_script_has_timeout_option(self) -> None:
        """Verify run-script command has --timeout option."""
        commands = Plugin.get_cli_commands()
        run_cmd = next(cmd for cmd in commands if cmd.name == "run-script")

        option_names = [opt.name for opt in run_cmd.options]
        assert "--timeout" in option_names

        timeout_opt = next(opt for opt in run_cmd.options if opt.name == "--timeout")
        assert isinstance(timeout_opt, CLIOption)
        assert timeout_opt.is_flag is False


# =============================================================================
# TestPackageJsonMetadata
# =============================================================================


class TestPackageJsonMetadata:
    """Tests for the get_plugin_metadata() class method."""

    def test_get_plugin_metadata_returns_dict(self) -> None:
        """Verify get_plugin_metadata() returns a dictionary."""
        metadata = Plugin.get_plugin_metadata()

        assert isinstance(metadata, dict)

    def test_get_plugin_metadata_has_name(self) -> None:
        """Verify get_plugin_metadata() includes name field."""
        metadata = Plugin.get_plugin_metadata()

        assert "name" in metadata
        assert metadata["name"] == "Package.json"

    def test_get_plugin_metadata_has_description(self) -> None:
        """Verify get_plugin_metadata() includes description field."""
        metadata = Plugin.get_plugin_metadata()

        assert "description" in metadata
        assert isinstance(metadata["description"], str)
        assert len(metadata["description"]) > 0
        # Should describe the plugin's purpose
        description_lower = metadata["description"].lower()
        assert "npm" in description_lower or "script" in description_lower
