"""Tests for Makefile plugin discovery, CLI commands, and metadata.

Tests cover:
    - discover() class method for detecting Makefiles
    - get_cli_commands() class method for CLI integration
    - get_plugin_metadata() class method for plugin information
"""

from __future__ import annotations

from pathlib import Path

from opencuff.plugins.base import CLIArgument, CLICommand, CLIOption
from opencuff.plugins.builtin.makefile import Plugin

# =============================================================================
# TestDiscover
# =============================================================================


class TestDiscover:
    """Tests for Plugin.discover() class method."""

    def test_discover_finds_makefile(self, tmp_path: Path) -> None:
        """Verify discover finds Makefile and returns correct result."""
        # Create a Makefile with some targets
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            ".PHONY: build test clean\n\n"
            "## Build the project\n"
            "build:\n\t@echo build\n\n"
            "## Run tests\n"
            "test:\n\t@echo test\n\n"
            "clean:\n\trm -rf build/\n"
        )

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert result.confidence == 1.0
        assert "makefile_path" in result.suggested_config
        assert result.suggested_config["targets"] == "*"
        assert result.suggested_config["extractor"] == "auto"
        assert "Makefile" in result.description or "makefile" in result.description
        assert len(result.discovered_items) > 0

    def test_discover_returns_target_names_in_discovered_items(
        self, tmp_path: Path
    ) -> None:
        """Verify discover returns target names in discovered_items."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            ".PHONY: build test clean\n\n"
            "build:\n\t@echo build\n\n"
            "test:\n\t@echo test\n\n"
            "clean:\n\t@echo clean\n"
        )

        result = Plugin.discover(tmp_path)

        assert isinstance(result.discovered_items, list)
        assert len(result.discovered_items) > 0
        # Check that we get actual target names
        for item in result.discovered_items:
            assert isinstance(item, str)
            assert len(item) > 0
        # Verify specific targets are found
        assert "build" in result.discovered_items
        assert "test" in result.discovered_items

    def test_discover_limits_discovered_items_to_first_10(self, tmp_path: Path) -> None:
        """Verify discover limits discovered_items to first 10 targets."""
        # Create a Makefile with more than 10 targets
        targets = [f"target{i}" for i in range(15)]
        phony_line = f".PHONY: {' '.join(targets)}"
        target_lines = [f"{t}:\n\t@echo {t}" for t in targets]
        content = phony_line + "\n\n" + "\n\n".join(target_lines)

        makefile = tmp_path / "Makefile"
        makefile.write_text(content)

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert len(result.discovered_items) <= 10

    def test_discover_not_applicable_when_no_makefile(self, tmp_path: Path) -> None:
        """Verify discover returns not applicable when no Makefile exists."""
        result = Plugin.discover(tmp_path)

        assert result.applicable is False
        assert result.confidence == 0.0
        assert result.suggested_config == {}
        description_lower = result.description.lower()
        assert "no makefile" in description_lower or "not found" in description_lower

    def test_discover_finds_lowercase_makefile(self, tmp_path: Path) -> None:
        """Verify discover finds lowercase 'makefile'."""
        makefile = tmp_path / "makefile"
        makefile.write_text(".PHONY: test\ntest:\n\t@echo test")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert result.confidence == 1.0
        # On case-insensitive filesystems (like macOS), 'Makefile' check
        # may match 'makefile'. Just check that a makefile path is returned.
        assert "makefile" in result.suggested_config["makefile_path"].lower()

    def test_discover_finds_gnumakefile(self, tmp_path: Path) -> None:
        """Verify discover finds GNUmakefile."""
        makefile = tmp_path / "GNUmakefile"
        makefile.write_text(".PHONY: build\nbuild:\n\t@echo build")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        assert result.confidence == 1.0
        assert "GNUmakefile" in result.suggested_config["makefile_path"]

    def test_discover_prefers_makefile_over_gnumakefile(self, tmp_path: Path) -> None:
        """Verify discover prefers Makefile over GNUmakefile."""
        # Create both files
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: main\nmain:\n\t@echo main")

        gnumakefile = tmp_path / "GNUmakefile"
        gnumakefile.write_text(".PHONY: gnu\ngnu:\n\t@echo gnu")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        # Should prefer Makefile (standard)
        assert "Makefile" in result.suggested_config["makefile_path"]
        assert "GNUmakefile" not in result.suggested_config["makefile_path"]

    def test_discover_suggested_config_has_sensible_defaults(
        self, tmp_path: Path
    ) -> None:
        """Verify suggested_config contains all expected default values."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: build\nbuild:\n\t@echo build")

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        config = result.suggested_config

        # Check all expected keys are present
        assert "makefile_path" in config
        assert "targets" in config
        assert "extractor" in config
        assert "cache_ttl" in config
        assert "trust_makefile" in config
        assert "working_directory" in config

        # Check sensible default values
        assert config["targets"] == "*"
        assert config["extractor"] == "auto"
        assert config["cache_ttl"] == 300
        assert config["trust_makefile"] is True
        assert config["working_directory"] == "."

    def test_discover_description_includes_target_count(self, tmp_path: Path) -> None:
        """Verify description includes the number of targets found."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            ".PHONY: build test\n\nbuild:\n\t@echo build\n\ntest:\n\t@echo test\n"
        )

        result = Plugin.discover(tmp_path)

        assert result.applicable is True
        # Description should mention target count like "Found Makefile with N targets"
        assert "target" in result.description.lower()
        # Should contain a number
        import re

        assert re.search(r"\d+", result.description) is not None

    def test_discover_with_empty_makefile(self, tmp_path: Path) -> None:
        """Verify discover handles empty Makefile gracefully."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("")

        result = Plugin.discover(tmp_path)

        # Empty Makefile is still a Makefile
        assert result.applicable is True
        assert result.confidence == 1.0
        assert result.discovered_items == []
        assert "0" in result.description  # 0 targets


# =============================================================================
# TestGetCLICommands
# =============================================================================


class TestGetCLICommands:
    """Tests for Plugin.get_cli_commands() class method."""

    def test_get_cli_commands_returns_list(self) -> None:
        """Verify get_cli_commands returns a list."""
        commands = Plugin.get_cli_commands()

        assert isinstance(commands, list)

    def test_get_cli_commands_returns_cli_command_objects(self) -> None:
        """Verify get_cli_commands returns CLICommand objects."""
        commands = Plugin.get_cli_commands()

        assert len(commands) > 0
        for cmd in commands:
            assert isinstance(cmd, CLICommand)

    def test_list_targets_command_exists(self) -> None:
        """Verify list-targets command is provided."""
        commands = Plugin.get_cli_commands()
        command_names = [cmd.name for cmd in commands]

        assert "list-targets" in command_names

    def test_run_target_command_exists(self) -> None:
        """Verify run-target command is provided."""
        commands = Plugin.get_cli_commands()
        command_names = [cmd.name for cmd in commands]

        assert "run-target" in command_names

    def test_list_targets_command_has_help(self) -> None:
        """Verify list-targets command has help text."""
        commands = Plugin.get_cli_commands()
        list_cmd = next(cmd for cmd in commands if cmd.name == "list-targets")

        assert list_cmd.help
        assert len(list_cmd.help) > 0

    def test_run_target_command_has_target_argument(self) -> None:
        """Verify run-target command has target argument."""
        commands = Plugin.get_cli_commands()
        run_cmd = next(cmd for cmd in commands if cmd.name == "run-target")

        assert len(run_cmd.arguments) > 0
        target_arg = run_cmd.arguments[0]
        assert isinstance(target_arg, CLIArgument)
        assert target_arg.name == "target"
        assert target_arg.required is True

    def test_run_target_command_has_dry_run_option(self) -> None:
        """Verify run-target command has --dry-run option."""
        commands = Plugin.get_cli_commands()
        run_cmd = next(cmd for cmd in commands if cmd.name == "run-target")

        option_names = [opt.name for opt in run_cmd.options]
        assert "--dry-run" in option_names

        dry_run_opt = next(opt for opt in run_cmd.options if opt.name == "--dry-run")
        assert isinstance(dry_run_opt, CLIOption)
        assert dry_run_opt.is_flag is True

    def test_run_target_command_has_timeout_option(self) -> None:
        """Verify run-target command has --timeout option."""
        commands = Plugin.get_cli_commands()
        run_cmd = next(cmd for cmd in commands if cmd.name == "run-target")

        option_names = [opt.name for opt in run_cmd.options]
        assert "--timeout" in option_names

        timeout_opt = next(opt for opt in run_cmd.options if opt.name == "--timeout")
        assert isinstance(timeout_opt, CLIOption)
        assert timeout_opt.is_flag is False

    def test_commands_have_callbacks(self) -> None:
        """Verify all commands have callable callbacks."""
        commands = Plugin.get_cli_commands()

        for cmd in commands:
            assert callable(cmd.callback)


# =============================================================================
# TestGetPluginMetadata
# =============================================================================


class TestGetPluginMetadata:
    """Tests for Plugin.get_plugin_metadata() class method."""

    def test_get_plugin_metadata_returns_dict(self) -> None:
        """Verify get_plugin_metadata returns a dictionary."""
        metadata = Plugin.get_plugin_metadata()

        assert isinstance(metadata, dict)

    def test_get_plugin_metadata_has_name(self) -> None:
        """Verify metadata includes name field."""
        metadata = Plugin.get_plugin_metadata()

        assert "name" in metadata
        assert metadata["name"] == "Makefile"

    def test_get_plugin_metadata_has_description(self) -> None:
        """Verify metadata includes description field."""
        metadata = Plugin.get_plugin_metadata()

        assert "description" in metadata
        assert len(metadata["description"]) > 0

    def test_get_plugin_metadata_description_is_meaningful(self) -> None:
        """Verify description mentions Makefile functionality."""
        metadata = Plugin.get_plugin_metadata()

        description = metadata["description"].lower()
        # Should mention Makefile-related terms
        has_makefile_term = (
            "makefile" in description
            or "make" in description
            or "target" in description
        )
        assert has_makefile_term
