"""Dummy plugin for testing the plugin system.

This plugin provides simple tools that are useful for testing:
    - dummy_echo: Echoes the input message
    - dummy_add: Adds two numbers
    - dummy_slow: Sleeps for a specified duration (for testing request barrier)

Example configuration:
    plugins:
      dummy:
        type: in_source
        module: opencuff.plugins.builtin.dummy
        config:
          prefix: "Echo: "  # Optional prefix for echo output
"""

import asyncio
from typing import Any

from opencuff.plugins.base import InSourcePlugin, ToolDefinition, ToolResult


class Plugin(InSourcePlugin):
    """Dummy plugin for testing purposes.

    This plugin exposes simple tools that can be used to verify the
    plugin system is working correctly.

    Configuration options:
        prefix: Optional prefix string added to echo output.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the dummy plugin.

        Args:
            config: Plugin configuration containing optional 'prefix'.
        """
        super().__init__(config)
        self._prefix = config.get("prefix", "")
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the plugin."""
        # Re-read prefix from config in case it changed during reload
        self._prefix = self.config.get("prefix", "")
        self._initialized = True

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        self._initialized = False

    async def health_check(self) -> bool:
        """Check if the plugin is healthy."""
        return self._initialized

    def get_tools(self) -> list[ToolDefinition]:
        """Return the list of tools provided by this plugin."""
        return [
            ToolDefinition(
                name="echo",
                description="Echo the input message back",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message to echo",
                        },
                    },
                    "required": ["message"],
                },
                returns={"type": "string"},
            ),
            ToolDefinition(
                name="add",
                description="Add two numbers together",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {
                            "type": "integer",
                            "description": "First number",
                        },
                        "b": {
                            "type": "integer",
                            "description": "Second number",
                        },
                    },
                    "required": ["a", "b"],
                },
                returns={"type": "integer"},
            ),
            ToolDefinition(
                name="slow",
                description="Sleep for a specified duration then return",
                parameters={
                    "type": "object",
                    "properties": {
                        "seconds": {
                            "type": "number",
                            "description": "Number of seconds to sleep",
                        },
                    },
                    "required": ["seconds"],
                },
                returns={"type": "string"},
            ),
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Handle tool invocations.

        Uses dictionary dispatch to route tool calls to their handlers.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Arguments for the tool.

        Returns:
            ToolResult with the tool output.
        """
        if not self._initialized:
            return ToolResult(
                success=False,
                error="Plugin not initialized",
            )

        # Dispatch table mapping tool names to handlers
        tool_handlers = {
            "echo": self._echo,
            "add": self._add,
            "slow": self._slow,
        }

        handler = tool_handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
            )

        return await handler(arguments)

    async def _echo(self, arguments: dict[str, Any]) -> ToolResult:
        """Echo the input message.

        Args:
            arguments: Dict containing 'message' key.

        Returns:
            ToolResult with the echoed message.
        """
        message = arguments.get("message", "")
        return ToolResult(
            success=True,
            data=f"{self._prefix}{message}",
        )

    async def _add(self, arguments: dict[str, Any]) -> ToolResult:
        """Add two numbers.

        Args:
            arguments: Dict containing 'a' and 'b' keys.

        Returns:
            ToolResult with the sum.
        """
        try:
            a = int(arguments.get("a", 0))
            b = int(arguments.get("b", 0))
            return ToolResult(
                success=True,
                data=a + b,
            )
        except (TypeError, ValueError) as e:
            return ToolResult(
                success=False,
                error=f"Invalid arguments: {e}",
            )

    async def _slow(self, arguments: dict[str, Any]) -> ToolResult:
        """Sleep for a specified duration.

        This tool is useful for testing the request barrier behavior
        during plugin reloads.

        Args:
            arguments: Dict containing 'seconds' key.

        Returns:
            ToolResult indicating completion.
        """
        try:
            seconds = float(arguments.get("seconds", 1.0))
            if seconds < 0:
                return ToolResult(
                    success=False,
                    error="Sleep duration must be non-negative",
                )

            await asyncio.sleep(seconds)
            return ToolResult(
                success=True,
                data=f"Slept for {seconds} seconds",
            )
        except (TypeError, ValueError) as e:
            return ToolResult(
                success=False,
                error=f"Invalid arguments: {e}",
            )
