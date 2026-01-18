"""Tests for the plugin discovery interface.

Tests cover:
    - DiscoveryResult dataclass creation and defaults
    - CLIArgument, CLIOption, CLICommand dataclasses
    - InSourcePlugin.discover() default implementation
    - InSourcePlugin.get_cli_commands() default implementation
    - InSourcePlugin.get_plugin_metadata() default implementation
    - Discovery registry functions
"""

from pathlib import Path
from typing import Any

from opencuff.plugins.base import (
    CLIArgument,
    CLICommand,
    CLIOption,
    DiscoveryResult,
    InSourcePlugin,
    ToolDefinition,
    ToolResult,
)


class TestDiscoveryResult:
    """Tests for DiscoveryResult dataclass."""

    def test_create_with_all_required_fields(self) -> None:
        """Verify DiscoveryResult can be created with required fields."""
        result = DiscoveryResult(
            applicable=True,
            confidence=0.8,
            suggested_config={"key": "value"},
            description="Found something",
        )

        assert result.applicable is True
        assert result.confidence == 0.8
        assert result.suggested_config == {"key": "value"}
        assert result.description == "Found something"

    def test_warnings_defaults_to_empty_list(self) -> None:
        """Verify warnings field defaults to empty list."""
        result = DiscoveryResult(
            applicable=True,
            confidence=1.0,
            suggested_config={},
            description="Test",
        )

        assert result.warnings == []

    def test_discovered_items_defaults_to_empty_list(self) -> None:
        """Verify discovered_items field defaults to empty list."""
        result = DiscoveryResult(
            applicable=True,
            confidence=1.0,
            suggested_config={},
            description="Test",
        )

        assert result.discovered_items == []

    def test_warnings_can_be_provided(self) -> None:
        """Verify warnings can be provided as a list."""
        warnings = ["Warning 1", "Warning 2"]
        result = DiscoveryResult(
            applicable=True,
            confidence=0.5,
            suggested_config={},
            description="Test",
            warnings=warnings,
        )

        assert result.warnings == ["Warning 1", "Warning 2"]

    def test_discovered_items_can_be_provided(self) -> None:
        """Verify discovered_items can be provided as a list."""
        items = ["target1", "target2", "target3"]
        result = DiscoveryResult(
            applicable=True,
            confidence=1.0,
            suggested_config={},
            description="Found items",
            discovered_items=items,
        )

        assert result.discovered_items == ["target1", "target2", "target3"]

    def test_not_applicable_result(self) -> None:
        """Verify a not-applicable result can be created."""
        result = DiscoveryResult(
            applicable=False,
            confidence=0.0,
            suggested_config={},
            description="No Makefile found",
        )

        assert result.applicable is False
        assert result.confidence == 0.0
        assert result.suggested_config == {}

    def test_confidence_boundaries(self) -> None:
        """Verify confidence can be 0.0 and 1.0."""
        result_zero = DiscoveryResult(
            applicable=False,
            confidence=0.0,
            suggested_config={},
            description="Test",
        )
        result_one = DiscoveryResult(
            applicable=True,
            confidence=1.0,
            suggested_config={},
            description="Test",
        )

        assert result_zero.confidence == 0.0
        assert result_one.confidence == 1.0


class TestCLIArgument:
    """Tests for CLIArgument dataclass."""

    def test_create_with_required_fields(self) -> None:
        """Verify CLIArgument can be created with required fields."""
        arg = CLIArgument(
            name="target",
            help="The target to run",
        )

        assert arg.name == "target"
        assert arg.help == "The target to run"

    def test_required_defaults_to_true(self) -> None:
        """Verify required field defaults to True."""
        arg = CLIArgument(name="target", help="Test")

        assert arg.required is True

    def test_default_defaults_to_none(self) -> None:
        """Verify default field defaults to None."""
        arg = CLIArgument(name="target", help="Test")

        assert arg.default is None

    def test_optional_argument(self) -> None:
        """Verify optional argument can be created."""
        arg = CLIArgument(
            name="output",
            help="Output file",
            required=False,
            default="output.txt",
        )

        assert arg.required is False
        assert arg.default == "output.txt"


class TestCLIOption:
    """Tests for CLIOption dataclass."""

    def test_create_with_required_fields(self) -> None:
        """Verify CLIOption can be created with required fields."""
        option = CLIOption(
            name="--dry-run",
            help="Show commands without executing",
        )

        assert option.name == "--dry-run"
        assert option.help == "Show commands without executing"

    def test_is_flag_defaults_to_false(self) -> None:
        """Verify is_flag defaults to False."""
        option = CLIOption(name="--verbose", help="Test")

        assert option.is_flag is False

    def test_default_defaults_to_none(self) -> None:
        """Verify default field defaults to None."""
        option = CLIOption(name="--verbose", help="Test")

        assert option.default is None

    def test_type_defaults_to_str(self) -> None:
        """Verify type field defaults to str."""
        option = CLIOption(name="--count", help="Test")

        assert option.type is str

    def test_flag_option(self) -> None:
        """Verify flag option can be created."""
        option = CLIOption(
            name="--force",
            help="Force the operation",
            is_flag=True,
            default=False,
        )

        assert option.is_flag is True
        assert option.default is False

    def test_typed_option(self) -> None:
        """Verify typed option can be created."""
        option = CLIOption(
            name="--count",
            help="Number of items",
            type=int,
            default=10,
        )

        assert option.type is int
        assert option.default == 10


class TestCLICommand:
    """Tests for CLICommand dataclass."""

    def test_create_with_required_fields(self) -> None:
        """Verify CLICommand can be created with required fields."""

        def dummy_callback() -> None:
            pass

        cmd = CLICommand(
            name="list-targets",
            help="List available targets",
            callback=dummy_callback,
        )

        assert cmd.name == "list-targets"
        assert cmd.help == "List available targets"
        assert cmd.callback is dummy_callback

    def test_arguments_defaults_to_empty_list(self) -> None:
        """Verify arguments field defaults to empty list."""
        cmd = CLICommand(
            name="test",
            help="Test",
            callback=lambda: None,
        )

        assert cmd.arguments == []

    def test_options_defaults_to_empty_list(self) -> None:
        """Verify options field defaults to empty list."""
        cmd = CLICommand(
            name="test",
            help="Test",
            callback=lambda: None,
        )

        assert cmd.options == []

    def test_command_with_arguments_and_options(self) -> None:
        """Verify command can have arguments and options."""

        def run_target(target: str, dry_run: bool = False) -> None:
            pass

        cmd = CLICommand(
            name="run-target",
            help="Execute a target",
            callback=run_target,
            arguments=[
                CLIArgument(name="target", help="Target to run"),
            ],
            options=[
                CLIOption(name="--dry-run", help="Dry run mode", is_flag=True),
            ],
        )

        assert len(cmd.arguments) == 1
        assert cmd.arguments[0].name == "target"
        assert len(cmd.options) == 1
        assert cmd.options[0].name == "--dry-run"


class TestInSourcePluginDiscovery:
    """Tests for InSourcePlugin discovery-related methods."""

    def _create_simple_plugin_class(self) -> type[InSourcePlugin]:
        """Create a simple concrete plugin class for testing."""

        class SimplePlugin(InSourcePlugin):
            """A simple test plugin."""

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        return SimplePlugin

    def test_discover_returns_not_applicable_by_default(self, tmp_path: Path) -> None:
        """Verify default discover() returns not applicable."""
        PluginClass = self._create_simple_plugin_class()

        result = PluginClass.discover(tmp_path)

        assert result.applicable is False
        assert result.confidence == 0.0
        assert result.suggested_config == {}
        assert "does not support" in result.description.lower()

    def test_discover_is_class_method(self) -> None:
        """Verify discover() is a class method and can be called without instance."""
        PluginClass = self._create_simple_plugin_class()

        # Should not raise - calling on class, not instance
        result = PluginClass.discover(Path("/nonexistent"))

        assert isinstance(result, DiscoveryResult)

    def test_get_cli_commands_returns_empty_list_by_default(self) -> None:
        """Verify default get_cli_commands() returns empty list."""
        PluginClass = self._create_simple_plugin_class()

        commands = PluginClass.get_cli_commands()

        assert commands == []
        assert isinstance(commands, list)

    def test_get_cli_commands_is_class_method(self) -> None:
        """Verify get_cli_commands() is a class method."""
        PluginClass = self._create_simple_plugin_class()

        # Should not raise - calling on class, not instance
        commands = PluginClass.get_cli_commands()

        assert isinstance(commands, list)

    def test_get_plugin_metadata_returns_name_and_description(self) -> None:
        """Verify get_plugin_metadata() returns plugin name and description."""
        PluginClass = self._create_simple_plugin_class()

        metadata = PluginClass.get_plugin_metadata()

        assert "name" in metadata
        assert "description" in metadata
        assert metadata["name"] == "SimplePlugin"
        assert "simple test plugin" in metadata["description"].lower()

    def test_get_plugin_metadata_is_class_method(self) -> None:
        """Verify get_plugin_metadata() is a class method."""
        PluginClass = self._create_simple_plugin_class()

        # Should not raise - calling on class, not instance
        metadata = PluginClass.get_plugin_metadata()

        assert isinstance(metadata, dict)

    def test_get_plugin_metadata_with_no_docstring(self) -> None:
        """Verify get_plugin_metadata() handles plugin with no docstring."""

        class NoDocPlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        metadata = NoDocPlugin.get_plugin_metadata()

        assert metadata["name"] == "NoDocPlugin"
        assert metadata["description"] == "No description"


class TestInSourcePluginDiscoveryOverride:
    """Tests for overriding discovery methods in subclasses."""

    def test_can_override_discover(self, tmp_path: Path) -> None:
        """Verify subclasses can override discover() method."""

        class DiscoverablePlugin(InSourcePlugin):
            """A plugin that supports discovery."""

            @classmethod
            def discover(cls, directory: Path) -> DiscoveryResult:
                marker_file = directory / "marker.txt"
                if marker_file.exists():
                    return DiscoveryResult(
                        applicable=True,
                        confidence=1.0,
                        suggested_config={"found": True},
                        description="Found marker file",
                    )
                return DiscoveryResult(
                    applicable=False,
                    confidence=0.0,
                    suggested_config={},
                    description="No marker file found",
                )

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        # Test without marker file
        result_without = DiscoverablePlugin.discover(tmp_path)
        assert result_without.applicable is False

        # Create marker file and test again
        (tmp_path / "marker.txt").write_text("test")
        result_with = DiscoverablePlugin.discover(tmp_path)
        assert result_with.applicable is True
        assert result_with.confidence == 1.0
        assert result_with.suggested_config == {"found": True}

    def test_can_override_get_cli_commands(self) -> None:
        """Verify subclasses can override get_cli_commands() method."""

        class PluginWithCLI(InSourcePlugin):
            """A plugin that provides CLI commands."""

            @classmethod
            def _cli_list_items(cls) -> list[str]:
                return ["item1", "item2"]

            @classmethod
            def get_cli_commands(cls) -> list[CLICommand]:
                return [
                    CLICommand(
                        name="list-items",
                        help="List all items",
                        callback=cls._cli_list_items,
                    ),
                ]

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        commands = PluginWithCLI.get_cli_commands()

        assert len(commands) == 1
        assert commands[0].name == "list-items"
        assert commands[0].callback() == ["item1", "item2"]


class TestDiscoveryRegistry:
    """Tests for the discovery registry module."""

    def test_get_discoverable_plugins_returns_dict(self) -> None:
        """Verify get_discoverable_plugins returns a dictionary."""
        from opencuff.plugins.discovery_registry import get_discoverable_plugins

        plugins = get_discoverable_plugins()

        assert isinstance(plugins, dict)

    def test_get_discoverable_plugins_contains_builtin_plugins(self) -> None:
        """Verify registry contains the built-in plugins."""
        from opencuff.plugins.discovery_registry import get_discoverable_plugins

        plugins = get_discoverable_plugins()

        # Should contain the makefile plugin at minimum
        assert "makefile" in plugins

    def test_get_module_paths_returns_dict(self) -> None:
        """Verify get_module_paths returns a dictionary."""
        from opencuff.plugins.discovery_registry import get_module_paths

        paths = get_module_paths()

        assert isinstance(paths, dict)

    def test_get_module_paths_matches_plugins(self) -> None:
        """Verify module paths correspond to discoverable plugins."""
        from opencuff.plugins.discovery_registry import (
            get_discoverable_plugins,
            get_module_paths,
        )

        plugins = get_discoverable_plugins()
        paths = get_module_paths()

        # Every plugin should have a module path
        for name in plugins:
            assert name in paths
            assert isinstance(paths[name], str)

    def test_register_plugin_adds_to_registry(self) -> None:
        """Verify register_plugin adds a new plugin to the registry."""
        from opencuff.plugins.discovery_registry import (
            get_discoverable_plugins,
            get_module_paths,
            register_plugin,
        )

        class TestPlugin(InSourcePlugin):
            """A test plugin for registration."""

            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolResult:
                return ToolResult(success=True)

        # Register the test plugin
        register_plugin(
            name="test_registration",
            plugin_cls=TestPlugin,
            module_path="tests.test_discovery.TestPlugin",
        )

        plugins = get_discoverable_plugins()
        paths = get_module_paths()

        assert "test_registration" in plugins
        assert plugins["test_registration"] is TestPlugin
        assert paths["test_registration"] == "tests.test_discovery.TestPlugin"

    def test_get_module_paths_returns_copy(self) -> None:
        """Verify get_module_paths returns a copy, not the original dict."""
        from opencuff.plugins.discovery_registry import get_module_paths

        paths1 = get_module_paths()
        paths2 = get_module_paths()

        # Should be equal but not the same object
        assert paths1 == paths2
        assert paths1 is not paths2
