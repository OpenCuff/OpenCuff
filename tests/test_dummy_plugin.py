"""Tests for the dummy plugin.

Tests cover:
    - Plugin initialization and shutdown
    - All tool implementations (echo, add, slow)
    - Error handling
"""

import asyncio

import pytest

from opencuff.plugins.builtin.dummy import Plugin


class TestDummyPluginLifecycle:
    """Tests for dummy plugin lifecycle."""

    @pytest.mark.asyncio
    async def test_initialize_sets_state(self) -> None:
        """Verify initialize sets the plugin to initialized state."""
        plugin = Plugin({})

        await plugin.initialize()

        assert await plugin.health_check() is True

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self) -> None:
        """Verify shutdown clears the initialized state."""
        plugin = Plugin({})
        await plugin.initialize()

        await plugin.shutdown()

        assert await plugin.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_before_init_returns_false(self) -> None:
        """Verify health check returns False before initialization."""
        plugin = Plugin({})

        assert await plugin.health_check() is False


class TestDummyPluginTools:
    """Tests for dummy plugin tools."""

    @pytest.mark.asyncio
    async def test_get_tools_returns_three_tools(self) -> None:
        """Verify get_tools returns all three tools."""
        plugin = Plugin({})
        await plugin.initialize()

        tools = plugin.get_tools()

        assert len(tools) == 3
        tool_names = [t.name for t in tools]
        assert "echo" in tool_names
        assert "add" in tool_names
        assert "slow" in tool_names

    @pytest.mark.asyncio
    async def test_tool_definitions_have_required_fields(self) -> None:
        """Verify all tools have proper definitions."""
        plugin = Plugin({})
        await plugin.initialize()

        tools = plugin.get_tools()

        for tool in tools:
            assert tool.name
            assert tool.description
            assert "type" in tool.parameters
            assert "properties" in tool.parameters


class TestEchoTool:
    """Tests for the echo tool."""

    @pytest.mark.asyncio
    async def test_echo_returns_message(self) -> None:
        """Verify echo returns the input message."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("echo", {"message": "hello"})

        assert result.success is True
        assert result.data == "hello"

    @pytest.mark.asyncio
    async def test_echo_with_prefix(self) -> None:
        """Verify echo applies configured prefix."""
        plugin = Plugin({"prefix": "Echo: "})
        await plugin.initialize()

        result = await plugin.call_tool("echo", {"message": "world"})

        assert result.success is True
        assert result.data == "Echo: world"

    @pytest.mark.asyncio
    async def test_echo_empty_message(self) -> None:
        """Verify echo handles empty message."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("echo", {"message": ""})

        assert result.success is True
        assert result.data == ""

    @pytest.mark.asyncio
    async def test_echo_missing_message(self) -> None:
        """Verify echo handles missing message argument."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("echo", {})

        assert result.success is True
        assert result.data == ""


class TestAddTool:
    """Tests for the add tool."""

    @pytest.mark.asyncio
    async def test_add_positive_numbers(self) -> None:
        """Verify add works with positive numbers."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("add", {"a": 2, "b": 3})

        assert result.success is True
        assert result.data == 5

    @pytest.mark.asyncio
    async def test_add_negative_numbers(self) -> None:
        """Verify add works with negative numbers."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("add", {"a": -5, "b": 3})

        assert result.success is True
        assert result.data == -2

    @pytest.mark.asyncio
    async def test_add_zero(self) -> None:
        """Verify add works with zero."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("add", {"a": 0, "b": 0})

        assert result.success is True
        assert result.data == 0

    @pytest.mark.asyncio
    async def test_add_missing_arguments(self) -> None:
        """Verify add uses defaults for missing arguments."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("add", {})

        assert result.success is True
        assert result.data == 0

    @pytest.mark.asyncio
    async def test_add_invalid_arguments(self) -> None:
        """Verify add handles invalid arguments."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("add", {"a": "not a number", "b": 1})

        assert result.success is False
        assert "Invalid arguments" in result.error


class TestSlowTool:
    """Tests for the slow tool."""

    @pytest.mark.asyncio
    async def test_slow_returns_after_delay(self) -> None:
        """Verify slow sleeps and returns."""
        plugin = Plugin({})
        await plugin.initialize()

        start = asyncio.get_event_loop().time()
        result = await plugin.call_tool("slow", {"seconds": 0.1})
        elapsed = asyncio.get_event_loop().time() - start

        assert result.success is True
        assert "0.1" in result.data
        assert elapsed >= 0.1

    @pytest.mark.asyncio
    async def test_slow_zero_seconds(self) -> None:
        """Verify slow handles zero seconds."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("slow", {"seconds": 0})

        assert result.success is True

    @pytest.mark.asyncio
    async def test_slow_negative_seconds(self) -> None:
        """Verify slow rejects negative duration."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("slow", {"seconds": -1})

        assert result.success is False
        assert "non-negative" in result.error

    @pytest.mark.asyncio
    async def test_slow_invalid_argument(self) -> None:
        """Verify slow handles invalid argument."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("slow", {"seconds": "not a number"})

        assert result.success is False
        assert "Invalid arguments" in result.error


class TestUnknownTool:
    """Tests for handling unknown tools."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Verify unknown tool returns error."""
        plugin = Plugin({})
        await plugin.initialize()

        result = await plugin.call_tool("nonexistent", {})

        assert result.success is False
        assert "Unknown tool" in result.error


class TestUninitializedPlugin:
    """Tests for calling tools on uninitialized plugin."""

    @pytest.mark.asyncio
    async def test_call_before_init_returns_error(self) -> None:
        """Verify calling tool before init returns error."""
        plugin = Plugin({})

        result = await plugin.call_tool("echo", {"message": "test"})

        assert result.success is False
        assert "not initialized" in result.error
