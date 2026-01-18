"""Plugin error types and error codes.

This module defines the error hierarchy for the plugin system, providing
specific error codes for different failure scenarios.

Classes:
    - PluginErrorCode: Enum of error codes for categorizing plugin errors
    - PluginError: Base exception for all plugin-related errors
"""

from enum import Enum


class PluginErrorCode(str, Enum):
    """Error codes for plugin operations.

    Used to categorize errors for logging, recovery decisions, and
    error handling strategies.
    """

    # Configuration errors
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISSING = "CONFIG_MISSING"

    # Lifecycle errors
    LOAD_FAILED = "LOAD_FAILED"
    INIT_FAILED = "INIT_FAILED"
    SHUTDOWN_FAILED = "SHUTDOWN_FAILED"

    # Runtime errors
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TIMEOUT = "TIMEOUT"

    # Communication errors (process/HTTP)
    COMMUNICATION_ERROR = "COMMUNICATION_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"

    # Health errors
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    PLUGIN_UNHEALTHY = "PLUGIN_UNHEALTHY"


class PluginError(Exception):
    """Base exception for plugin errors.

    Attributes:
        code: The error code categorizing this error.
        message: Human-readable error message.
        plugin_name: Name of the plugin that caused the error (if applicable).
        cause: The underlying exception that caused this error (if any).

    Example:
        raise PluginError(
            code=PluginErrorCode.INIT_FAILED,
            message="Failed to connect to database",
            plugin_name="db_plugin",
            cause=original_exception,
        )
    """

    def __init__(
        self,
        code: PluginErrorCode,
        message: str,
        plugin_name: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        """Initialize the plugin error.

        Args:
            code: The error code for this error.
            message: Human-readable error message.
            plugin_name: Name of the plugin (optional).
            cause: The underlying exception (optional).
        """
        self.code = code
        self.message = message
        self.plugin_name = plugin_name
        self.cause = cause

        # Build full error message
        full_message = f"[{code.value}] {message}"
        if plugin_name:
            full_message = f"[{plugin_name}] {full_message}"

        super().__init__(full_message)
