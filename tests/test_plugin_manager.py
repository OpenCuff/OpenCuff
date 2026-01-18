"""Tests for the plugin manager module.

Tests cover:
    - Plugin loading and unloading
    - Plugin reload with request barrier
    - Health check scheduling
    - Configuration change handling
    - Tool routing
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from opencuff.plugins.base import PluginState
from opencuff.plugins.config import OpenCuffSettings, PluginConfig, PluginType
from opencuff.plugins.errors import PluginError, PluginErrorCode
from opencuff.plugins.manager import HealthMonitor, PluginLifecycle, PluginManager
from opencuff.plugins.registry import ToolRegistry


class TestPluginLifecycle:
    """Tests for PluginLifecycle class."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create a fresh registry for each test."""
        return ToolRegistry()

    @pytest.fixture
    def dummy_config(self) -> PluginConfig:
        """Create a dummy plugin configuration."""
        return PluginConfig(
            type=PluginType.IN_SOURCE,
            enabled=True,
            module="opencuff.plugins.builtin.dummy",
            config={"prefix": "Test: "},
        )

    @pytest.mark.asyncio
    async def test_initial_state_is_unloaded(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify initial state is UNLOADED."""
        lifecycle = PluginLifecycle("test", dummy_config, registry)

        assert lifecycle.state == PluginState.UNLOADED

    @pytest.mark.asyncio
    async def test_load_transitions_to_active(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify load() transitions state to ACTIVE."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)

        await lifecycle.load()

        assert lifecycle.state == PluginState.ACTIVE

    @pytest.mark.asyncio
    async def test_load_registers_tools(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify load() registers tools in the registry."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)

        await lifecycle.load()

        # Dummy plugin has 3 tools
        assert registry.get_tool("dummy.echo") is not None
        assert registry.get_tool("dummy.add") is not None
        assert registry.get_tool("dummy.slow") is not None

    @pytest.mark.asyncio
    async def test_unload_transitions_to_unloaded(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify unload() transitions state to UNLOADED."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)
        await lifecycle.load()

        await lifecycle.unload()

        assert lifecycle.state == PluginState.UNLOADED

    @pytest.mark.asyncio
    async def test_unload_removes_tools(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify unload() removes tools from registry."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)
        await lifecycle.load()

        await lifecycle.unload()

        assert registry.get_tool("dummy.echo") is None

    @pytest.mark.asyncio
    async def test_call_tool_succeeds(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify call_tool() invokes the tool successfully."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)
        await lifecycle.load()

        result = await lifecycle.call_tool("echo", {"message": "hello"})

        assert result.success is True
        assert result.data == "Test: hello"

    @pytest.mark.asyncio
    async def test_call_tool_on_inactive_raises_error(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify call_tool() raises error when plugin not active."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)

        with pytest.raises(PluginError) as exc_info:
            await lifecycle.call_tool("echo", {"message": "hello"})

        assert exc_info.value.code == PluginErrorCode.PLUGIN_UNHEALTHY

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_healthy(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify health_check() returns True for healthy plugin."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)
        await lifecycle.load()

        healthy = await lifecycle.health_check()

        assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_not_loaded(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify health_check() returns False when not loaded."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)

        healthy = await lifecycle.health_check()

        assert healthy is False


class TestPluginLifecycleReload:
    """Tests for plugin reload functionality."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        """Create a fresh registry for each test."""
        return ToolRegistry()

    @pytest.fixture
    def dummy_config(self) -> PluginConfig:
        """Create a dummy plugin configuration."""
        return PluginConfig(
            type=PluginType.IN_SOURCE,
            enabled=True,
            module="opencuff.plugins.builtin.dummy",
            config={"prefix": "Old: "},
        )

    @pytest.mark.asyncio
    async def test_reload_updates_config(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify reload() applies new configuration."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)
        await lifecycle.load()

        new_config = PluginConfig(
            type=PluginType.IN_SOURCE,
            module="opencuff.plugins.builtin.dummy",
            config={"prefix": "New: "},
        )
        await lifecycle.reload(new_config)

        result = await lifecycle.call_tool("echo", {"message": "hello"})
        assert result.data == "New: hello"

    @pytest.mark.asyncio
    async def test_reload_blocks_new_requests(
        self, registry: ToolRegistry, dummy_config: PluginConfig
    ) -> None:
        """Verify reload() blocks new requests during reload."""
        lifecycle = PluginLifecycle("dummy", dummy_config, registry)
        await lifecycle.load()

        # Start a slow request
        slow_task = asyncio.create_task(lifecycle.call_tool("slow", {"seconds": 0.1}))

        # Give it time to start
        await asyncio.sleep(0.02)

        # Start reload (should wait for slow request)
        reload_task = asyncio.create_task(lifecycle.reload())

        # Both should complete
        await asyncio.gather(slow_task, reload_task)

        # Plugin should still be functional
        result = await lifecycle.call_tool("echo", {"message": "test"})
        assert result.success is True


class TestPluginManager:
    """Tests for PluginManager class."""

    @pytest.fixture
    def settings(self) -> OpenCuffSettings:
        """Create test settings with dummy plugin."""
        return OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=True,
                    module="opencuff.plugins.builtin.dummy",
                    config={"prefix": ""},
                )
            },
            plugin_settings={
                "health_check_interval": 0,  # Disable health checks for tests
                "live_reload": False,
            },
        )

    @pytest.mark.asyncio
    async def test_start_loads_plugins(self, settings: OpenCuffSettings) -> None:
        """Verify start() loads all enabled plugins."""
        manager = PluginManager(settings=settings)

        await manager.start()

        assert "dummy" in manager.plugins
        assert manager.plugins["dummy"].state == PluginState.ACTIVE

        await manager.stop()

    @pytest.mark.asyncio
    async def test_stop_unloads_plugins(self, settings: OpenCuffSettings) -> None:
        """Verify stop() unloads all plugins."""
        manager = PluginManager(settings=settings)
        await manager.start()

        await manager.stop()

        assert len(manager.plugins) == 0

    @pytest.mark.asyncio
    async def test_call_tool_routes_to_plugin(self, settings: OpenCuffSettings) -> None:
        """Verify call_tool() routes to the correct plugin."""
        manager = PluginManager(settings=settings)
        await manager.start()

        result = await manager.call_tool("dummy.echo", {"message": "hello"})

        assert result.success is True
        assert result.data == "hello"

        await manager.stop()

    @pytest.mark.asyncio
    async def test_call_tool_unknown_raises_error(
        self, settings: OpenCuffSettings
    ) -> None:
        """Verify call_tool() raises error for unknown tool."""
        manager = PluginManager(settings=settings)
        await manager.start()

        with pytest.raises(PluginError) as exc_info:
            await manager.call_tool("nonexistent.tool", {})

        assert exc_info.value.code == PluginErrorCode.TOOL_NOT_FOUND

        await manager.stop()

    @pytest.mark.asyncio
    async def test_get_all_tools(self, settings: OpenCuffSettings) -> None:
        """Verify get_all_tools() returns all registered tools."""
        manager = PluginManager(settings=settings)
        await manager.start()

        tools = manager.get_all_tools()

        assert len(tools) == 3
        fqns = [fqn for fqn, _ in tools]
        assert "dummy.echo" in fqns
        assert "dummy.add" in fqns
        assert "dummy.slow" in fqns

        await manager.stop()

    @pytest.mark.asyncio
    async def test_load_plugin_manually(self) -> None:
        """Verify manual plugin loading."""
        manager = PluginManager(settings=OpenCuffSettings())
        await manager.start()

        config = PluginConfig(
            type=PluginType.IN_SOURCE,
            module="opencuff.plugins.builtin.dummy",
            config={},
        )
        await manager.load_plugin("test_plugin", config)

        assert "test_plugin" in manager.plugins
        assert manager.tool_registry.get_tool("test_plugin.echo") is not None

        await manager.stop()

    @pytest.mark.asyncio
    async def test_unload_plugin_manually(self, settings: OpenCuffSettings) -> None:
        """Verify manual plugin unloading."""
        manager = PluginManager(settings=settings)
        await manager.start()

        await manager.unload_plugin("dummy")

        assert "dummy" not in manager.plugins
        assert manager.tool_registry.get_tool("dummy.echo") is None

        await manager.stop()

    @pytest.mark.asyncio
    async def test_disabled_plugins_not_loaded(self) -> None:
        """Verify disabled plugins are not loaded."""
        settings = OpenCuffSettings(
            plugins={
                "disabled_plugin": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=False,
                    module="opencuff.plugins.builtin.dummy",
                )
            }
        )
        manager = PluginManager(settings=settings)
        await manager.start()

        assert "disabled_plugin" not in manager.plugins

        await manager.stop()


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    @pytest.mark.asyncio
    async def test_disabled_with_zero_interval(self) -> None:
        """Verify health monitor doesn't start with 0 interval."""
        settings = OpenCuffSettings(plugin_settings={"health_check_interval": 0})
        manager = PluginManager(settings=settings)
        monitor = HealthMonitor(manager, interval=0)

        await monitor.start()

        # Should not have a running task
        assert monitor._task is None

        await monitor.stop()

    @pytest.mark.asyncio
    async def test_runs_health_checks(self) -> None:
        """Verify health monitor runs periodic checks."""
        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    module="opencuff.plugins.builtin.dummy",
                )
            },
            plugin_settings={"health_check_interval": 0},
        )
        manager = PluginManager(settings=settings)
        await manager.start()

        # Create monitor with short interval
        monitor = HealthMonitor(manager, interval=0.05)
        await monitor.start()

        # Wait for at least one check
        await asyncio.sleep(0.1)

        await monitor.stop()
        await manager.stop()


class TestConfigWatcher:
    """Tests for config file watching."""

    @pytest.mark.asyncio
    async def test_config_change_loads_new_plugin(self) -> None:
        """Verify config change loads newly added plugins."""
        # Create temp settings file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\nplugins: {}\n")
            settings_path = f.name

        try:
            manager = PluginManager(settings_path=settings_path)
            await manager.start()

            assert len(manager.plugins) == 0

            # Update settings file
            Path(settings_path).write_text("""
version: "1"
plugins:
  dummy:
    type: in_source
    module: opencuff.plugins.builtin.dummy
""")

            # Simulate config change
            from opencuff.plugins.config import load_settings

            new_settings = load_settings(settings_path)
            await manager._on_config_change(new_settings)

            assert "dummy" in manager.plugins

            await manager.stop()

        finally:
            Path(settings_path).unlink()

    @pytest.mark.asyncio
    async def test_config_change_unloads_removed_plugin(self) -> None:
        """Verify config change unloads removed plugins."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("""
version: "1"
plugins:
  dummy:
    type: in_source
    module: opencuff.plugins.builtin.dummy
""")
            settings_path = f.name

        try:
            manager = PluginManager(settings_path=settings_path)
            await manager.start()

            assert "dummy" in manager.plugins

            # Update to remove plugin
            Path(settings_path).write_text("version: '1'\nplugins: {}\n")

            from opencuff.plugins.config import load_settings

            new_settings = load_settings(settings_path)
            await manager._on_config_change(new_settings)

            assert "dummy" not in manager.plugins

            await manager.stop()

        finally:
            Path(settings_path).unlink()
