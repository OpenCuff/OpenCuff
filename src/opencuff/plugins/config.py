"""Plugin configuration models and utilities.

This module provides Pydantic models for validating and loading plugin configuration
from YAML files, with support for environment variable expansion.

Models:
    - PluginType: Enum for plugin types (in_source, process, http)
    - ProcessSettings: Settings specific to process plugins
    - HTTPSettings: Settings specific to HTTP plugins
    - PluginConfig: Configuration for a single plugin
    - PluginSettings: Global plugin system settings
    - OpenCuffSettings: Root configuration model

Functions:
    - expand_env_vars: Expand ${VAR} patterns in strings
    - expand_env_vars_in_dict: Recursively expand env vars in nested dicts
    - load_settings: Load and validate settings from a YAML file
"""

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PluginType(str, Enum):
    """Types of plugins supported by OpenCuff.

    Attributes:
        IN_SOURCE: Python modules within the opencuff.plugins namespace
        PROCESS: Standalone executables communicating via JSON over stdin/stdout
        HTTP: Remote services accessed via HTTP with JSON payloads
    """

    IN_SOURCE = "in_source"
    PROCESS = "process"
    HTTP = "http"


class ProcessSettings(BaseModel):
    """Settings specific to process plugins.

    Attributes:
        restart_on_crash: Whether to restart the plugin if it crashes.
        max_restarts: Maximum number of restart attempts before giving up.
        restart_delay: Delay in seconds between restart attempts.
        env: Additional environment variables to pass to the process.
    """

    restart_on_crash: bool = True
    max_restarts: int = 3
    restart_delay: float = 5.0
    env: dict[str, str] = Field(default_factory=dict)


class HTTPSettings(BaseModel):
    """Settings specific to HTTP plugins.

    Attributes:
        timeout: Request timeout in seconds.
        headers: HTTP headers to include in requests.
        retry_count: Number of retry attempts on failure.
        retry_delay: Delay in seconds between retry attempts.
        verify_ssl: Whether to verify SSL certificates.
    """

    timeout: float = 30.0
    headers: dict[str, str] = Field(default_factory=dict)
    retry_count: int = 3
    retry_delay: float = 1.0
    verify_ssl: bool = True


class PluginConfig(BaseModel):
    """Configuration for a single plugin.

    Attributes:
        type: The type of plugin (in_source, process, or http).
        enabled: Whether the plugin is enabled.
        module: Python module path for in_source plugins.
        command: Executable path for process plugins.
        args: Command-line arguments for process plugins.
        endpoint: HTTP endpoint URL for http plugins.
        config: Plugin-specific configuration passed to the plugin.
        process_settings: Settings specific to process plugins.
        http_settings: Settings specific to HTTP plugins.
    """

    type: PluginType
    enabled: bool = True

    # Type-specific fields
    module: str | None = None  # For in_source
    command: str | None = None  # For process
    args: list[str] = Field(default_factory=list)  # For process
    endpoint: str | None = None  # For HTTP

    # Plugin-specific configuration (passed to plugin)
    config: dict[str, Any] = Field(default_factory=dict)

    # Type-specific settings
    process_settings: ProcessSettings | None = None
    http_settings: HTTPSettings | None = None


class PluginSettings(BaseModel):
    """Global plugin system settings.

    Attributes:
        config_poll_interval: Fallback polling interval for config changes (seconds).
            Used only when watchfiles/inotify is unavailable.
        default_timeout: Default timeout for plugin operations (seconds).
        live_reload: Whether to enable live reload of plugins on config changes.
        health_check_interval: Interval for periodic health checks (seconds).
            Set to 0 to disable periodic health checks.
    """

    config_poll_interval: float = 5.0
    default_timeout: float = 30.0
    live_reload: bool = True
    health_check_interval: float = 30.0


class OpenCuffSettings(BaseModel):
    """Root configuration model for OpenCuff.

    Attributes:
        version: Configuration schema version.
        plugin_settings: Global plugin system settings.
        plugins: Dictionary mapping plugin names to their configurations.
    """

    version: str = "1"
    plugin_settings: PluginSettings = Field(default_factory=PluginSettings)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)


# Environment variable expansion pattern: ${VAR_NAME}
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars(value: str) -> str:
    """Expand ${VAR} patterns with environment variables.

    Args:
        value: String potentially containing ${VAR} patterns.

    Returns:
        String with all ${VAR} patterns replaced with environment variable values.

    Raises:
        ValueError: If a referenced environment variable is not set.

    Example:
        >>> os.environ["API_KEY"] = "secret"
        >>> expand_env_vars("Bearer ${API_KEY}")
        "Bearer secret"
    """

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable '{var_name}' not set")
        return env_value

    return _ENV_VAR_PATTERN.sub(replacer, value)


def expand_env_vars_in_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively expand environment variables in a nested dictionary.

    Args:
        data: Dictionary potentially containing ${VAR} patterns in string values.

    Returns:
        New dictionary with all ${VAR} patterns expanded.

    Raises:
        ValueError: If a referenced environment variable is not set.
    """
    result: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, str):
            result[key] = expand_env_vars(value)
        elif isinstance(value, dict):
            result[key] = expand_env_vars_in_dict(value)
        elif isinstance(value, list):
            result[key] = [
                expand_env_vars(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def load_settings(path: str | Path) -> OpenCuffSettings:
    """Load and validate settings from a YAML file.

    Performs environment variable expansion on all string values before
    validation.

    Args:
        path: Path to the settings.yml file.

    Returns:
        Validated OpenCuffSettings instance.

    Raises:
        FileNotFoundError: If the settings file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
        pydantic.ValidationError: If the configuration is invalid.
        ValueError: If environment variable expansion fails.

    Example:
        >>> settings = load_settings("~/.opencuff/settings.yml")
        >>> settings.plugins["my_plugin"].type
        PluginType.IN_SOURCE
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Settings file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f)

    # Handle empty file
    if data is None:
        data = {}

    # Expand environment variables
    data = expand_env_vars_in_dict(data)

    return OpenCuffSettings.model_validate(data)
