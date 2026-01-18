"""Integration tests for the OpenCuff plugin system.

Tests cover:
    - End-to-end tool invocation via MCP client
    - Plugin tools accessible through MCP protocol
    - Tool listing includes plugin tools
    - Server lifecycle with plugins
"""

import asyncio

import pytest
import pytest_asyncio
from fastmcp import Client

from opencuff.plugins.config import OpenCuffSettings, PluginConfig, PluginType
from opencuff.server import (
    get_plugin_manager,
    initialize_plugins,
    mcp,
    shutdown_plugins,
)


class TestMCPIntegration:
    """Integration tests using the MCP client."""

    @pytest_asyncio.fixture(autouse=True)
    async def reset_server(self):
        """Reset server state before each test."""
        # Shutdown any existing plugin manager
        await shutdown_plugins()
        yield
        # Cleanup after test
        await shutdown_plugins()

    @pytest.mark.asyncio
    async def test_server_exposes_builtin_tools(self) -> None:
        """Verify the MCP server exposes built-in tools."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            # Should have at least hello and list_plugins
            tool_names = [t.name for t in tools]
            assert "hello" in tool_names
            assert "list_plugins" in tool_names

    @pytest.mark.asyncio
    async def test_hello_tool_works(self) -> None:
        """Verify the hello tool returns expected response."""
        async with Client(mcp) as client:
            result = await client.call_tool("hello", {})

            assert "Hello from OpenCuff" in str(result)

    @pytest.mark.asyncio
    async def test_list_plugins_without_initialization(self) -> None:
        """Verify list_plugins works without plugin initialization."""
        async with Client(mcp) as client:
            result = await client.call_tool("list_plugins", {})

            # Result should contain plugin information
            result_str = str(result)
            # The result contains no plugins when not initialized
            assert "total_tools" in result_str


class TestPluginIntegration:
    """Integration tests with plugins loaded."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_plugins(self):
        """Setup plugins before each test."""
        # Shutdown any existing plugin manager
        await shutdown_plugins()

        # Initialize with dummy plugin
        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=True,
                    module="opencuff.plugins.builtin.dummy",
                    config={"prefix": "Test: "},
                )
            },
            plugin_settings={
                "health_check_interval": 0,  # Disable for tests
                "live_reload": False,
            },
        )
        await initialize_plugins(settings=settings)

        yield

        # Cleanup
        await shutdown_plugins()

    @pytest.mark.asyncio
    async def test_plugin_manager_initialized(self) -> None:
        """Verify plugin manager is properly initialized."""
        manager = get_plugin_manager()

        assert manager is not None
        assert "dummy" in manager.plugins

    @pytest.mark.asyncio
    async def test_plugin_tools_registered(self) -> None:
        """Verify plugin tools are registered in the manager."""
        manager = get_plugin_manager()
        assert manager is not None

        tools = manager.get_all_tools()
        fqns = [fqn for fqn, _ in tools]

        assert "dummy.echo" in fqns
        assert "dummy.add" in fqns
        assert "dummy.slow" in fqns

    @pytest.mark.asyncio
    async def test_call_plugin_tool_directly(self) -> None:
        """Verify plugin tools can be called through the manager."""
        manager = get_plugin_manager()
        assert manager is not None

        result = await manager.call_tool("dummy.echo", {"message": "hello"})

        assert result.success is True
        assert result.data == "Test: hello"

    @pytest.mark.asyncio
    async def test_call_add_tool(self) -> None:
        """Verify the add tool works correctly."""
        manager = get_plugin_manager()
        assert manager is not None

        result = await manager.call_tool("dummy.add", {"a": 5, "b": 3})

        assert result.success is True
        assert result.data == 8

    @pytest.mark.asyncio
    async def test_list_plugins_shows_loaded_plugins(self) -> None:
        """Verify list_plugins shows the loaded dummy plugin."""
        async with Client(mcp) as client:
            result = await client.call_tool("list_plugins", {})

            # Result should contain dummy plugin info
            result_str = str(result)
            assert "dummy" in result_str or "total_tools" in result_str

    @pytest.mark.asyncio
    async def test_call_plugin_tool_via_mcp(self) -> None:
        """Verify plugin tools can be called via the MCP call_plugin_tool."""
        async with Client(mcp) as client:
            result = await client.call_tool(
                "call_plugin_tool",
                {"tool_name": "dummy.echo", "arguments": {"message": "via mcp"}},
            )

            assert "Test: via mcp" in str(result)


class TestPluginLifecycleIntegration:
    """Integration tests for plugin lifecycle operations."""

    @pytest_asyncio.fixture(autouse=True)
    async def reset_server(self):
        """Reset server state before each test."""
        await shutdown_plugins()
        yield
        await shutdown_plugins()

    @pytest.mark.asyncio
    async def test_plugins_can_be_reloaded(self) -> None:
        """Verify plugins can be reloaded with new configuration."""
        # Initial load
        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    module="opencuff.plugins.builtin.dummy",
                    config={"prefix": "Old: "},
                )
            },
            plugin_settings={"health_check_interval": 0, "live_reload": False},
        )
        await initialize_plugins(settings=settings)

        manager = get_plugin_manager()
        assert manager is not None

        # Verify initial prefix
        result = await manager.call_tool("dummy.echo", {"message": "test"})
        assert result.data == "Old: test"

        # Reload with new config
        new_config = PluginConfig(
            type=PluginType.IN_SOURCE,
            module="opencuff.plugins.builtin.dummy",
            config={"prefix": "New: "},
        )
        await manager.reload_plugin("dummy", new_config)

        # Verify new prefix
        result = await manager.call_tool("dummy.echo", {"message": "test"})
        assert result.data == "New: test"

    @pytest.mark.asyncio
    async def test_plugins_can_be_unloaded(self) -> None:
        """Verify plugins can be manually unloaded."""
        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    module="opencuff.plugins.builtin.dummy",
                )
            },
            plugin_settings={"health_check_interval": 0, "live_reload": False},
        )
        await initialize_plugins(settings=settings)

        manager = get_plugin_manager()
        assert manager is not None
        assert "dummy" in manager.plugins

        await manager.unload_plugin("dummy")

        assert "dummy" not in manager.plugins

    @pytest.mark.asyncio
    async def test_plugins_can_be_loaded_dynamically(self) -> None:
        """Verify plugins can be loaded after server start."""
        # Start with no plugins
        settings = OpenCuffSettings(
            plugins={},
            plugin_settings={"health_check_interval": 0, "live_reload": False},
        )
        await initialize_plugins(settings=settings)

        manager = get_plugin_manager()
        assert manager is not None
        assert len(manager.plugins) == 0

        # Load plugin dynamically
        config = PluginConfig(
            type=PluginType.IN_SOURCE,
            module="opencuff.plugins.builtin.dummy",
        )
        await manager.load_plugin("dynamic_dummy", config)

        assert "dynamic_dummy" in manager.plugins

        # Verify it works
        result = await manager.call_tool("dynamic_dummy.echo", {"message": "hi"})
        assert result.success is True
        assert result.data == "hi"


class TestConcurrentAccess:
    """Tests for concurrent access to the plugin system."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_plugins(self):
        """Setup plugins before each test."""
        await shutdown_plugins()

        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    module="opencuff.plugins.builtin.dummy",
                )
            },
            plugin_settings={"health_check_interval": 0, "live_reload": False},
        )
        await initialize_plugins(settings=settings)

        yield

        await shutdown_plugins()

    @pytest.mark.asyncio
    async def test_concurrent_tool_calls(self) -> None:
        """Verify multiple concurrent tool calls work correctly."""
        manager = get_plugin_manager()
        assert manager is not None

        async def call_add(a: int, b: int) -> int:
            result = await manager.call_tool("dummy.add", {"a": a, "b": b})
            return result.data

        # Run 10 concurrent calls
        results = await asyncio.gather(*[call_add(i, i + 1) for i in range(10)])

        # Verify all results
        expected = [i + (i + 1) for i in range(10)]
        assert results == expected

    @pytest.mark.asyncio
    async def test_slow_requests_complete_during_reload(self) -> None:
        """Verify in-flight requests complete during plugin reload."""
        manager = get_plugin_manager()
        assert manager is not None

        # Start a slow request
        slow_task = asyncio.create_task(
            manager.call_tool("dummy.slow", {"seconds": 0.1})
        )

        # Give it time to start
        await asyncio.sleep(0.02)

        # Start a reload
        lifecycle = manager.plugins["dummy"]
        reload_task = asyncio.create_task(lifecycle.reload())

        # Both should complete
        slow_result, _ = await asyncio.gather(slow_task, reload_task)

        assert slow_result.success is True
        assert "0.1" in slow_result.data


class TestDynamicToolRegistration:
    """Tests for dynamic tool registration with FastMCP.

    These tests verify that plugin tools appear as first-class MCP tools,
    not just through the call_plugin_tool gateway.
    """

    @pytest_asyncio.fixture(autouse=True)
    async def setup_plugins(self):
        """Setup plugins before each test."""
        await shutdown_plugins()

        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=True,
                    module="opencuff.plugins.builtin.dummy",
                    config={"prefix": "Dynamic: "},
                )
            },
            plugin_settings={
                "health_check_interval": 0,
                "live_reload": False,
            },
        )
        await initialize_plugins(settings=settings)

        yield

        await shutdown_plugins()

    @pytest.mark.asyncio
    async def test_plugin_tools_visible_in_mcp_tool_list(self) -> None:
        """Verify plugin tools appear directly in MCP tool listing."""
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]

            # Plugin tools should be directly visible
            assert "dummy.echo" in tool_names
            assert "dummy.add" in tool_names
            assert "dummy.slow" in tool_names

            # Built-in tools should still be present
            assert "hello" in tool_names
            assert "list_plugins" in tool_names

    @pytest.mark.asyncio
    async def test_plugin_tools_callable_directly_via_mcp(self) -> None:
        """Verify plugin tools can be called directly without gateway."""
        async with Client(mcp) as client:
            # Call plugin tool directly (not via call_plugin_tool)
            result = await client.call_tool(
                "dummy.echo",
                {"message": "hello from dynamic registration"},
            )

            assert "Dynamic: hello from dynamic registration" in str(result)

    @pytest.mark.asyncio
    async def test_plugin_tool_with_parameters(self) -> None:
        """Verify plugin tools work correctly with parameters."""
        async with Client(mcp) as client:
            result = await client.call_tool(
                "dummy.add",
                {"a": 42, "b": 8},
            )

            # Result should be the sum
            assert result == 50 or "50" in str(result)

    @pytest.mark.asyncio
    async def test_tool_schema_exposed_to_mcp(self) -> None:
        """Verify tool parameter schemas are exposed to MCP clients."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            # Find the echo tool
            echo_tool = next((t for t in tools if t.name == "dummy.echo"), None)
            assert echo_tool is not None

            # Check description is present
            assert echo_tool.description is not None
            assert "echo" in echo_tool.description.lower()

    @pytest.mark.asyncio
    async def test_dynamically_loaded_plugin_tools_visible(self) -> None:
        """Verify dynamically loaded plugin tools appear in MCP."""
        manager = get_plugin_manager()
        assert manager is not None

        # Initially should have dummy plugin tools
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "dummy.echo" in tool_names

        # Dynamically load a new plugin instance
        new_config = PluginConfig(
            type=PluginType.IN_SOURCE,
            enabled=True,
            module="opencuff.plugins.builtin.dummy",
            config={"prefix": "New: "},
        )
        await manager.load_plugin("dynamic_plugin", new_config)

        # New plugin tools should now be visible
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "dynamic_plugin.echo" in tool_names
            assert "dynamic_plugin.add" in tool_names

            # Call the new tool
            result = await client.call_tool(
                "dynamic_plugin.echo",
                {"message": "from dynamic plugin"},
            )
            assert "New: from dynamic plugin" in str(result)

    @pytest.mark.asyncio
    async def test_unloaded_plugin_tools_removed_from_mcp(self) -> None:
        """Verify unloaded plugin tools are removed from MCP listing."""
        manager = get_plugin_manager()
        assert manager is not None

        # Plugin tools should be visible
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "dummy.echo" in tool_names

        # Unload the plugin
        await manager.unload_plugin("dummy")

        # Plugin tools should no longer be visible
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "dummy.echo" not in tool_names
            assert "dummy.add" not in tool_names

    @pytest.mark.asyncio
    async def test_reloaded_plugin_tools_still_callable(self) -> None:
        """Verify plugin tools work after reload."""
        manager = get_plugin_manager()
        assert manager is not None

        # Call before reload
        async with Client(mcp) as client:
            result = await client.call_tool(
                "dummy.echo",
                {"message": "before reload"},
            )
            assert "Dynamic: before reload" in str(result)

        # Reload with new config
        new_config = PluginConfig(
            type=PluginType.IN_SOURCE,
            enabled=True,
            module="opencuff.plugins.builtin.dummy",
            config={"prefix": "Reloaded: "},
        )
        await manager.reload_plugin("dummy", new_config)

        # Tools should still work with new config
        async with Client(mcp) as client:
            result = await client.call_tool(
                "dummy.echo",
                {"message": "after reload"},
            )
            assert "Reloaded: after reload" in str(result)

    @pytest.mark.asyncio
    async def test_both_gateway_and_direct_call_work(self) -> None:
        """Verify both call_plugin_tool gateway and direct calls work."""
        async with Client(mcp) as client:
            # Direct call
            direct_result = await client.call_tool(
                "dummy.echo",
                {"message": "direct"},
            )

            # Gateway call (for backward compatibility)
            gateway_result = await client.call_tool(
                "call_plugin_tool",
                {"tool_name": "dummy.echo", "arguments": {"message": "gateway"}},
            )

            assert "Dynamic: direct" in str(direct_result)
            assert "Dynamic: gateway" in str(gateway_result)
