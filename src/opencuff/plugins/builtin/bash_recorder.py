"""BashRecorder plugin for OpenCuff.

This plugin provides a governed bash execution tool that records all commands
executed by AI agents. Unlike standard bash tools, BashRecorder maintains an
audit trail of all agent actions, enabling:

- Audit & Compliance: Track what commands agents execute
- Policy Generation: Automatically create allowlists from recorded sessions
- Debugging: Replay and analyze agent behavior
- Security Analysis: Identify potentially risky command patterns

Example configuration:
    plugins:
      bash_recorder:
        type: in_source
        module: opencuff.plugins.builtin.bash_recorder
        config:
          recording:
            enabled: true
            directory: .cuff/recordings
            capture_output: true
            max_output_size: 1000000
            session_mode: per_conversation
          execution:
            default_timeout: 120
            max_timeout: 600
            shell: /bin/bash

SECURITY WARNING:
    BashRecorder executes any command provided by the AI agent WITHOUT
    restriction. This is intentional for the "learning/recording" phase.
    For production use with untrusted agents, combine with a policy
    enforcement plugin.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from opencuff.plugins.base import InSourcePlugin, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# Plugin version
PLUGIN_VERSION = "1.0.0"

# Try to get OpenCuff version, fallback to unknown
try:
    from opencuff import __version__ as OPENCUFF_VERSION
except ImportError:
    OPENCUFF_VERSION = "unknown"


# =============================================================================
# Exceptions
# =============================================================================


class BashRecorderError(Exception):
    """Base exception for BashRecorder errors."""


class RecordingError(BashRecorderError):
    """Errors related to recording operations."""


class ExecutionError(BashRecorderError):
    """Errors related to command execution."""


class ConfigurationError(BashRecorderError):
    """Errors related to plugin configuration."""


# =============================================================================
# Configuration Models
# =============================================================================


class RecordingConfig(BaseModel):
    """Recording-specific configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable/disable recording",
    )

    directory: Path = Field(
        default=Path(".cuff/recordings"),
        description="Directory to store recordings",
    )

    capture_env: bool = Field(
        default=False,
        description="Record environment variables (security sensitive)",
    )

    env_allowlist: list[str] = Field(
        default=["PATH", "HOME", "USER", "SHELL", "PWD"],
        description="Environment variables to capture when capture_env=True",
    )

    capture_output: bool = Field(
        default=True,
        description="Record stdout/stderr output",
    )

    max_output_size: int = Field(
        default=1_000_000,  # 1MB
        gt=0,
        description="Maximum output size to capture per command (bytes)",
    )

    session_mode: Literal["per_conversation", "per_day", "continuous"] = Field(
        default="per_conversation",
        description=(
            "How to group recordings into sessions. "
            "per_conversation: New session per conversation_id. "
            "per_day: New session each calendar day. "
            "continuous: Single session until server restart."
        ),
    )

    retention_days: int = Field(
        default=30,
        ge=0,
        description="Days to retain recordings (0 = forever)",
    )


class ExecutionConfig(BaseModel):
    """Execution-specific configuration."""

    default_timeout: int = Field(
        default=120,
        gt=0,
        description="Default command timeout in seconds",
    )

    max_timeout: int = Field(
        default=600,
        gt=0,
        description="Maximum allowed timeout in seconds",
    )

    working_directory: Path | None = Field(
        default=None,
        description="Default working directory (None = current directory)",
    )

    shell: str = Field(
        default="/bin/bash",
        description="Shell to use for execution",
    )

    inherit_env: bool = Field(
        default=True,
        description="Inherit parent process environment",
    )

    env_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set/override",
    )


class BashRecorderConfig(BaseModel):
    """Root configuration for BashRecorder plugin."""

    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


# =============================================================================
# Recording Models
# =============================================================================


class RecordingEntry(BaseModel):
    """Single command execution record."""

    # Identity
    entry_id: str
    session_id: str
    sequence_number: int

    # Timing
    timestamp: datetime
    duration_ms: int

    # Command
    command: str
    description: str | None = None
    working_directory: str
    shell: str

    # Execution Context
    timeout_seconds: int
    timed_out: bool

    # Results
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    output_truncated: bool
    output_truncated_bytes: int | None = None

    # Environment (optional)
    environment: dict[str, str] | None = None

    # Agent Context (if available)
    agent_id: str | None = None
    conversation_id: str | None = None
    tool_call_id: str | None = None

    # Metadata
    opencuff_version: str
    plugin_version: str


class SessionMetadata(BaseModel):
    """Session-level metadata stored in separate file."""

    session_id: str
    created_at: datetime
    last_updated: datetime
    entry_count: int
    total_duration_ms: int
    status: Literal[
        "active",
        "complete",
        "shutdown",
        "interrupted",
        "config_reload",
        "recording_disabled",
    ]

    # Aggregates
    commands_succeeded: int
    commands_failed: int
    commands_timed_out: int

    # Context
    working_directory: str
    agent_id: str | None = None
    conversation_id: str | None = None


class SessionIndexEntry(BaseModel):
    """Index entry for a single session."""

    session_id: str
    created_at: datetime
    last_updated: datetime
    entry_count: int
    status: Literal[
        "active",
        "complete",
        "shutdown",
        "interrupted",
        "config_reload",
        "recording_disabled",
    ]
    file_size_bytes: int


class RecordingIndex(BaseModel):
    """Quick lookup index for all sessions."""

    version: str = "1.0"
    last_updated: datetime
    total_sessions: int
    total_entries: int
    sessions: dict[str, SessionIndexEntry]


# =============================================================================
# Session Manager
# =============================================================================


class SessionManager:
    """Manages recording sessions.

    Responsible for:
    - Generating unique session IDs
    - Tracking current session state
    - Managing session lifecycle (start, finalize)
    - Generating entry IDs within a session
    """

    def __init__(self, config: RecordingConfig) -> None:
        """Initialize session manager.

        Args:
            config: Recording configuration.
        """
        self._config = config
        self._current_session_id: str | None = None
        self._entry_count: int = 0
        self._session_start_time: datetime | None = None
        self._total_duration_ms: int = 0
        self._commands_succeeded: int = 0
        self._commands_failed: int = 0
        self._commands_timed_out: int = 0
        self._working_directory: str = str(Path.cwd())

    @property
    def current_session_id(self) -> str | None:
        """Get the current session ID."""
        return self._current_session_id

    @property
    def entry_count(self) -> int:
        """Get the number of entries in the current session."""
        return self._entry_count

    def _generate_session_id(self) -> str:
        """Generate a unique session ID.

        Format: YYYYMMDD_HHMMSS_uuid4_prefix

        Returns:
            A unique session ID string.
        """
        now = datetime.now(UTC)
        date_part = now.strftime("%Y%m%d")
        time_part = now.strftime("%H%M%S")
        uuid_prefix = str(uuid.uuid4()).replace("-", "")[:12]
        return f"{date_part}_{time_part}_{uuid_prefix}"

    def _generate_entry_id(self) -> str:
        """Generate a unique entry ID within the session.

        Format: e_YYYYMMDD_HHMMSS_NNN

        Returns:
            A unique entry ID string.
        """
        now = datetime.now(UTC)
        date_part = now.strftime("%Y%m%d")
        time_part = now.strftime("%H%M%S")
        seq_num = f"{self._entry_count + 1:03d}"
        return f"e_{date_part}_{time_part}_{seq_num}"

    async def start_session(
        self,
        working_directory: str | None = None,
        agent_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """Start a new recording session.

        Args:
            working_directory: Initial working directory for the session.
            agent_id: Agent identifier if available.
            conversation_id: Conversation identifier if available.

        Returns:
            The new session ID.
        """
        self._current_session_id = self._generate_session_id()
        self._entry_count = 0
        self._session_start_time = datetime.now(UTC)
        self._total_duration_ms = 0
        self._commands_succeeded = 0
        self._commands_failed = 0
        self._commands_timed_out = 0
        self._working_directory = working_directory or str(Path.cwd())

        logger.info(
            "session_started",
            extra={
                "session_id": self._current_session_id,
                "working_directory": self._working_directory,
            },
        )

        return self._current_session_id

    async def finalize_session(
        self,
        status: Literal[
            "complete", "shutdown", "interrupted", "config_reload", "recording_disabled"
        ] = "complete",
    ) -> SessionMetadata | None:
        """Finalize the current session.

        Args:
            status: The final status of the session.

        Returns:
            The session metadata, or None if no active session.
        """
        if self._current_session_id is None:
            return None

        now = datetime.now(UTC)
        metadata = SessionMetadata(
            session_id=self._current_session_id,
            created_at=self._session_start_time or now,
            last_updated=now,
            entry_count=self._entry_count,
            total_duration_ms=self._total_duration_ms,
            status=status,
            commands_succeeded=self._commands_succeeded,
            commands_failed=self._commands_failed,
            commands_timed_out=self._commands_timed_out,
            working_directory=self._working_directory,
        )

        logger.info(
            "session_finalized",
            extra={
                "session_id": self._current_session_id,
                "entry_count": self._entry_count,
                "status": status,
            },
        )

        self._current_session_id = None
        return metadata

    def increment_entry_count(self) -> None:
        """Increment the entry count for the current session."""
        self._entry_count += 1

    def record_command_result(
        self,
        duration_ms: int,
        exit_code: int | None,
        timed_out: bool,
    ) -> None:
        """Record the result of a command execution.

        Args:
            duration_ms: Command execution duration in milliseconds.
            exit_code: Command exit code (None if timed out).
            timed_out: Whether the command timed out.
        """
        self._total_duration_ms += duration_ms

        if timed_out:
            self._commands_timed_out += 1
        elif exit_code == 0:
            self._commands_succeeded += 1
        else:
            self._commands_failed += 1

    def get_next_entry_id(self) -> str:
        """Get the next entry ID and increment the counter.

        Returns:
            The next entry ID.
        """
        entry_id = self._generate_entry_id()
        self.increment_entry_count()
        return entry_id


# =============================================================================
# Recorder
# =============================================================================


class Recorder:
    """Handles writing recordings to JSONL files.

    Responsible for:
    - Creating recording directories with proper permissions
    - Writing entries atomically with fsync
    - Managing session metadata files
    - Updating the index file
    """

    def __init__(
        self,
        session_manager: SessionManager,
        config: RecordingConfig,
    ) -> None:
        """Initialize the recorder.

        Args:
            session_manager: The session manager instance.
            config: Recording configuration.
        """
        self._session_manager = session_manager
        self._config = config
        self._initialized = False

    def _ensure_directories(self) -> None:
        """Ensure recording directories exist with proper permissions."""
        sessions_dir = self._config.directory / "sessions"

        if not sessions_dir.exists():
            # Create with restrictive permissions (0700)
            sessions_dir.mkdir(parents=True, mode=0o700)
        else:
            # Ensure permissions are correct - ignore if not possible
            with contextlib.suppress(OSError):
                sessions_dir.chmod(0o700)

        self._initialized = True

    def _get_session_file_path(self) -> Path:
        """Get the path to the current session's JSONL file.

        Returns:
            Path to the session file.
        """
        session_id = self._session_manager.current_session_id
        return self._config.directory / "sessions" / f"{session_id}.jsonl"

    def _get_metadata_file_path(self) -> Path:
        """Get the path to the current session's metadata file.

        Returns:
            Path to the metadata file.
        """
        session_id = self._session_manager.current_session_id
        return self._config.directory / "sessions" / f"{session_id}.meta.json"

    async def write_entry(self, entry: RecordingEntry) -> None:
        """Write a recording entry to the session file.

        Uses atomic write pattern with fsync for durability.

        Args:
            entry: The recording entry to write.

        Raises:
            RecordingError: If the write fails.
        """
        if not self._initialized:
            self._ensure_directories()

        session_file = self._get_session_file_path()
        entry_json = entry.model_dump_json() + "\n"

        try:
            # Use asyncio.to_thread for file I/O to avoid blocking
            await asyncio.to_thread(self._write_entry_sync, session_file, entry_json)
        except OSError as e:
            logger.error(
                "recording_write_failed",
                extra={
                    "session_id": entry.session_id,
                    "entry_id": entry.entry_id,
                    "error": str(e),
                },
            )
            raise RecordingError(f"Failed to write recording entry: {e}") from e

    def _write_entry_sync(self, file_path: Path, content: str) -> None:
        """Synchronous entry write with fsync.

        Args:
            file_path: Path to write to.
            content: Content to write.
        """
        with open(file_path, "a") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        # Set file permissions to 0600 (owner read/write only)
        with contextlib.suppress(OSError):
            file_path.chmod(0o600)

    async def write_session_metadata(self, metadata: SessionMetadata) -> None:
        """Write session metadata to file.

        Args:
            metadata: The session metadata to write.
        """
        if not self._initialized:
            self._ensure_directories()

        metadata_file = (
            self._config.directory / "sessions" / f"{metadata.session_id}.meta.json"
        )
        metadata_json = metadata.model_dump_json(indent=2)

        try:
            await asyncio.to_thread(
                self._write_metadata_sync, metadata_file, metadata_json
            )
        except OSError as e:
            logger.error(
                "metadata_write_failed",
                extra={
                    "session_id": metadata.session_id,
                    "error": str(e),
                },
            )

    def _write_metadata_sync(self, file_path: Path, content: str) -> None:
        """Synchronous metadata write.

        Args:
            file_path: Path to write to.
            content: Content to write.
        """
        with open(file_path, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        with contextlib.suppress(OSError):
            file_path.chmod(0o600)


# =============================================================================
# Plugin Implementation
# =============================================================================


class Plugin(InSourcePlugin):
    """BashRecorder plugin for bash execution with recording.

    This plugin provides:
    - execute: Execute a bash command and record it
    - session_info: Get current session information
    - list_recent: List recent recordings
    """

    PLUGIN_VERSION = PLUGIN_VERSION

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the BashRecorder plugin.

        Args:
            config: Plugin configuration dictionary.
        """
        super().__init__(config)
        self._plugin_config = BashRecorderConfig.model_validate(config)
        self._session_manager: SessionManager | None = None
        self._recorder: Recorder | None = None
        self._recording_enabled: bool = True
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize plugin resources.

        Creates recording directory, initializes session manager,
        and starts a new recording session.
        """
        if self._plugin_config.recording.enabled:
            self._ensure_recording_directory()
            self._session_manager = SessionManager(self._plugin_config.recording)
            self._recorder = Recorder(
                session_manager=self._session_manager,
                config=self._plugin_config.recording,
            )
            await self._session_manager.start_session()
            self._recording_enabled = True
        else:
            self._recording_enabled = False

        self._initialized = True
        logger.info(
            "plugin_initialized",
            extra={
                "recording_enabled": self._recording_enabled,
            },
        )

    async def shutdown(self) -> None:
        """Clean up plugin resources.

        Finalizes current recording session and flushes pending writes.
        """
        if self._session_manager:
            metadata = await self._session_manager.finalize_session(status="shutdown")
            if metadata and self._recorder:
                await self._recorder.write_session_metadata(metadata)

        self._initialized = False
        logger.info("plugin_shutdown")

    async def health_check(self) -> bool:
        """Check plugin health status.

        Returns:
            True if the plugin is healthy, False otherwise.
        """
        if not self._initialized:
            return False

        # Check if recording directory is writable (if recording enabled)
        if self._recording_enabled and self._plugin_config.recording.enabled:
            return self._check_directory_writable()

        return True

    async def detailed_health_check(self) -> dict[str, Any]:
        """Perform detailed health check with diagnostic information.

        Returns:
            Health status dictionary with diagnostic details.
        """
        directory_writable = self._check_directory_writable()
        disk_space_ok = self._check_disk_space()

        healthy = directory_writable and disk_space_ok

        return {
            "healthy": healthy,
            "recording_enabled": self._recording_enabled,
            "directory_writable": directory_writable,
            "disk_space_ok": disk_space_ok,
            "current_session": (
                self._session_manager.current_session_id
                if self._session_manager
                else None
            ),
            "entries_recorded": (
                self._session_manager.entry_count if self._session_manager else 0
            ),
        }

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration changes.

        Args:
            new_config: New configuration dictionary.
        """
        old_config = self._plugin_config
        self._plugin_config = BashRecorderConfig.model_validate(new_config)

        # Handle recording directory change
        if self._plugin_config.recording.directory != old_config.recording.directory:
            if self._session_manager:
                await self._session_manager.finalize_session(status="config_reload")
            self._ensure_recording_directory()
            self._session_manager = SessionManager(self._plugin_config.recording)
            self._recorder = Recorder(
                session_manager=self._session_manager,
                config=self._plugin_config.recording,
            )
            await self._session_manager.start_session()

        # Handle recording enable/disable
        if not self._plugin_config.recording.enabled and old_config.recording.enabled:
            if self._session_manager:
                await self._session_manager.finalize_session(
                    status="recording_disabled"
                )
            self._recording_enabled = False

        self.config = new_config

    def get_tools(self) -> list[ToolDefinition]:
        """Return available tools.

        Returns:
            List of tool definitions.
        """
        return [
            self._get_execute_tool_definition(),
            self._get_session_info_tool_definition(),
            self._get_list_recent_tool_definition(),
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool (without namespace prefix).
            arguments: Tool arguments.

        Returns:
            ToolResult with execution outcome.
        """
        handlers = {
            "execute": self._handle_execute,
            "session_info": self._handle_session_info,
            "list_recent": self._handle_list_recent,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
            )

        return await handler(arguments)

    # =========================================================================
    # Tool Definitions
    # =========================================================================

    def _get_execute_tool_definition(self) -> ToolDefinition:
        """Get the execute tool definition."""
        return ToolDefinition(
            name="execute",
            description="""Execute a bash command and record the execution.

This tool runs shell commands similar to the built-in bash tool, but records
all executions for audit, analysis, and policy generation.

Arguments:
- command (required): The bash command to execute
- timeout (optional): Timeout in seconds (default: 120, max: 600)
- working_directory (optional): Directory to run command in
- description (optional): Human-readable description of what this command does

Returns:
- stdout: Command standard output
- stderr: Command standard error
- exit_code: Command exit code
- timed_out: Whether command exceeded timeout
- duration_ms: Execution time in milliseconds
- recording_id: ID of the recording entry (for reference)
""",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 120,
                        "minimum": 1,
                        "maximum": 600,
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Directory to run command in",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of the command's purpose",
                    },
                },
                "required": ["command"],
            },
            returns={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "timed_out": {"type": "boolean"},
                    "duration_ms": {"type": "integer"},
                    "recording_id": {"type": "string"},
                    "working_directory": {"type": "string"},
                },
            },
        )

    def _get_session_info_tool_definition(self) -> ToolDefinition:
        """Get the session_info tool definition."""
        return ToolDefinition(
            name="session_info",
            description="Get information about the current recording session.",
            parameters={
                "type": "object",
                "properties": {},
            },
            returns={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "entry_count": {"type": "integer"},
                    "recording_enabled": {"type": "boolean"},
                },
            },
        )

    def _get_list_recent_tool_definition(self) -> ToolDefinition:
        """Get the list_recent tool definition."""
        return ToolDefinition(
            name="list_recent",
            description="List recent command recordings from the current session.",
            parameters={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent entries to return",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
            returns={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "command": {"type": "string"},
                        "exit_code": {"type": "integer"},
                        "duration_ms": {"type": "integer"},
                    },
                },
            },
        )

    # =========================================================================
    # Tool Handlers
    # =========================================================================

    async def _handle_execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Handle the execute tool.

        Args:
            arguments: Tool arguments including 'command'.

        Returns:
            ToolResult with execution outcome.
        """
        command = arguments.get("command")
        if not command:
            return ToolResult(
                success=False,
                error="Missing required argument: command",
            )

        # Get timeout, capped at max_timeout
        default_timeout = self._plugin_config.execution.default_timeout
        timeout = arguments.get("timeout", default_timeout)
        timeout = min(timeout, self._plugin_config.execution.max_timeout)

        # Get working directory
        working_directory = arguments.get("working_directory")
        if working_directory:
            work_dir = Path(working_directory)
            if not work_dir.exists():
                return ToolResult(
                    success=False,
                    error=f"Working directory does not exist: {working_directory}",
                )
            if not work_dir.is_dir():
                return ToolResult(
                    success=False,
                    error=f"Working directory is not a directory: {working_directory}",
                )
        else:
            working_directory = (
                str(self._plugin_config.execution.working_directory)
                if self._plugin_config.execution.working_directory
                else str(Path.cwd())
            )

        description = arguments.get("description")

        # Execute the command
        start_time = time.time()
        try:
            result = await self._execute_command(
                command=command,
                timeout=timeout,
                working_directory=working_directory,
            )
        except Exception as e:
            logger.error(
                "command_execution_failed",
                extra={"command": command, "error": str(e)},
            )
            return ToolResult(
                success=False,
                error=f"Command execution failed: {e}",
            )

        duration_ms = int((time.time() - start_time) * 1000)

        # Record the execution (graceful degradation)
        recording_id: str | None = None
        if self._recording_enabled and self._session_manager and self._recorder:
            try:
                recording_id = await self._record_execution(
                    command=command,
                    description=description,
                    working_directory=working_directory,
                    timeout_seconds=timeout,
                    duration_ms=duration_ms,
                    exit_code=result["exit_code"],
                    stdout=result["stdout"],
                    stderr=result["stderr"],
                    timed_out=result["timed_out"],
                )
            except RecordingError as e:
                # Log the error but continue - graceful degradation
                logger.warning(
                    "recording_failed_continuing",
                    extra={"error": str(e)},
                )
                recording_id = None

        return ToolResult(
            success=True,
            data={
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
                "duration_ms": duration_ms,
                "recording_id": recording_id,
                "working_directory": working_directory,
            },
        )

    async def _handle_session_info(self, arguments: dict[str, Any]) -> ToolResult:
        """Handle the session_info tool.

        Args:
            arguments: Tool arguments (none required).

        Returns:
            ToolResult with session information.
        """
        return ToolResult(
            success=True,
            data={
                "session_id": (
                    self._session_manager.current_session_id
                    if self._session_manager
                    else None
                ),
                "entry_count": (
                    self._session_manager.entry_count if self._session_manager else 0
                ),
                "recording_enabled": self._recording_enabled,
            },
        )

    async def _handle_list_recent(self, arguments: dict[str, Any]) -> ToolResult:
        """Handle the list_recent tool.

        Args:
            arguments: Tool arguments including optional 'count'.

        Returns:
            ToolResult with list of recent recordings.
        """
        count = arguments.get("count", 10)

        if not self._session_manager or not self._recording_enabled:
            return ToolResult(
                success=True,
                data=[],
            )

        # Read entries from the current session file
        session_file = (
            self._plugin_config.recording.directory
            / "sessions"
            / f"{self._session_manager.current_session_id}.jsonl"
        )

        if not session_file.exists():
            return ToolResult(
                success=True,
                data=[],
            )

        try:
            entries = await asyncio.to_thread(
                self._read_recent_entries, session_file, count
            )
            return ToolResult(
                success=True,
                data=entries,
            )
        except Exception as e:
            logger.error(
                "list_recent_failed",
                extra={"error": str(e)},
            )
            return ToolResult(
                success=True,
                data=[],
            )

    def _read_recent_entries(self, file_path: Path, count: int) -> list[dict[str, Any]]:
        """Read recent entries from a session file.

        Args:
            file_path: Path to the session JSONL file.
            count: Number of entries to return.

        Returns:
            List of entry dictionaries.
        """
        entries = []
        with open(file_path) as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        entries.append(
                            {
                                "entry_id": entry.get("entry_id"),
                                "command": entry.get("command"),
                                "exit_code": entry.get("exit_code"),
                                "duration_ms": entry.get("duration_ms"),
                                "timestamp": entry.get("timestamp"),
                            }
                        )
                    except json.JSONDecodeError:
                        continue

        # Return the last 'count' entries
        return entries[-count:]

    # =========================================================================
    # Command Execution
    # =========================================================================

    async def _execute_command(
        self,
        command: str,
        timeout: int,
        working_directory: str,
    ) -> dict[str, Any]:
        """Execute a bash command.

        Args:
            command: The command to execute.
            timeout: Timeout in seconds.
            working_directory: Directory to run the command in.

        Returns:
            Dictionary with stdout, stderr, exit_code, and timed_out.
        """
        # Build environment
        env = os.environ.copy() if self._plugin_config.execution.inherit_env else {}
        env.update(self._plugin_config.execution.env_overrides)

        shell = self._plugin_config.execution.shell

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_directory,
                env=env,
                executable=shell,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )

                return {
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "exit_code": process.returncode,
                    "timed_out": False,
                }

            except TimeoutError:
                # Kill the process on timeout
                process.kill()
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=5,  # Give it 5 seconds to die
                    )
                except TimeoutError:
                    stdout, stderr = b"", b""

                return {
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "exit_code": None,
                    "timed_out": True,
                }

        except OSError as e:
            raise ExecutionError(f"Failed to execute command: {e}") from e

    # =========================================================================
    # Recording
    # =========================================================================

    async def _record_execution(
        self,
        command: str,
        description: str | None,
        working_directory: str,
        timeout_seconds: int,
        duration_ms: int,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        timed_out: bool,
    ) -> str:
        """Record a command execution.

        Args:
            command: The executed command.
            description: Optional command description.
            working_directory: Directory the command ran in.
            timeout_seconds: Timeout that was set.
            duration_ms: Execution duration in milliseconds.
            exit_code: Command exit code.
            stdout: Command stdout.
            stderr: Command stderr.
            timed_out: Whether the command timed out.

        Returns:
            The recording entry ID.
        """
        if not self._session_manager or not self._recorder:
            raise RecordingError("Recording not initialized")

        entry_id = self._session_manager.get_next_entry_id()

        # Truncate output if necessary
        max_size = self._plugin_config.recording.max_output_size
        stdout_truncated = len(stdout.encode("utf-8")) > max_size
        stderr_truncated = len(stderr.encode("utf-8")) > max_size

        original_stdout_bytes = len(stdout.encode("utf-8"))
        original_stderr_bytes = len(stderr.encode("utf-8"))

        recorded_stdout: str | None = None
        recorded_stderr: str | None = None

        if self._plugin_config.recording.capture_output:
            if stdout_truncated:
                recorded_stdout = stdout.encode("utf-8")[:max_size].decode(
                    "utf-8", errors="replace"
                )
            else:
                recorded_stdout = stdout

            if stderr_truncated:
                recorded_stderr = stderr.encode("utf-8")[:max_size].decode(
                    "utf-8", errors="replace"
                )
            else:
                recorded_stderr = stderr

        output_truncated = stdout_truncated or stderr_truncated
        output_truncated_bytes = (
            max(original_stdout_bytes, original_stderr_bytes)
            if output_truncated
            else None
        )

        # Capture environment if configured
        environment: dict[str, str] | None = None
        if self._plugin_config.recording.capture_env:
            environment = {
                k: v
                for k, v in os.environ.items()
                if k in self._plugin_config.recording.env_allowlist
            }

        entry = RecordingEntry(
            entry_id=entry_id,
            session_id=self._session_manager.current_session_id,
            sequence_number=self._session_manager.entry_count,
            timestamp=datetime.now(UTC),
            duration_ms=duration_ms,
            command=command,
            description=description,
            working_directory=working_directory,
            shell=self._plugin_config.execution.shell,
            timeout_seconds=timeout_seconds,
            timed_out=timed_out,
            exit_code=exit_code,
            stdout=recorded_stdout,
            stderr=recorded_stderr,
            output_truncated=output_truncated,
            output_truncated_bytes=output_truncated_bytes,
            environment=environment,
            opencuff_version=OPENCUFF_VERSION,
            plugin_version=PLUGIN_VERSION,
        )

        await self._recorder.write_entry(entry)

        # Record result in session manager
        self._session_manager.record_command_result(
            duration_ms=duration_ms,
            exit_code=exit_code,
            timed_out=timed_out,
        )

        return entry_id

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _ensure_recording_directory(self) -> None:
        """Ensure the recording directory exists with proper permissions."""
        recordings_dir = self._plugin_config.recording.directory
        sessions_dir = recordings_dir / "sessions"

        if not sessions_dir.exists():
            sessions_dir.mkdir(parents=True, mode=0o700)

    def _check_directory_writable(self) -> bool:
        """Check if the recording directory is writable.

        Returns:
            True if writable, False otherwise.
        """
        sessions_dir = self._plugin_config.recording.directory / "sessions"

        if not sessions_dir.exists():
            try:
                sessions_dir.mkdir(parents=True, mode=0o700)
                return True
            except OSError:
                return False

        # Try to write a test file
        test_file = sessions_dir / ".write_test"
        try:
            test_file.write_text("test")
            test_file.unlink()
            return True
        except OSError:
            return False

    def _check_disk_space(self, min_bytes: int = 10_000_000) -> bool:
        """Check if there's sufficient disk space.

        Uses shutil.disk_usage() for cross-platform compatibility.

        Args:
            min_bytes: Minimum required bytes (default: 10MB).

        Returns:
            True if sufficient space, False otherwise.
        """
        try:
            recordings_dir = self._plugin_config.recording.directory
            if not recordings_dir.exists():
                recordings_dir = recordings_dir.parent

            usage = shutil.disk_usage(recordings_dir)
            return usage.free >= min_bytes
        except OSError:
            # If we can't check, assume it's OK
            return True


# Alias for compatibility
BashRecorderPlugin = Plugin
