"""Tests for the plugin base module.

Tests cover:
    - PluginState enum transitions
    - ToolDefinition and ToolResult dataclasses
    - InSourcePlugin lifecycle hooks
    - PluginProtocol ABC requirements
"""

import pytest

from opencuff.plugins.base import (
    InSourcePlugin,
    PluginState,
    ToolDefinition,
    ToolResult,
)


class TestPluginState:
    """Tests for PluginState enum."""

    def test_all_states_defined(self) -> None:
        """Verify all required plugin states are defined."""
        assert PluginState.UNLOADED is not None
        assert PluginState.INITIALIZING is not None
        assert PluginState.ACTIVE is not None
        assert PluginState.ERROR is not None
        assert PluginState.RECOVERING is not None

    def test_state_values_are_strings(self) -> None:
        """Verify state values are string-based for serialization."""
        assert PluginState.UNLOADED.value == "unloaded"
        assert PluginState.INITIALIZING.value == "initializing"
        assert PluginState.ACTIVE.value == "active"
        assert PluginState.ERROR.value == "error"
        assert PluginState.RECOVERING.value == "recovering"


class TestToolDefinition:
    """Tests for ToolDefinition dataclass."""

    def test_create_tool_definition(self) -> None:
        """Verify tool definition can be created with all required fields."""
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            returns={"type": "string"},
        )

        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.parameters == {"type": "object", "properties": {}}
        assert tool.returns == {"type": "string"}

    def test_tool_definition_equality(self) -> None:
        """Verify two tool definitions with same values are equal."""
        tool1 = ToolDefinition(
            name="echo",
            description="Echo input",
            parameters={"type": "object"},
            returns={"type": "string"},
        )
        tool2 = ToolDefinition(
            name="echo",
            description="Echo input",
            parameters={"type": "object"},
            returns={"type": "string"},
        )

        assert tool1 == tool2


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_create_successful_result(self) -> None:
        """Verify successful tool result creation."""
        result = ToolResult(success=True, data={"output": "hello"})

        assert result.success is True
        assert result.data == {"output": "hello"}
        assert result.error is None

    def test_create_error_result(self) -> None:
        """Verify error tool result creation."""
        result = ToolResult(success=False, error="Something went wrong")

        assert result.success is False
        assert result.data is None
        assert result.error == "Something went wrong"

    def test_default_values(self) -> None:
        """Verify default values for optional fields."""
        result = ToolResult(success=True)

        assert result.data is None
        assert result.error is None


class TestInSourcePlugin:
    """Tests for InSourcePlugin base class."""

    def test_config_stored(self) -> None:
        """Verify configuration is stored on initialization."""

        class SimplePlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                return ToolResult(success=True)

        config = {"key": "value", "nested": {"a": 1}}
        plugin = SimplePlugin(config)

        assert plugin.config == config

    @pytest.mark.asyncio
    async def test_initialize_default_does_nothing(self) -> None:
        """Verify default initialize() is a no-op."""

        class SimplePlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                return ToolResult(success=True)

        plugin = SimplePlugin({})
        # Should not raise
        await plugin.initialize()

    @pytest.mark.asyncio
    async def test_shutdown_default_does_nothing(self) -> None:
        """Verify default shutdown() is a no-op."""

        class SimplePlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                return ToolResult(success=True)

        plugin = SimplePlugin({})
        # Should not raise
        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_default_returns_true(self) -> None:
        """Verify default health_check() returns True."""

        class SimplePlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                return ToolResult(success=True)

        plugin = SimplePlugin({})
        assert await plugin.health_check() is True

    @pytest.mark.asyncio
    async def test_on_config_reload_calls_shutdown_and_initialize(self) -> None:
        """Verify on_config_reload() performs shutdown/initialize cycle."""
        call_order: list[str] = []

        class TrackedPlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                return ToolResult(success=True)

            async def initialize(self) -> None:
                call_order.append("initialize")

            async def shutdown(self) -> None:
                call_order.append("shutdown")

        plugin = TrackedPlugin({"old": "config"})
        new_config = {"new": "config"}

        await plugin.on_config_reload(new_config)

        assert call_order == ["shutdown", "initialize"]
        assert plugin.config == new_config

    def test_get_tools_must_be_implemented(self) -> None:
        """Verify get_tools() is abstract and must be implemented."""

        class IncompletePlugin(InSourcePlugin):
            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                return ToolResult(success=True)

        # Should raise TypeError when instantiating without implementing get_tools
        with pytest.raises(TypeError, match="abstract method"):
            IncompletePlugin({})

    def test_call_tool_must_be_implemented(self) -> None:
        """Verify call_tool() is abstract and must be implemented."""

        class IncompletePlugin(InSourcePlugin):
            def get_tools(self) -> list[ToolDefinition]:
                return []

        # Should raise TypeError when instantiating without implementing call_tool
        with pytest.raises(TypeError, match="abstract method"):
            IncompletePlugin({})


class TestInSourcePluginImplementation:
    """Tests for a concrete InSourcePlugin implementation."""

    @pytest.mark.asyncio
    async def test_full_plugin_lifecycle(self) -> None:
        """Test a complete plugin implementation through its lifecycle."""
        lifecycle_events: list[str] = []

        class FullPlugin(InSourcePlugin):
            def __init__(self, config: dict) -> None:
                super().__init__(config)
                self._initialized = False

            async def initialize(self) -> None:
                lifecycle_events.append("init")
                self._initialized = True

            async def shutdown(self) -> None:
                lifecycle_events.append("shutdown")
                self._initialized = False

            def get_tools(self) -> list[ToolDefinition]:
                return [
                    ToolDefinition(
                        name="greet",
                        description="Greet someone",
                        parameters={
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        },
                        returns={"type": "string"},
                    )
                ]

            async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
                if not self._initialized:
                    return ToolResult(success=False, error="Plugin not initialized")

                if tool_name == "greet":
                    name = arguments.get("name", "World")
                    return ToolResult(success=True, data=f"Hello, {name}!")

                return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        # Arrange
        plugin = FullPlugin({"greeting_prefix": "Hello"})

        # Act - Initialize
        await plugin.initialize()
        assert "init" in lifecycle_events

        # Act - Get tools
        tools = plugin.get_tools()
        assert len(tools) == 1
        assert tools[0].name == "greet"

        # Act - Call tool
        result = await plugin.call_tool("greet", {"name": "Alice"})
        assert result.success is True
        assert result.data == "Hello, Alice!"

        # Act - Call unknown tool
        result = await plugin.call_tool("unknown", {})
        assert result.success is False
        assert "Unknown tool" in result.error

        # Act - Shutdown
        await plugin.shutdown()
        assert lifecycle_events == ["init", "shutdown"]

        # Act - Call after shutdown
        result = await plugin.call_tool("greet", {"name": "Bob"})
        assert result.success is False
        assert "not initialized" in result.error
