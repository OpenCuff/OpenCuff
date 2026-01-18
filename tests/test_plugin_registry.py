"""Tests for the tool registry module.

Tests cover:
    - Tool registration with namespacing
    - Duplicate tool detection
    - Plugin unregistration
    - Thread-safe concurrent access
    - Tool lookup by fully qualified name
"""

import asyncio

import pytest

from opencuff.plugins.base import ToolDefinition
from opencuff.plugins.registry import ToolRegistry


class TestToolRegistry:
    """Tests for ToolRegistry class."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create a fresh registry for each test."""
        return ToolRegistry()

    @pytest.fixture
    def sample_tools(self) -> list[ToolDefinition]:
        """Sample tools for testing."""
        return [
            ToolDefinition(
                name="echo",
                description="Echo input",
                parameters={"type": "object"},
                returns={"type": "string"},
            ),
            ToolDefinition(
                name="add",
                description="Add numbers",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                },
                returns={"type": "integer"},
            ),
        ]


class TestToolRegistration(TestToolRegistry):
    """Tests for tool registration."""

    @pytest.mark.asyncio
    async def test_register_tools_creates_namespaced_names(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify tools are registered with namespace prefix."""
        await registry.register_tools("my_plugin", sample_tools)

        # Check fully qualified names
        assert registry.get_tool("my_plugin.echo") is not None
        assert registry.get_tool("my_plugin.add") is not None

    @pytest.mark.asyncio
    async def test_register_tools_preserves_tool_definition(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify tool definitions are preserved after registration."""
        await registry.register_tools("test", sample_tools)

        result = registry.get_tool("test.echo")
        assert result is not None

        plugin_name, tool = result
        assert plugin_name == "test"
        assert tool.name == "echo"
        assert tool.description == "Echo input"

    @pytest.mark.asyncio
    async def test_register_multiple_plugins(self, registry: ToolRegistry) -> None:
        """Verify multiple plugins can register tools."""
        tools_a = [
            ToolDefinition(
                name="tool1", description="Tool 1", parameters={}, returns={}
            )
        ]
        tools_b = [
            ToolDefinition(
                name="tool2", description="Tool 2", parameters={}, returns={}
            )
        ]

        await registry.register_tools("plugin_a", tools_a)
        await registry.register_tools("plugin_b", tools_b)

        assert registry.get_tool("plugin_a.tool1") is not None
        assert registry.get_tool("plugin_b.tool2") is not None

    @pytest.mark.asyncio
    async def test_same_tool_name_different_plugins_allowed(
        self, registry: ToolRegistry
    ) -> None:
        """Verify same tool name in different plugins works."""
        tools_a = [
            ToolDefinition(
                name="status", description="Status A", parameters={}, returns={}
            )
        ]
        tools_b = [
            ToolDefinition(
                name="status", description="Status B", parameters={}, returns={}
            )
        ]

        await registry.register_tools("git", tools_a)
        await registry.register_tools("docker", tools_b)

        git_status = registry.get_tool("git.status")
        docker_status = registry.get_tool("docker.status")

        assert git_status is not None
        assert docker_status is not None
        assert git_status[1].description == "Status A"
        assert docker_status[1].description == "Status B"


class TestDuplicateDetection(TestToolRegistry):
    """Tests for duplicate tool detection."""

    @pytest.mark.asyncio
    async def test_duplicate_tool_within_plugin_raises_error(
        self, registry: ToolRegistry
    ) -> None:
        """Verify duplicate tool name within same plugin raises error."""
        tools = [
            ToolDefinition(name="dupe", description="First", parameters={}, returns={}),
            ToolDefinition(
                name="dupe", description="Second", parameters={}, returns={}
            ),
        ]

        from opencuff.plugins.errors import PluginError

        with pytest.raises(PluginError) as exc_info:
            await registry.register_tools("my_plugin", tools)

        assert "Duplicate tool name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_re_register_same_plugin_raises_error(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify re-registering same plugin without unregistering raises error."""
        await registry.register_tools("plugin", sample_tools)

        from opencuff.plugins.errors import PluginError

        with pytest.raises(PluginError) as exc_info:
            await registry.register_tools("plugin", sample_tools)

        assert "Duplicate tool name" in str(exc_info.value)


class TestPluginUnregistration(TestToolRegistry):
    """Tests for plugin unregistration."""

    @pytest.mark.asyncio
    async def test_unregister_removes_all_plugin_tools(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify unregistering a plugin removes all its tools."""
        await registry.register_tools("plugin", sample_tools)

        # Verify tools exist
        assert registry.get_tool("plugin.echo") is not None
        assert registry.get_tool("plugin.add") is not None

        # Unregister
        await registry.unregister_plugin("plugin")

        # Verify tools are gone
        assert registry.get_tool("plugin.echo") is None
        assert registry.get_tool("plugin.add") is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_plugin_is_safe(
        self, registry: ToolRegistry
    ) -> None:
        """Verify unregistering nonexistent plugin doesn't raise."""
        # Should not raise
        await registry.unregister_plugin("nonexistent")

    @pytest.mark.asyncio
    async def test_unregister_only_affects_target_plugin(
        self, registry: ToolRegistry
    ) -> None:
        """Verify unregistering one plugin doesn't affect others."""
        tools_a = [
            ToolDefinition(name="tool", description="A", parameters={}, returns={})
        ]
        tools_b = [
            ToolDefinition(name="tool", description="B", parameters={}, returns={})
        ]

        await registry.register_tools("plugin_a", tools_a)
        await registry.register_tools("plugin_b", tools_b)

        await registry.unregister_plugin("plugin_a")

        assert registry.get_tool("plugin_a.tool") is None
        assert registry.get_tool("plugin_b.tool") is not None


class TestToolLookup(TestToolRegistry):
    """Tests for tool lookup operations."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_tool_returns_none(
        self, registry: ToolRegistry
    ) -> None:
        """Verify looking up nonexistent tool returns None."""
        assert registry.get_tool("nonexistent.tool") is None

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_registered(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify list_tools returns all registered tools."""
        await registry.register_tools("plugin", sample_tools)

        tools = registry.list_tools()

        assert len(tools) == 2
        fqns = [fqn for fqn, _ in tools]
        assert "plugin.echo" in fqns
        assert "plugin.add" in fqns

    @pytest.mark.asyncio
    async def test_list_tools_empty_registry(self, registry: ToolRegistry) -> None:
        """Verify list_tools returns empty list for empty registry."""
        tools = registry.list_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_get_plugin_name_from_tool(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify plugin name can be retrieved from tool lookup."""
        await registry.register_tools("my_plugin", sample_tools)

        result = registry.get_tool("my_plugin.echo")
        assert result is not None

        plugin_name, _ = result
        assert plugin_name == "my_plugin"


class TestConcurrentAccess(TestToolRegistry):
    """Tests for thread-safe concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_registration(self, registry: ToolRegistry) -> None:
        """Verify concurrent tool registration is safe."""

        async def register_plugin(index: int) -> None:
            tools = [
                ToolDefinition(
                    name="tool",
                    description=f"Tool {index}",
                    parameters={},
                    returns={},
                )
            ]
            await registry.register_tools(f"plugin_{index}", tools)

        # Register 10 plugins concurrently
        await asyncio.gather(*[register_plugin(i) for i in range(10)])

        # Verify all registered
        tools = registry.list_tools()
        assert len(tools) == 10

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self, registry: ToolRegistry) -> None:
        """Verify concurrent reads and writes are safe."""
        # Pre-populate with some tools
        initial_tools = [
            ToolDefinition(
                name="initial", description="Initial", parameters={}, returns={}
            )
        ]
        await registry.register_tools("initial", initial_tools)

        read_count = 0
        write_count = 0

        async def reader() -> None:
            nonlocal read_count
            for _ in range(100):
                registry.get_tool("initial.initial")
                registry.list_tools()
                read_count += 1
                await asyncio.sleep(0)

        async def writer(index: int) -> None:
            nonlocal write_count
            tools = [
                ToolDefinition(
                    name="tool",
                    description=f"Tool {index}",
                    parameters={},
                    returns={},
                )
            ]
            await registry.register_tools(f"writer_{index}", tools)
            write_count += 1

        # Run readers and writers concurrently
        await asyncio.gather(
            reader(),
            reader(),
            *[writer(i) for i in range(5)],
        )

        assert read_count == 200  # 2 readers * 100 iterations
        assert write_count == 5


class TestMakeFqn:
    """Tests for fully qualified name generation."""

    def test_make_fqn_format(self) -> None:
        """Verify FQN format is plugin_name.tool_name."""
        registry = ToolRegistry()
        fqn = registry._make_fqn("my_plugin", "my_tool")
        assert fqn == "my_plugin.my_tool"

    def test_make_fqn_with_special_characters(self) -> None:
        """Verify FQN handles underscores correctly."""
        registry = ToolRegistry()
        fqn = registry._make_fqn("plugin_with_underscore", "tool_name")
        assert fqn == "plugin_with_underscore.tool_name"


class TestRegistryCallbacks(TestToolRegistry):
    """Tests for callback support in ToolRegistry."""

    @pytest.mark.asyncio
    async def test_on_registered_callback_called_on_registration(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify on_registered callback is called after tool registration."""
        callback_called = False
        received_plugin = None
        received_tools = None

        async def on_registered(plugin_name: str, tools: list[ToolDefinition]) -> None:
            nonlocal callback_called, received_plugin, received_tools
            callback_called = True
            received_plugin = plugin_name
            received_tools = tools

        registry.set_callbacks(on_registered=on_registered)
        await registry.register_tools("my_plugin", sample_tools)

        assert callback_called is True
        assert received_plugin == "my_plugin"
        assert received_tools == sample_tools

    @pytest.mark.asyncio
    async def test_on_unregistered_callback_called_on_unregistration(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify on_unregistered callback is called after plugin unregistration."""
        callback_called = False
        received_plugin = None

        async def on_unregistered(plugin_name: str) -> None:
            nonlocal callback_called, received_plugin
            callback_called = True
            received_plugin = plugin_name

        await registry.register_tools("my_plugin", sample_tools)

        registry.set_callbacks(on_unregistered=on_unregistered)
        await registry.unregister_plugin("my_plugin")

        assert callback_called is True
        assert received_plugin == "my_plugin"

    @pytest.mark.asyncio
    async def test_callbacks_not_called_when_not_set(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify no errors when callbacks are not set."""
        # Should not raise
        await registry.register_tools("my_plugin", sample_tools)
        await registry.unregister_plugin("my_plugin")

    @pytest.mark.asyncio
    async def test_callbacks_can_be_cleared(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify callbacks can be cleared by setting to None."""
        callback_count = 0

        async def on_registered(plugin_name: str, tools: list[ToolDefinition]) -> None:
            nonlocal callback_count
            callback_count += 1

        registry.set_callbacks(on_registered=on_registered)
        await registry.register_tools("plugin1", sample_tools[:1])

        # Clear callback
        registry.set_callbacks(on_registered=None)
        await registry.register_tools("plugin2", sample_tools[1:])

        # Should have been called only once
        assert callback_count == 1

    @pytest.mark.asyncio
    async def test_on_unregistered_not_called_for_nonexistent_plugin(
        self, registry: ToolRegistry
    ) -> None:
        """Verify on_unregistered not called for nonexistent plugin."""
        callback_called = False

        async def on_unregistered(plugin_name: str) -> None:
            nonlocal callback_called
            callback_called = True

        registry.set_callbacks(on_unregistered=on_unregistered)
        await registry.unregister_plugin("nonexistent")

        # Callback should not be called for nonexistent plugin
        assert callback_called is False

    @pytest.mark.asyncio
    async def test_callback_error_does_not_affect_registration(
        self, registry: ToolRegistry, sample_tools: list[ToolDefinition]
    ) -> None:
        """Verify callback errors don't affect successful registration."""

        async def on_registered(plugin_name: str, tools: list[ToolDefinition]) -> None:
            raise RuntimeError("Callback error")

        registry.set_callbacks(on_registered=on_registered)

        # Should not raise, registration should succeed
        await registry.register_tools("my_plugin", sample_tools)

        # Tools should still be registered
        assert registry.get_tool("my_plugin.echo") is not None
