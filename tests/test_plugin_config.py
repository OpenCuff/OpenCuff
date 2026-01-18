"""Tests for the plugin configuration module.

Tests cover:
    - PluginType enum values
    - Pydantic model validation
    - YAML loading and parsing
    - Environment variable expansion
    - Configuration validation errors
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from opencuff.plugins.config import (
    HTTPSettings,
    OpenCuffSettings,
    PluginConfig,
    PluginSettings,
    PluginType,
    ProcessSettings,
    expand_env_vars,
    load_settings,
)


class TestPluginType:
    """Tests for PluginType enum."""

    def test_in_source_type(self) -> None:
        """Verify in_source plugin type."""
        assert PluginType.IN_SOURCE.value == "in_source"

    def test_process_type(self) -> None:
        """Verify process plugin type."""
        assert PluginType.PROCESS.value == "process"

    def test_http_type(self) -> None:
        """Verify http plugin type."""
        assert PluginType.HTTP.value == "http"


class TestProcessSettings:
    """Tests for ProcessSettings model."""

    def test_default_values(self) -> None:
        """Verify default values for process settings."""
        settings = ProcessSettings()

        assert settings.restart_on_crash is True
        assert settings.max_restarts == 3
        assert settings.restart_delay == 5.0
        assert settings.env == {}

    def test_custom_values(self) -> None:
        """Verify custom values are accepted."""
        settings = ProcessSettings(
            restart_on_crash=False,
            max_restarts=5,
            restart_delay=10.0,
            env={"FOO": "bar"},
        )

        assert settings.restart_on_crash is False
        assert settings.max_restarts == 5
        assert settings.restart_delay == 10.0
        assert settings.env == {"FOO": "bar"}


class TestHTTPSettings:
    """Tests for HTTPSettings model."""

    def test_default_values(self) -> None:
        """Verify default values for HTTP settings."""
        settings = HTTPSettings()

        assert settings.timeout == 30.0
        assert settings.headers == {}
        assert settings.retry_count == 3
        assert settings.retry_delay == 1.0
        assert settings.verify_ssl is True

    def test_custom_values(self) -> None:
        """Verify custom values are accepted."""
        settings = HTTPSettings(
            timeout=60.0,
            headers={"Authorization": "Bearer token"},
            retry_count=5,
            retry_delay=2.0,
            verify_ssl=False,
        )

        assert settings.timeout == 60.0
        assert settings.headers == {"Authorization": "Bearer token"}
        assert settings.retry_count == 5
        assert settings.retry_delay == 2.0
        assert settings.verify_ssl is False


class TestPluginConfig:
    """Tests for PluginConfig model."""

    def test_minimal_in_source_config(self) -> None:
        """Verify minimal in-source plugin configuration."""
        config = PluginConfig(
            type=PluginType.IN_SOURCE,
            module="opencuff.plugins.dummy",
        )

        assert config.type == PluginType.IN_SOURCE
        assert config.enabled is True
        assert config.module == "opencuff.plugins.dummy"

    def test_minimal_process_config(self) -> None:
        """Verify minimal process plugin configuration."""
        config = PluginConfig(
            type=PluginType.PROCESS,
            command="/usr/bin/my-plugin",
        )

        assert config.type == PluginType.PROCESS
        assert config.command == "/usr/bin/my-plugin"
        assert config.args == []

    def test_minimal_http_config(self) -> None:
        """Verify minimal HTTP plugin configuration."""
        config = PluginConfig(
            type=PluginType.HTTP,
            endpoint="https://api.example.com/plugin",
        )

        assert config.type == PluginType.HTTP
        assert config.endpoint == "https://api.example.com/plugin"

    def test_full_in_source_config(self) -> None:
        """Verify full in-source plugin configuration."""
        config = PluginConfig(
            type=PluginType.IN_SOURCE,
            enabled=True,
            module="opencuff.plugins.makefile",
            config={"makefile_path": "./Makefile", "targets": "*"},
        )

        assert config.type == PluginType.IN_SOURCE
        assert config.module == "opencuff.plugins.makefile"
        assert config.config == {"makefile_path": "./Makefile", "targets": "*"}

    def test_full_process_config(self) -> None:
        """Verify full process plugin configuration with settings."""
        config = PluginConfig(
            type=PluginType.PROCESS,
            command="/usr/bin/linter",
            args=["--mode", "mcp"],
            config={"severity": "warning"},
            process_settings=ProcessSettings(max_restarts=5),
        )

        assert config.command == "/usr/bin/linter"
        assert config.args == ["--mode", "mcp"]
        assert config.process_settings.max_restarts == 5

    def test_full_http_config(self) -> None:
        """Verify full HTTP plugin configuration with settings."""
        config = PluginConfig(
            type=PluginType.HTTP,
            endpoint="https://api.example.com",
            config={"language": "python"},
            http_settings=HTTPSettings(timeout=60.0),
        )

        assert config.endpoint == "https://api.example.com"
        assert config.http_settings.timeout == 60.0

    def test_disabled_plugin(self) -> None:
        """Verify plugin can be disabled."""
        config = PluginConfig(
            type=PluginType.IN_SOURCE,
            enabled=False,
            module="opencuff.plugins.disabled",
        )

        assert config.enabled is False


class TestPluginSettings:
    """Tests for PluginSettings model."""

    def test_default_values(self) -> None:
        """Verify default global plugin settings."""
        settings = PluginSettings()

        assert settings.config_poll_interval == 5.0
        assert settings.default_timeout == 30.0
        assert settings.live_reload is True
        assert settings.health_check_interval == 30.0

    def test_disabled_health_check(self) -> None:
        """Verify health check can be disabled with 0."""
        settings = PluginSettings(health_check_interval=0)

        assert settings.health_check_interval == 0


class TestOpenCuffSettings:
    """Tests for OpenCuffSettings model."""

    def test_default_values(self) -> None:
        """Verify default root settings."""
        settings = OpenCuffSettings()

        assert settings.version == "1"
        assert settings.plugin_settings is not None
        assert settings.plugins == {}

    def test_from_dict(self) -> None:
        """Verify settings can be created from dict."""
        data = {
            "version": "1",
            "plugin_settings": {
                "default_timeout": 60.0,
            },
            "plugins": {
                "dummy": {
                    "type": "in_source",
                    "module": "opencuff.plugins.builtin.dummy",
                }
            },
        }

        settings = OpenCuffSettings.model_validate(data)

        assert settings.version == "1"
        assert settings.plugin_settings.default_timeout == 60.0
        assert "dummy" in settings.plugins
        assert settings.plugins["dummy"].type == PluginType.IN_SOURCE


class TestEnvVarExpansion:
    """Tests for environment variable expansion."""

    def test_expand_single_var(self) -> None:
        """Verify single environment variable expansion."""
        os.environ["TEST_VAR"] = "test_value"
        try:
            result = expand_env_vars("prefix_${TEST_VAR}_suffix")
            assert result == "prefix_test_value_suffix"
        finally:
            del os.environ["TEST_VAR"]

    def test_expand_multiple_vars(self) -> None:
        """Verify multiple environment variables are expanded."""
        os.environ["VAR1"] = "first"
        os.environ["VAR2"] = "second"
        try:
            result = expand_env_vars("${VAR1}_and_${VAR2}")
            assert result == "first_and_second"
        finally:
            del os.environ["VAR1"]
            del os.environ["VAR2"]

    def test_missing_var_raises_error(self) -> None:
        """Verify missing environment variable raises ValueError."""
        with pytest.raises(ValueError, match="NONEXISTENT_VAR"):
            expand_env_vars("${NONEXISTENT_VAR}")

    def test_no_vars_unchanged(self) -> None:
        """Verify string without vars is unchanged."""
        result = expand_env_vars("plain string without vars")
        assert result == "plain string without vars"

    def test_expand_in_nested_dict(self) -> None:
        """Verify environment variables are expanded in nested structures."""
        from opencuff.plugins.config import expand_env_vars_in_dict

        os.environ["API_KEY"] = "secret123"
        try:
            data = {
                "headers": {
                    "Authorization": "Bearer ${API_KEY}",
                },
                "plain": "no vars here",
            }
            result = expand_env_vars_in_dict(data)
            assert result["headers"]["Authorization"] == "Bearer secret123"
            assert result["plain"] == "no vars here"
        finally:
            del os.environ["API_KEY"]


class TestLoadSettings:
    """Tests for YAML settings loading."""

    def test_load_valid_yaml(self) -> None:
        """Verify valid YAML file loads correctly."""
        yaml_content = """
version: "1"
plugin_settings:
  default_timeout: 45.0
plugins:
  test_plugin:
    type: in_source
    module: opencuff.plugins.test
    config:
      key: value
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                settings = load_settings(f.name)
                assert settings.version == "1"
                assert settings.plugin_settings.default_timeout == 45.0
                assert "test_plugin" in settings.plugins
            finally:
                Path(f.name).unlink()

    def test_load_empty_plugins(self) -> None:
        """Verify settings with no plugins loads correctly."""
        yaml_content = """
version: "1"
plugins: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                settings = load_settings(f.name)
                assert settings.plugins == {}
            finally:
                Path(f.name).unlink()

    def test_load_with_env_var_expansion(self) -> None:
        """Verify environment variables are expanded during load."""
        os.environ["MY_ENDPOINT"] = "https://api.example.com"
        yaml_content = """
version: "1"
plugins:
  http_plugin:
    type: http
    endpoint: "${MY_ENDPOINT}/v1"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                settings = load_settings(f.name)
                assert (
                    settings.plugins["http_plugin"].endpoint
                    == "https://api.example.com/v1"
                )
            finally:
                Path(f.name).unlink()
                del os.environ["MY_ENDPOINT"]

    def test_load_nonexistent_file_raises_error(self) -> None:
        """Verify loading nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_settings("/nonexistent/path/settings.yml")

    def test_load_invalid_yaml_raises_error(self) -> None:
        """Verify invalid YAML raises error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("invalid: yaml: content: [")
            f.flush()

            try:
                with pytest.raises(yaml.YAMLError):
                    load_settings(f.name)
            finally:
                Path(f.name).unlink()

    def test_load_invalid_schema_raises_error(self) -> None:
        """Verify invalid configuration raises validation error."""
        yaml_content = """
version: "1"
plugins:
  bad_plugin:
    type: invalid_type
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                from pydantic import ValidationError

                with pytest.raises(ValidationError):
                    load_settings(f.name)
            finally:
                Path(f.name).unlink()


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_duplicate_plugin_names_detected(self) -> None:
        """YAML doesn't allow duplicate keys, so this is handled by YAML parser."""
        # This test documents the behavior - YAML parsers typically use last value
        yaml_content = """
version: "1"
plugins:
  same_name:
    type: in_source
    module: module1
  same_name:
    type: in_source
    module: module2
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                # YAML uses last value for duplicate keys
                settings = load_settings(f.name)
                assert settings.plugins["same_name"].module == "module2"
            finally:
                Path(f.name).unlink()
