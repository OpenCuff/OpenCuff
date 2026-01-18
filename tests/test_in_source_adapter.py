"""Tests for the in-source plugin adapter.

Tests cover:
    - Module path validation (allowed/disallowed prefixes)
    - Module loading and instantiation
    - Plugin class validation
    - Tool retrieval
    - Tool invocation
    - Error handling
"""

import pytest

from opencuff.plugins.adapters.in_source import InSourceAdapter
from opencuff.plugins.base import ToolDefinition
from opencuff.plugins.errors import PluginError, PluginErrorCode


class TestInSourceAdapterModulePathValidation:
    """Tests for module path validation."""

    def test_allowed_prefix_passes_validation(self) -> None:
        """Verify module path with allowed prefix passes validation."""
        # Should not raise
        adapter = InSourceAdapter(
            name="test",
            module_path="opencuff.plugins.builtin.dummy",
        )

        assert adapter.name == "test"

    def test_disallowed_prefix_raises_error(self) -> None:
        """Verify module path with disallowed prefix raises PluginError."""
        with pytest.raises(PluginError) as exc_info:
            InSourceAdapter(
                name="test",
                module_path="some.other.module",
            )

        assert exc_info.value.code == PluginErrorCode.CONFIG_INVALID
        assert "not in allowed namespace" in exc_info.value.message

    def test_custom_allowed_prefixes(self) -> None:
        """Verify custom allowed_prefixes parameter works."""
        # Using custom prefixes should allow different module paths
        adapter = InSourceAdapter(
            name="test",
            module_path="mycompany.plugins.custom",
            allowed_prefixes=["mycompany.plugins."],
        )

        assert adapter.name == "test"

    def test_custom_allowed_prefixes_blocks_default(self) -> None:
        """Verify custom allowed_prefixes replaces defaults."""
        # Default prefix should no longer work with custom prefixes
        with pytest.raises(PluginError) as exc_info:
            InSourceAdapter(
                name="test",
                module_path="opencuff.plugins.builtin.dummy",
                allowed_prefixes=["mycompany.plugins."],
            )

        assert exc_info.value.code == PluginErrorCode.CONFIG_INVALID

    def test_multiple_allowed_prefixes(self) -> None:
        """Verify multiple allowed prefixes all work."""
        # First prefix
        adapter1 = InSourceAdapter(
            name="test1",
            module_path="prefix1.module",
            allowed_prefixes=["prefix1.", "prefix2."],
        )
        assert adapter1.name == "test1"

        # Second prefix
        adapter2 = InSourceAdapter(
            name="test2",
            module_path="prefix2.module",
            allowed_prefixes=["prefix1.", "prefix2."],
        )
        assert adapter2.name == "test2"

    def test_default_allowed_prefixes_constant(self) -> None:
        """Verify DEFAULT_ALLOWED_PREFIXES class attribute exists."""
        assert hasattr(InSourceAdapter, "DEFAULT_ALLOWED_PREFIXES")
        assert "opencuff.plugins." in InSourceAdapter.DEFAULT_ALLOWED_PREFIXES


class TestInSourceAdapterModuleLoading:
    """Tests for module loading functionality."""

    @pytest.mark.asyncio
    async def test_initialize_loads_module(self) -> None:
        """Verify initialize() loads the specified module."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )

        await adapter.initialize({})

        # Module should be loaded
        assert adapter._module is not None
        assert adapter._plugin is not None

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_initialize_with_invalid_module_raises_error(self) -> None:
        """Verify initialize() raises error for non-existent module."""
        adapter = InSourceAdapter(
            name="nonexistent",
            module_path="opencuff.plugins.does_not_exist",
        )

        with pytest.raises(PluginError) as exc_info:
            await adapter.initialize({})

        assert exc_info.value.code == PluginErrorCode.LOAD_FAILED

    @pytest.mark.asyncio
    async def test_initialize_with_missing_class_raises_error(self) -> None:
        """Verify initialize() raises error when plugin class is missing."""
        adapter = InSourceAdapter(
            name="test",
            module_path="opencuff.plugins.builtin.dummy",
            plugin_class_name="NonExistentClass",
        )

        with pytest.raises(PluginError) as exc_info:
            await adapter.initialize({})

        assert exc_info.value.code == PluginErrorCode.LOAD_FAILED
        assert "NonExistentClass" in exc_info.value.message


class TestInSourceAdapterPluginClassValidation:
    """Tests for plugin class validation."""

    @pytest.mark.asyncio
    async def test_valid_plugin_class_is_accepted(self) -> None:
        """Verify valid InSourcePlugin subclass is accepted."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )

        await adapter.initialize({})

        # Should have successfully instantiated the plugin
        assert adapter._plugin is not None

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_custom_plugin_class_name(self) -> None:
        """Verify custom plugin class name can be specified."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
            plugin_class_name="Plugin",  # This is the actual class name
        )

        await adapter.initialize({})

        assert adapter._plugin is not None

        await adapter.shutdown()


class TestInSourceAdapterToolRetrieval:
    """Tests for tool retrieval functionality."""

    @pytest.mark.asyncio
    async def test_get_tools_returns_tool_list(self) -> None:
        """Verify get_tools() returns list of ToolDefinition."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})

        tools = await adapter.get_tools()

        assert isinstance(tools, list)
        assert len(tools) > 0
        assert all(isinstance(t, ToolDefinition) for t in tools)

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_get_tools_before_init_raises_error(self) -> None:
        """Verify get_tools() raises error before initialization."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )

        with pytest.raises(PluginError) as exc_info:
            await adapter.get_tools()

        assert exc_info.value.code == PluginErrorCode.PLUGIN_UNHEALTHY

    @pytest.mark.asyncio
    async def test_get_tools_includes_expected_tools(self) -> None:
        """Verify get_tools() includes expected dummy plugin tools."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})

        tools = await adapter.get_tools()
        tool_names = [t.name for t in tools]

        assert "echo" in tool_names
        assert "add" in tool_names
        assert "slow" in tool_names

        await adapter.shutdown()


class TestInSourceAdapterToolInvocation:
    """Tests for tool invocation functionality."""

    @pytest.mark.asyncio
    async def test_call_tool_succeeds(self) -> None:
        """Verify call_tool() invokes tool successfully."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})

        result = await adapter.call_tool("echo", {"message": "hello"})

        assert result.success is True
        assert result.data == "hello"

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_call_tool_before_init_raises_error(self) -> None:
        """Verify call_tool() raises error before initialization."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )

        with pytest.raises(PluginError) as exc_info:
            await adapter.call_tool("echo", {"message": "hello"})

        assert exc_info.value.code == PluginErrorCode.PLUGIN_UNHEALTHY

    @pytest.mark.asyncio
    async def test_call_tool_with_config(self) -> None:
        """Verify call_tool() respects plugin configuration."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
            config={"prefix": "TEST: "},
        )
        await adapter.initialize({})

        result = await adapter.call_tool("echo", {"message": "hello"})

        assert result.success is True
        assert result.data == "TEST: hello"

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool_returns_error(self) -> None:
        """Verify call_tool() returns error for unknown tool."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})

        result = await adapter.call_tool("nonexistent", {})

        assert result.success is False
        assert "Unknown tool" in result.error

        await adapter.shutdown()


class TestInSourceAdapterErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_healthy(self) -> None:
        """Verify health_check() returns True for healthy plugin."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})

        healthy = await adapter.health_check()

        assert healthy is True

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_not_initialized(self) -> None:
        """Verify health_check() returns False before initialization."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )

        healthy = await adapter.health_check()

        assert healthy is False

    @pytest.mark.asyncio
    async def test_shutdown_clears_plugin(self) -> None:
        """Verify shutdown() clears the plugin instance."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})
        assert adapter._plugin is not None

        await adapter.shutdown()

        assert adapter._plugin is None

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self) -> None:
        """Verify shutdown() can be called multiple times safely."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )
        await adapter.initialize({})

        # First shutdown
        await adapter.shutdown()

        # Second shutdown should not raise
        await adapter.shutdown()

        assert adapter._plugin is None


class TestInSourceAdapterReload:
    """Tests for plugin reload functionality."""

    @pytest.mark.asyncio
    async def test_reload_updates_config(self) -> None:
        """Verify reload() updates the plugin configuration."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
            config={"prefix": "OLD: "},
        )
        await adapter.initialize({})

        # Reload with new config
        await adapter.reload({"prefix": "NEW: "})

        result = await adapter.call_tool("echo", {"message": "test"})
        assert result.data == "NEW: test"

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_reload_before_init_raises_error(self) -> None:
        """Verify reload() raises error before initialization."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
        )

        with pytest.raises(PluginError) as exc_info:
            await adapter.reload({})

        assert exc_info.value.code == PluginErrorCode.PLUGIN_UNHEALTHY


class TestInSourceAdapterConfigMerging:
    """Tests for configuration merging behavior."""

    @pytest.mark.asyncio
    async def test_init_config_takes_precedence(self) -> None:
        """Verify config from __init__ takes precedence over initialize()."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
            config={"prefix": "INIT: "},
        )

        # Pass different config to initialize
        await adapter.initialize({"prefix": "INITIALIZE: "})

        result = await adapter.call_tool("echo", {"message": "test"})

        # Init config should win
        assert result.data == "INIT: test"

        await adapter.shutdown()

    @pytest.mark.asyncio
    async def test_configs_are_merged(self) -> None:
        """Verify configs are merged with init taking precedence."""
        adapter = InSourceAdapter(
            name="dummy",
            module_path="opencuff.plugins.builtin.dummy",
            config={"prefix": "PREFIX: "},
        )

        # Initialize with additional keys
        await adapter.initialize({"other_key": "value"})

        # Plugin should have received merged config
        result = await adapter.call_tool("echo", {"message": "test"})
        assert result.data == "PREFIX: test"

        await adapter.shutdown()
