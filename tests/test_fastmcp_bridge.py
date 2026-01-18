"""Tests for the FastMCP bridge module.

Tests cover:
    - Tool registration with FastMCP
    - Tool unregistration from FastMCP
    - Tool wrapper function creation and invocation
    - Full synchronization of tools
    - Error handling during registration
    - Duplicate tool handling
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from opencuff.plugins.base import ToolDefinition, ToolResult
from opencuff.plugins.registry import ToolRegistry


class TestFastMCPBridgeUnit:
    """Unit tests for FastMCPBridge class."""

    @pytest.fixture
    def mock_mcp(self) -> MagicMock:
        """Create a mock FastMCP instance."""
        mcp = MagicMock()
        mcp.add_tool = MagicMock()
        mcp.remove_tool = MagicMock()
        return mcp

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create a fresh ToolRegistry."""
        return ToolRegistry()

    @pytest.fixture
    def mock_call_handler(self) -> AsyncMock:
        """Create a mock call handler that returns success."""

        async def handler(fqn: str, args: dict[str, Any]) -> ToolResult:
            return ToolResult(success=True, data={"fqn": fqn, "args": args})

        return AsyncMock(side_effect=handler)

    @pytest.fixture
    def sample_tools(self) -> list[ToolDefinition]:
        """Sample tools for testing."""
        return [
            ToolDefinition(
                name="echo",
                description="Echo the input message",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
            ),
            ToolDefinition(
                name="add",
                description="Add two numbers",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
            ),
        ]


class TestToolSynchronization(TestFastMCPBridgeUnit):
    """Tests for tool synchronization with FastMCP."""

    @pytest.mark.asyncio
    async def test_sync_tools_registers_with_fastmcp(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify sync_tools registers tools with FastMCP."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        await bridge.sync_tools("dummy", sample_tools)

        # Should have called add_tool twice (once per tool)
        assert mock_mcp.add_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_tools_creates_correct_tool_names(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify tools are registered with fully qualified names."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        await bridge.sync_tools("dummy", sample_tools)

        # Check the tool names passed to add_tool
        registered_names = [
            call.args[0].name for call in mock_mcp.add_tool.call_args_list
        ]
        assert "dummy.echo" in registered_names
        assert "dummy.add" in registered_names

    @pytest.mark.asyncio
    async def test_sync_tools_preserves_description(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify tool descriptions are preserved."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        tools = [
            ToolDefinition(
                name="test",
                description="This is a test description",
            ),
        ]

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)
        await bridge.sync_tools("plugin", tools)

        # Get the registered tool and check description
        registered_tool = mock_mcp.add_tool.call_args_list[0].args[0]
        assert registered_tool.description == "This is a test description"

    @pytest.mark.asyncio
    async def test_sync_tools_tracks_registered_tools(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify bridge tracks which tools are registered."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        await bridge.sync_tools("dummy", sample_tools)

        assert "dummy.echo" in bridge.registered_tools
        assert "dummy.add" in bridge.registered_tools


class TestToolUnregistration(TestFastMCPBridgeUnit):
    """Tests for tool unregistration from FastMCP."""

    @pytest.mark.asyncio
    async def test_remove_plugin_tools_calls_fastmcp_remove(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify remove_plugin_tools calls FastMCP's remove_tool."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        # First register tools
        await bridge.sync_tools("dummy", sample_tools)

        # Then remove them
        await bridge.remove_plugin_tools("dummy")

        # Should have called remove_tool twice
        assert mock_mcp.remove_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_remove_plugin_tools_removes_from_tracking(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify tools are removed from tracking after unregistration."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        await bridge.sync_tools("dummy", sample_tools)
        assert len(bridge.registered_tools) == 2

        await bridge.remove_plugin_tools("dummy")
        assert len(bridge.registered_tools) == 0

    @pytest.mark.asyncio
    async def test_remove_plugin_tools_only_affects_target_plugin(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify removing one plugin's tools doesn't affect others."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        tools_a = [ToolDefinition(name="tool", description="A")]
        tools_b = [ToolDefinition(name="tool", description="B")]

        await bridge.sync_tools("plugin_a", tools_a)
        await bridge.sync_tools("plugin_b", tools_b)

        await bridge.remove_plugin_tools("plugin_a")

        assert "plugin_a.tool" not in bridge.registered_tools
        assert "plugin_b.tool" in bridge.registered_tools

    @pytest.mark.asyncio
    async def test_remove_nonexistent_plugin_is_safe(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify removing tools for nonexistent plugin doesn't raise."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        # Should not raise
        await bridge.remove_plugin_tools("nonexistent")

        assert mock_mcp.remove_tool.call_count == 0


class TestDuplicateHandling(TestFastMCPBridgeUnit):
    """Tests for duplicate tool handling."""

    @pytest.mark.asyncio
    async def test_duplicate_registration_skipped(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify duplicate tools are not re-registered."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        tools = [ToolDefinition(name="echo", description="Echo")]
        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        await bridge.sync_tools("dummy", tools)
        await bridge.sync_tools("dummy", tools)

        # Should only be called once
        assert mock_mcp.add_tool.call_count == 1


class TestToolWrapperInvocation(TestFastMCPBridgeUnit):
    """Tests for tool wrapper function invocation."""

    @pytest.mark.asyncio
    async def test_wrapper_calls_handler_with_correct_fqn(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
    ) -> None:
        """Verify wrapper function calls handler with correct FQN."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        call_handler = AsyncMock(return_value=ToolResult(success=True, data="result"))
        tools = [ToolDefinition(name="echo", description="Echo")]

        bridge = FastMCPBridge(mock_mcp, registry, call_handler)
        await bridge.sync_tools("dummy", tools)

        # Get the registered tool and call its function
        registered_tool = mock_mcp.add_tool.call_args_list[0].args[0]
        await registered_tool.fn(message="hello")

        call_handler.assert_called_once_with("dummy.echo", {"message": "hello"})

    @pytest.mark.asyncio
    async def test_wrapper_returns_data_on_success(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
    ) -> None:
        """Verify wrapper returns data when handler succeeds."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        call_handler = AsyncMock(
            return_value=ToolResult(success=True, data="echoed: hello")
        )
        tools = [ToolDefinition(name="echo", description="Echo")]

        bridge = FastMCPBridge(mock_mcp, registry, call_handler)
        await bridge.sync_tools("dummy", tools)

        registered_tool = mock_mcp.add_tool.call_args_list[0].args[0]
        result = await registered_tool.fn(message="hello")

        assert result == "echoed: hello"

    @pytest.mark.asyncio
    async def test_wrapper_raises_on_failure(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
    ) -> None:
        """Verify wrapper raises RuntimeError when handler fails."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        call_handler = AsyncMock(
            return_value=ToolResult(success=False, error="Tool failed")
        )
        tools = [ToolDefinition(name="echo", description="Echo")]

        bridge = FastMCPBridge(mock_mcp, registry, call_handler)
        await bridge.sync_tools("dummy", tools)

        registered_tool = mock_mcp.add_tool.call_args_list[0].args[0]

        with pytest.raises(RuntimeError) as exc_info:
            await registered_tool.fn(message="hello")

        assert "Tool failed" in str(exc_info.value)


class TestFullSync(TestFastMCPBridgeUnit):
    """Tests for full synchronization."""

    @pytest.mark.asyncio
    async def test_full_sync_registers_all_registry_tools(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify full_sync registers all tools from the registry."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        # Pre-populate the registry
        await registry.register_tools("dummy", sample_tools)

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)
        await bridge.full_sync()

        assert mock_mcp.add_tool.call_count == 2
        assert "dummy.echo" in bridge.registered_tools
        assert "dummy.add" in bridge.registered_tools

    @pytest.mark.asyncio
    async def test_full_sync_removes_stale_tools(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
        sample_tools: list[ToolDefinition],
    ) -> None:
        """Verify full_sync removes tools no longer in registry."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        # Register tools in both registry and bridge
        await registry.register_tools("dummy", sample_tools)
        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)
        await bridge.sync_tools("dummy", sample_tools)

        # Unregister from registry but not bridge
        await registry.unregister_plugin("dummy")

        # Full sync should detect the stale tools
        await bridge.full_sync()

        # Bridge should have removed them
        assert len(bridge.registered_tools) == 0


class TestErrorHandling(TestFastMCPBridgeUnit):
    """Tests for error handling during registration."""

    @pytest.mark.asyncio
    async def test_registration_error_isolated_per_tool(
        self,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify registration error for one tool doesn't affect others."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        mock_mcp = MagicMock()
        call_count = 0

        def add_tool_side_effect(tool):
            nonlocal call_count
            call_count += 1
            if tool.name == "dummy.failing":
                raise RuntimeError("Registration failed")

        mock_mcp.add_tool = MagicMock(side_effect=add_tool_side_effect)
        mock_mcp.remove_tool = MagicMock()

        tools = [
            ToolDefinition(name="good", description="Good tool"),
            ToolDefinition(name="failing", description="Failing tool"),
            ToolDefinition(name="another_good", description="Another good tool"),
        ]

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        # Should not raise, but some tools may fail
        await bridge.sync_tools("dummy", tools)

        # At least some tools should have been attempted
        assert call_count >= 2


class TestConcurrency(TestFastMCPBridgeUnit):
    """Tests for concurrent access safety."""

    @pytest.mark.asyncio
    async def test_concurrent_sync_is_safe(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify concurrent sync operations are thread-safe."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        async def sync_plugin(index: int) -> None:
            tools = [
                ToolDefinition(
                    name=f"tool_{j}",
                    description=f"Tool {j} from plugin {index}",
                )
                for j in range(3)
            ]
            await bridge.sync_tools(f"plugin_{index}", tools)

        # Run 5 concurrent syncs
        await asyncio.gather(*[sync_plugin(i) for i in range(5)])

        # All 15 tools (5 plugins * 3 tools) should be registered
        assert len(bridge.registered_tools) == 15

    @pytest.mark.asyncio
    async def test_concurrent_sync_and_remove_is_safe(
        self,
        mock_mcp: MagicMock,
        registry: ToolRegistry,
        mock_call_handler: AsyncMock,
    ) -> None:
        """Verify concurrent sync and remove operations are safe."""
        from opencuff.plugins.fastmcp_bridge import FastMCPBridge

        bridge = FastMCPBridge(mock_mcp, registry, mock_call_handler)

        # Pre-populate with some plugins
        for i in range(3):
            tools = [ToolDefinition(name="tool", description=f"Tool {i}")]
            await bridge.sync_tools(f"existing_{i}", tools)

        async def sync_new(index: int) -> None:
            tools = [ToolDefinition(name="tool", description=f"New {index}")]
            await bridge.sync_tools(f"new_{index}", tools)

        async def remove_existing(index: int) -> None:
            await bridge.remove_plugin_tools(f"existing_{index}")

        # Run syncs and removes concurrently
        await asyncio.gather(
            sync_new(0),
            sync_new(1),
            remove_existing(0),
            remove_existing(1),
            sync_new(2),
            remove_existing(2),
        )

        # Should complete without errors (exact state depends on timing)
        # Just verify we can still use the bridge
        assert isinstance(bridge.registered_tools, set)
