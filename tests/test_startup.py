"""Tests for plugin tool loading on server startup.

These tests verify that plugin tools are properly loaded and available
when the MCP server starts, using the OPENCUFF_SETTINGS environment variable
to specify the settings file location.

Tests cover:
    - Tools loaded via lifespan context
    - OPENCUFF_SETTINGS environment variable support
    - Makefile plugin tools available on startup
"""

import os
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client

from opencuff.plugins.config import OpenCuffSettings, PluginConfig, PluginType
from opencuff.server import (
    _reset_for_testing,
    find_settings_path,
    initialize_plugins,
    mcp,
)


class TestStartupToolLoading:
    """Tests for automatic tool loading on server startup."""

    @pytest_asyncio.fixture(autouse=True)
    async def reset_server(self):
        """Reset server state before each test."""
        await _reset_for_testing()
        yield
        await _reset_for_testing()

    @pytest.mark.asyncio
    async def test_plugin_tools_loaded_via_env_var_on_startup(
        self, tmp_path: Path
    ) -> None:
        """Verify plugin tools are loaded using OPENCUFF_SETTINGS env var.

        This simulates the real-world startup flow when running via
        fastmcp run with mcp.json configuration:
        1. Sets up a settings file with the dummy plugin
        2. Sets OPENCUFF_SETTINGS env var to point to it
        3. Calls initialize_plugins() without arguments (as lifespan does)
        4. Verifies plugin tools are available in the tool list

        This is equivalent to what the lifespan context does on server startup.
        """
        # Create a settings file with dummy plugin
        settings_content = """
version: "1"
plugin_settings:
  health_check_interval: 0
  live_reload: false
plugins:
  dummy:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.dummy
    config:
      prefix: "Startup: "
"""
        settings_file = tmp_path / "settings.yml"
        settings_file.write_text(settings_content)

        # Set the environment variable
        old_env = os.environ.get("OPENCUFF_SETTINGS")
        os.environ["OPENCUFF_SETTINGS"] = str(settings_file)

        try:
            # Initialize plugins without arguments - uses env var via
            # find_settings_path(). This is exactly what _server_lifespan() does
            await initialize_plugins()

            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool_names = [t.name for t in tools]

                # Plugin tools should be available
                assert "dummy.echo" in tool_names, (
                    f"dummy.echo not found in tools. Available: {tool_names}"
                )
                assert "dummy.add" in tool_names
                assert "dummy.slow" in tool_names

                # Built-in tools should also be present
                assert "hello" in tool_names
                assert "list_plugins" in tool_names

                # Verify the tools actually work
                result = await client.call_tool(
                    "dummy.echo",
                    {"message": "from startup"},
                )
                assert "Startup: from startup" in str(result)
        finally:
            # Restore environment
            if old_env is None:
                os.environ.pop("OPENCUFF_SETTINGS", None)
            else:
                os.environ["OPENCUFF_SETTINGS"] = old_env

    @pytest.mark.asyncio
    async def test_makefile_plugin_tools_loaded_on_startup(
        self, tmp_path: Path
    ) -> None:
        """Verify Makefile plugin tools are loaded on startup.

        This test creates a Makefile with several targets and verifies
        they are exposed as MCP tools when the server starts.
        """
        # Create a simple Makefile
        makefile_content = """.PHONY: test build clean

test: ## Run tests
\t@echo "Running tests"

build: ## Build the project
\t@echo "Building project"

clean: ## Clean build artifacts
\t@echo "Cleaning"
"""
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(makefile_content)

        # Create settings file with makefile plugin
        settings_content = f"""
version: "1"
plugin_settings:
  health_check_interval: 0
  live_reload: false
plugins:
  makefile:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.makefile
    config:
      makefile_path: {makefile_path}
      targets: "*"
      extractor: simple
      cache_ttl: 300
      expose_list_targets: true
"""
        settings_file = tmp_path / "settings.yml"
        settings_file.write_text(settings_content)

        # Initialize plugins via settings path (simulating startup with env var)
        await initialize_plugins(settings_path=settings_file)

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]

            # Makefile targets should be exposed as tools
            # Tool names are prefixed with 'makefile.' and 'make_'
            assert "makefile.make_test" in tool_names, (
                f"makefile.make_test not found. Available: {tool_names}"
            )
            assert "makefile.make_build" in tool_names
            assert "makefile.make_clean" in tool_names

            # The list_targets tool should also be available
            assert "makefile.make_list_targets" in tool_names


class TestEnvironmentVariableSettings:
    """Tests for OPENCUFF_SETTINGS environment variable support."""

    @pytest_asyncio.fixture(autouse=True)
    async def reset_server(self):
        """Reset server state before each test."""
        await _reset_for_testing()
        yield
        await _reset_for_testing()

    def test_find_settings_path_uses_env_var(self, tmp_path: Path) -> None:
        """Verify find_settings_path() checks OPENCUFF_SETTINGS first."""
        settings_file = tmp_path / "custom_settings.yml"
        settings_file.write_text("version: '1'\nplugins: {}")

        old_env = os.environ.get("OPENCUFF_SETTINGS")
        os.environ["OPENCUFF_SETTINGS"] = str(settings_file)

        try:
            result = find_settings_path()
            assert result == settings_file
        finally:
            if old_env is None:
                os.environ.pop("OPENCUFF_SETTINGS", None)
            else:
                os.environ["OPENCUFF_SETTINGS"] = old_env

    def test_find_settings_path_falls_back_when_env_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify fallback when OPENCUFF_SETTINGS points to missing file."""
        # Set env var to non-existent file
        old_env = os.environ.get("OPENCUFF_SETTINGS")
        os.environ["OPENCUFF_SETTINGS"] = "/nonexistent/settings.yml"

        # Change cwd to tmp_path with a settings file
        cwd_settings = tmp_path / "settings.yml"
        cwd_settings.write_text("version: '1'\nplugins: {}")
        monkeypatch.chdir(tmp_path)

        try:
            result = find_settings_path()
            # Should fall back to cwd settings
            assert result == cwd_settings
        finally:
            if old_env is None:
                os.environ.pop("OPENCUFF_SETTINGS", None)
            else:
                os.environ["OPENCUFF_SETTINGS"] = old_env

    def test_find_settings_path_returns_none_when_no_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify None returned when no settings file exists."""
        # Clear env var
        old_env = os.environ.get("OPENCUFF_SETTINGS")
        os.environ.pop("OPENCUFF_SETTINGS", None)

        # Change to empty directory
        monkeypatch.chdir(tmp_path)

        try:
            result = find_settings_path()
            # Should return None (no settings found)
            assert result is None
        finally:
            if old_env is not None:
                os.environ["OPENCUFF_SETTINGS"] = old_env

    @pytest.mark.asyncio
    async def test_initialize_plugins_uses_env_var(self, tmp_path: Path) -> None:
        """Verify initialize_plugins() loads settings from OPENCUFF_SETTINGS env var.

        This is the key integration test that verifies the fix works end-to-end:
        1. Settings file with plugins is created
        2. OPENCUFF_SETTINGS env var points to it
        3. initialize_plugins() is called without arguments
        4. Plugins are loaded from the env var path
        """
        settings_content = """
version: "1"
plugin_settings:
  health_check_interval: 0
  live_reload: false
plugins:
  dummy:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.dummy
    config:
      prefix: "EnvVar: "
"""
        settings_file = tmp_path / "settings.yml"
        settings_file.write_text(settings_content)

        old_env = os.environ.get("OPENCUFF_SETTINGS")
        os.environ["OPENCUFF_SETTINGS"] = str(settings_file)

        try:
            # Call initialize_plugins without any arguments
            # It should find settings via OPENCUFF_SETTINGS env var
            await initialize_plugins()

            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool_names = [t.name for t in tools]

                # Plugin should be loaded
                assert "dummy.echo" in tool_names, (
                    f"dummy.echo not found. Available: {tool_names}"
                )

                # Verify it uses the correct config
                result = await client.call_tool(
                    "dummy.echo",
                    {"message": "test"},
                )
                assert "EnvVar: test" in str(result)
        finally:
            if old_env is None:
                os.environ.pop("OPENCUFF_SETTINGS", None)
            else:
                os.environ["OPENCUFF_SETTINGS"] = old_env


class TestStartupWithMultiplePlugins:
    """Tests for startup with multiple plugins configured."""

    @pytest_asyncio.fixture(autouse=True)
    async def reset_server(self):
        """Reset server state before each test."""
        await _reset_for_testing()
        yield
        await _reset_for_testing()

    @pytest.mark.asyncio
    async def test_multiple_plugins_loaded_on_startup(self, tmp_path: Path) -> None:
        """Verify multiple plugins are all loaded on startup."""
        # Create a Makefile
        makefile_path = tmp_path / "Makefile"
        makefile_path.write_text(".PHONY: help\nhelp: ## Show help\n\t@echo help")

        # Create settings file with both dummy and makefile plugins
        settings_content = f"""
version: "1"
plugin_settings:
  health_check_interval: 0
  live_reload: false
plugins:
  dummy:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.dummy
    config:
      prefix: "Multi: "
  makefile:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.makefile
    config:
      makefile_path: {makefile_path}
      targets: "*"
      extractor: simple
"""
        settings_file = tmp_path / "settings.yml"
        settings_file.write_text(settings_content)

        # Initialize plugins via settings path
        await initialize_plugins(settings_path=settings_file)

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]

            # Both plugins' tools should be available
            assert "dummy.echo" in tool_names
            assert "dummy.add" in tool_names
            assert "makefile.make_help" in tool_names

            # list_plugins should show both
            result = await client.call_tool("list_plugins", {})
            result_str = str(result)
            assert "dummy" in result_str
            assert "makefile" in result_str

    @pytest.mark.asyncio
    async def test_disabled_plugin_not_loaded_on_startup(self, tmp_path: Path) -> None:
        """Verify disabled plugins are not loaded on startup."""
        settings = OpenCuffSettings(
            plugins={
                "dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=True,
                    module="opencuff.plugins.builtin.dummy",
                ),
                "disabled_dummy": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=False,
                    module="opencuff.plugins.builtin.dummy",
                    config={"prefix": "Disabled: "},
                ),
            },
            plugin_settings={
                "health_check_interval": 0,
                "live_reload": False,
            },
        )

        await initialize_plugins(settings=settings)

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]

            # Enabled plugin should be present
            assert "dummy.echo" in tool_names

            # Disabled plugin should NOT be present
            assert "disabled_dummy.echo" not in tool_names
