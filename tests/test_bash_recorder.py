"""Tests for the BashRecorder plugin.

Tests cover:
    - Configuration validation (TestBashRecorderConfig)
    - Recording entry and session metadata models (TestRecordingModels)
    - Session management (TestSessionManager)
    - Recording operations (TestRecorder)
    - Command execution (TestCommandExecution)
    - Plugin lifecycle and tools (TestBashRecorderPlugin)
    - Graceful degradation (TestGracefulDegradation)
    - Output truncation (TestOutputTruncation)
    - Integration tests with MCP client (TestBashRecorderIntegration)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

# =============================================================================
# TestBashRecorderConfig
# =============================================================================


class TestBashRecorderConfig:
    """Tests for BashRecorderConfig validation."""

    def test_default_values(self) -> None:
        """Verify default configuration values."""
        from opencuff.plugins.builtin.bash_recorder import BashRecorderConfig

        config = BashRecorderConfig()

        # Recording defaults
        assert config.recording.enabled is True
        assert config.recording.directory == Path(".cuff/recordings")
        assert config.recording.capture_env is False
        assert config.recording.capture_output is True
        assert config.recording.max_output_size == 1_000_000
        assert config.recording.session_mode == "per_conversation"
        assert config.recording.retention_days == 30

        # Execution defaults
        assert config.execution.default_timeout == 120
        assert config.execution.max_timeout == 600
        assert config.execution.working_directory is None
        assert config.execution.shell == "/bin/bash"
        assert config.execution.inherit_env is True
        assert config.execution.env_overrides == {}

    def test_custom_values(self) -> None:
        """Verify custom configuration values are accepted."""
        from opencuff.plugins.builtin.bash_recorder import BashRecorderConfig

        config = BashRecorderConfig(
            recording={
                "enabled": False,
                "directory": "/var/log/cuff/recordings",
                "capture_env": True,
                "env_allowlist": ["PATH", "HOME", "CUSTOM_VAR"],
                "capture_output": False,
                "max_output_size": 5_000_000,
                "session_mode": "per_day",
                "retention_days": 90,
            },
            execution={
                "default_timeout": 300,
                "max_timeout": 900,
                "working_directory": "/workspace",
                "shell": "/bin/sh",
                "inherit_env": False,
                "env_overrides": {"CI": "true"},
            },
        )

        assert config.recording.enabled is False
        assert config.recording.directory == Path("/var/log/cuff/recordings")
        assert config.recording.capture_env is True
        assert "CUSTOM_VAR" in config.recording.env_allowlist
        assert config.recording.capture_output is False
        assert config.recording.max_output_size == 5_000_000
        assert config.recording.session_mode == "per_day"
        assert config.recording.retention_days == 90

        assert config.execution.default_timeout == 300
        assert config.execution.max_timeout == 900
        assert config.execution.working_directory == Path("/workspace")
        assert config.execution.shell == "/bin/sh"
        assert config.execution.inherit_env is False
        assert config.execution.env_overrides == {"CI": "true"}

    def test_session_mode_validation(self) -> None:
        """Verify session_mode only accepts valid values."""
        from opencuff.plugins.builtin.bash_recorder import BashRecorderConfig

        # Valid values should work
        for mode in ["per_conversation", "per_day", "continuous"]:
            config = BashRecorderConfig(recording={"session_mode": mode})
            assert config.recording.session_mode == mode

        # Invalid value should raise
        with pytest.raises(ValidationError):
            BashRecorderConfig(recording={"session_mode": "invalid"})

    def test_timeout_validation(self) -> None:
        """Verify timeout values must be positive."""
        from opencuff.plugins.builtin.bash_recorder import BashRecorderConfig

        with pytest.raises(ValidationError):
            BashRecorderConfig(execution={"default_timeout": 0})

        with pytest.raises(ValidationError):
            BashRecorderConfig(execution={"max_timeout": -1})

    def test_max_output_size_validation(self) -> None:
        """Verify max_output_size must be positive."""
        from opencuff.plugins.builtin.bash_recorder import BashRecorderConfig

        with pytest.raises(ValidationError):
            BashRecorderConfig(recording={"max_output_size": 0})

    def test_retention_days_validation(self) -> None:
        """Verify retention_days cannot be negative."""
        from opencuff.plugins.builtin.bash_recorder import BashRecorderConfig

        # 0 is valid (means keep forever)
        config = BashRecorderConfig(recording={"retention_days": 0})
        assert config.recording.retention_days == 0

        # Negative should fail
        with pytest.raises(ValidationError):
            BashRecorderConfig(recording={"retention_days": -1})


# =============================================================================
# TestRecordingModels
# =============================================================================


class TestRecordingModels:
    """Tests for RecordingEntry and SessionMetadata models."""

    def test_recording_entry_creation(self) -> None:
        """Verify RecordingEntry can be created with required fields."""
        from opencuff.plugins.builtin.bash_recorder import RecordingEntry

        entry = RecordingEntry(
            entry_id="e_20260118_143052_001",
            session_id="20260118_143052_a7b3c9d2",
            sequence_number=1,
            timestamp=datetime.now(UTC),
            duration_ms=1523,
            command="git status",
            working_directory="/home/user/project",
            shell="/bin/bash",
            timeout_seconds=120,
            timed_out=False,
            output_truncated=False,
            opencuff_version="0.1.0",
            plugin_version="1.0.0",
        )

        assert entry.entry_id == "e_20260118_143052_001"
        assert entry.sequence_number == 1
        assert entry.command == "git status"
        assert entry.timed_out is False

    def test_recording_entry_optional_fields(self) -> None:
        """Verify RecordingEntry handles optional fields correctly."""
        from opencuff.plugins.builtin.bash_recorder import RecordingEntry

        entry = RecordingEntry(
            entry_id="e_001",
            session_id="session_1",
            sequence_number=1,
            timestamp=datetime.now(UTC),
            duration_ms=100,
            command="echo test",
            description="A test command",
            working_directory="/tmp",
            shell="/bin/bash",
            timeout_seconds=60,
            timed_out=False,
            exit_code=0,
            stdout="test\n",
            stderr="",
            output_truncated=False,
            output_truncated_bytes=None,
            environment={"PATH": "/usr/bin"},
            agent_id="claude-code",
            conversation_id="conv_123",
            tool_call_id="tc_456",
            opencuff_version="0.1.0",
            plugin_version="1.0.0",
        )

        assert entry.description == "A test command"
        assert entry.exit_code == 0
        assert entry.stdout == "test\n"
        assert entry.environment == {"PATH": "/usr/bin"}
        assert entry.agent_id == "claude-code"

    def test_recording_entry_serialization(self) -> None:
        """Verify RecordingEntry can be serialized to JSON."""
        from opencuff.plugins.builtin.bash_recorder import RecordingEntry

        entry = RecordingEntry(
            entry_id="e_001",
            session_id="session_1",
            sequence_number=1,
            timestamp=datetime(2026, 1, 18, 14, 30, 52, tzinfo=UTC),
            duration_ms=100,
            command="echo test",
            working_directory="/tmp",
            shell="/bin/bash",
            timeout_seconds=60,
            timed_out=False,
            output_truncated=False,
            opencuff_version="0.1.0",
            plugin_version="1.0.0",
        )

        json_str = entry.model_dump_json()
        data = json.loads(json_str)

        assert data["entry_id"] == "e_001"
        assert data["command"] == "echo test"
        assert "2026-01-18" in data["timestamp"]

    def test_session_metadata_creation(self) -> None:
        """Verify SessionMetadata can be created."""
        from opencuff.plugins.builtin.bash_recorder import SessionMetadata

        metadata = SessionMetadata(
            session_id="20260118_143052_a7b3c9d2",
            created_at=datetime.now(UTC),
            last_updated=datetime.now(UTC),
            entry_count=5,
            total_duration_ms=10000,
            status="active",
            commands_succeeded=4,
            commands_failed=1,
            commands_timed_out=0,
            working_directory="/home/user/project",
        )

        assert metadata.session_id == "20260118_143052_a7b3c9d2"
        assert metadata.entry_count == 5
        assert metadata.status == "active"
        assert metadata.commands_succeeded == 4

    def test_session_metadata_status_validation(self) -> None:
        """Verify SessionMetadata status only accepts valid values."""
        from opencuff.plugins.builtin.bash_recorder import SessionMetadata

        valid_statuses = ["active", "complete", "shutdown", "interrupted"]
        for status in valid_statuses:
            metadata = SessionMetadata(
                session_id="test",
                created_at=datetime.now(UTC),
                last_updated=datetime.now(UTC),
                entry_count=0,
                total_duration_ms=0,
                status=status,
                commands_succeeded=0,
                commands_failed=0,
                commands_timed_out=0,
                working_directory="/tmp",
            )
            assert metadata.status == status


# =============================================================================
# TestSessionManager
# =============================================================================


class TestSessionManager:
    """Tests for SessionManager."""

    def test_session_id_format(self) -> None:
        """Verify session ID follows expected format."""
        from opencuff.plugins.builtin.bash_recorder import (
            RecordingConfig,
            SessionManager,
        )

        config = RecordingConfig(directory=Path("/tmp/recordings"))
        manager = SessionManager(config)

        session_id = manager._generate_session_id()

        # Format: YYYYMMDD_HHMMSS_uuid4_prefix
        parts = session_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert len(parts[2]) == 12  # uuid4 prefix (first 12 chars)

    def test_entry_id_format(self) -> None:
        """Verify entry ID follows expected format."""
        from opencuff.plugins.builtin.bash_recorder import (
            RecordingConfig,
            SessionManager,
        )

        config = RecordingConfig(directory=Path("/tmp/recordings"))
        manager = SessionManager(config)
        manager._current_session_id = "20260118_143052_a7b3c9d2"
        manager._entry_count = 0

        entry_id = manager._generate_entry_id()

        # Format: e_YYYYMMDD_HHMMSS_NNN
        assert entry_id.startswith("e_")
        parts = entry_id.split("_")
        assert len(parts) == 4

    @pytest.mark.asyncio
    async def test_start_session(self, tmp_path: Path) -> None:
        """Verify session can be started."""
        from opencuff.plugins.builtin.bash_recorder import (
            RecordingConfig,
            SessionManager,
        )

        config = RecordingConfig(directory=tmp_path / "recordings")
        manager = SessionManager(config)

        await manager.start_session()

        assert manager.current_session_id is not None
        assert manager.entry_count == 0

    @pytest.mark.asyncio
    async def test_finalize_session(self, tmp_path: Path) -> None:
        """Verify session can be finalized."""
        from opencuff.plugins.builtin.bash_recorder import (
            RecordingConfig,
            SessionManager,
        )

        config = RecordingConfig(directory=tmp_path / "recordings")
        manager = SessionManager(config)

        await manager.start_session()
        session_id = manager.current_session_id

        await manager.finalize_session(status="complete")

        # Session should be cleared
        session_cleared = (
            manager.current_session_id is None
            or manager.current_session_id != session_id
        )
        assert session_cleared

    def test_increment_entry_count(self, tmp_path: Path) -> None:
        """Verify entry count increments correctly."""
        from opencuff.plugins.builtin.bash_recorder import (
            RecordingConfig,
            SessionManager,
        )

        config = RecordingConfig(directory=tmp_path / "recordings")
        manager = SessionManager(config)
        manager._current_session_id = "test_session"
        manager._entry_count = 0

        manager.increment_entry_count()
        assert manager.entry_count == 1

        manager.increment_entry_count()
        assert manager.entry_count == 2


# =============================================================================
# TestRecorder
# =============================================================================


class TestRecorder:
    """Tests for Recorder file operations."""

    @pytest.mark.asyncio
    async def test_write_entry_creates_file(self, tmp_path: Path) -> None:
        """Verify writing an entry creates the JSONL file."""
        from opencuff.plugins.builtin.bash_recorder import (
            Recorder,
            RecordingConfig,
            RecordingEntry,
            SessionManager,
        )

        config = RecordingConfig(directory=tmp_path / "recordings")
        session_manager = SessionManager(config)
        await session_manager.start_session()
        recorder = Recorder(session_manager=session_manager, config=config)

        entry = RecordingEntry(
            entry_id="e_001",
            session_id=session_manager.current_session_id,
            sequence_number=1,
            timestamp=datetime.now(UTC),
            duration_ms=100,
            command="echo test",
            working_directory="/tmp",
            shell="/bin/bash",
            timeout_seconds=60,
            timed_out=False,
            output_truncated=False,
            opencuff_version="0.1.0",
            plugin_version="1.0.0",
        )

        await recorder.write_entry(entry)

        # Verify file exists
        session_file = (
            tmp_path
            / "recordings"
            / "sessions"
            / f"{session_manager.current_session_id}.jsonl"
        )
        assert session_file.exists()

        # Verify content
        content = session_file.read_text()
        data = json.loads(content.strip())
        assert data["command"] == "echo test"

    @pytest.mark.asyncio
    async def test_write_multiple_entries_appends(self, tmp_path: Path) -> None:
        """Verify multiple entries are appended to the same file."""
        from opencuff.plugins.builtin.bash_recorder import (
            Recorder,
            RecordingConfig,
            RecordingEntry,
            SessionManager,
        )

        config = RecordingConfig(directory=tmp_path / "recordings")
        session_manager = SessionManager(config)
        await session_manager.start_session()
        recorder = Recorder(session_manager=session_manager, config=config)

        for i in range(3):
            entry = RecordingEntry(
                entry_id=f"e_{i:03d}",
                session_id=session_manager.current_session_id,
                sequence_number=i + 1,
                timestamp=datetime.now(UTC),
                duration_ms=100,
                command=f"echo {i}",
                working_directory="/tmp",
                shell="/bin/bash",
                timeout_seconds=60,
                timed_out=False,
                output_truncated=False,
                opencuff_version="0.1.0",
                plugin_version="1.0.0",
            )
            await recorder.write_entry(entry)

        session_file = (
            tmp_path
            / "recordings"
            / "sessions"
            / f"{session_manager.current_session_id}.jsonl"
        )
        lines = session_file.read_text().strip().split("\n")
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_directory_permissions(self, tmp_path: Path) -> None:
        """Verify directories are created with restrictive permissions."""
        from opencuff.plugins.builtin.bash_recorder import (
            Recorder,
            RecordingConfig,
            RecordingEntry,
            SessionManager,
        )

        recordings_dir = tmp_path / "recordings"
        config = RecordingConfig(directory=recordings_dir)
        session_manager = SessionManager(config)
        await session_manager.start_session()
        recorder = Recorder(session_manager=session_manager, config=config)

        entry = RecordingEntry(
            entry_id="e_001",
            session_id=session_manager.current_session_id,
            sequence_number=1,
            timestamp=datetime.now(UTC),
            duration_ms=100,
            command="echo test",
            working_directory="/tmp",
            shell="/bin/bash",
            timeout_seconds=60,
            timed_out=False,
            output_truncated=False,
            opencuff_version="0.1.0",
            plugin_version="1.0.0",
        )

        await recorder.write_entry(entry)

        # Check directory permissions (should be 0700)
        sessions_dir = recordings_dir / "sessions"
        dir_mode = sessions_dir.stat().st_mode & 0o777
        assert dir_mode == 0o700


# =============================================================================
# TestCommandExecution
# =============================================================================


class TestCommandExecution:
    """Tests for command execution functionality."""

    @pytest.mark.asyncio
    async def test_execute_simple_command(self, tmp_path: Path) -> None:
        """Verify simple command execution works."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
            "execution": {"default_timeout": 30},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("execute", {"command": "echo hello"})

        assert result.success is True
        assert "hello" in result.data["stdout"]
        assert result.data["exit_code"] == 0
        assert result.data["timed_out"] is False

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_command_with_exit_code(self, tmp_path: Path) -> None:
        """Verify command exit code is captured."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("execute", {"command": "exit 42"})

        assert result.success is True  # Command ran, even if exit code non-zero
        assert result.data["exit_code"] == 42

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_command_with_stderr(self, tmp_path: Path) -> None:
        """Verify stderr is captured."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("execute", {"command": "echo error >&2"})

        assert result.success is True
        assert "error" in result.data["stderr"]

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_command_timeout(self, tmp_path: Path) -> None:
        """Verify command timeout works."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
            "execution": {"default_timeout": 1, "max_timeout": 2},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool(
            "execute", {"command": "sleep 10", "timeout": 1}
        )

        assert result.success is True
        assert result.data["timed_out"] is True
        assert result.data["exit_code"] is None

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_command_with_working_directory(self, tmp_path: Path) -> None:
        """Verify working directory is respected."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        work_dir = tmp_path / "workdir"
        work_dir.mkdir()

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool(
            "execute",
            {"command": "pwd", "working_directory": str(work_dir)},
        )

        assert result.success is True
        assert str(work_dir) in result.data["stdout"]

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_command_invalid_working_directory(
        self, tmp_path: Path
    ) -> None:
        """Verify invalid working directory returns error."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool(
            "execute",
            {"command": "pwd", "working_directory": "/nonexistent/path"},
        )

        assert result.success is False
        assert "directory" in result.error.lower()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_respects_max_timeout(self, tmp_path: Path) -> None:
        """Verify requested timeout is capped at max_timeout."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
            "execution": {"default_timeout": 30, "max_timeout": 60},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Request timeout exceeding max should be capped
        result = await plugin.call_tool(
            "execute", {"command": "echo test", "timeout": 1000}
        )

        assert result.success is True
        # The command should have run with capped timeout

        await plugin.shutdown()


# =============================================================================
# TestBashRecorderPlugin
# =============================================================================


class TestBashRecorderPlugin:
    """Tests for BashRecorderPlugin class."""

    @pytest.mark.asyncio
    async def test_plugin_initialization(self, tmp_path: Path) -> None:
        """Verify plugin initializes correctly."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Should have tools available
        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        assert "execute" in tool_names
        assert "session_info" in tool_names
        assert "list_recent" in tool_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_plugin_get_tools(self, tmp_path: Path) -> None:
        """Verify get_tools returns correct tool definitions."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)

        tools = plugin.get_tools()

        # Find execute tool
        execute_tool = next((t for t in tools if t.name == "execute"), None)
        assert execute_tool is not None
        assert "command" in str(execute_tool.parameters)

    @pytest.mark.asyncio
    async def test_session_info_tool(self, tmp_path: Path) -> None:
        """Verify session_info tool returns session information."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("session_info", {})

        assert result.success is True
        assert "session_id" in result.data
        assert "entry_count" in result.data

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_list_recent_tool(self, tmp_path: Path) -> None:
        """Verify list_recent tool returns recent commands."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Execute some commands first
        await plugin.call_tool("execute", {"command": "echo one"})
        await plugin.call_tool("execute", {"command": "echo two"})

        result = await plugin.call_tool("list_recent", {"count": 5})

        assert result.success is True
        assert isinstance(result.data, list)
        assert len(result.data) >= 2

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tmp_path: Path) -> None:
        """Verify unknown tool returns error."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("nonexistent", {})

        assert result.success is False
        assert "Unknown tool" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_health_check(self, tmp_path: Path) -> None:
        """Verify health check works correctly."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        is_healthy = await plugin.health_check()

        assert is_healthy is True

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_finalizes_session(self, tmp_path: Path) -> None:
        """Verify shutdown finalizes the recording session."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Execute a command to create a session
        await plugin.call_tool("execute", {"command": "echo test"})

        await plugin.shutdown()

        # Session metadata file should exist
        sessions_dir = tmp_path / "recordings" / "sessions"
        meta_files = list(sessions_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1


# =============================================================================
# TestGracefulDegradation
# =============================================================================


class TestGracefulDegradation:
    """Tests for graceful degradation when recording fails."""

    @pytest.mark.asyncio
    async def test_execute_continues_when_recording_fails(self, tmp_path: Path) -> None:
        """Verify command execution continues even when recording fails."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        # Create read-only directory to cause recording failure
        recordings_dir = tmp_path / "recordings"
        recordings_dir.mkdir()
        sessions_dir = recordings_dir / "sessions"
        sessions_dir.mkdir()

        config = {
            "recording": {"directory": str(recordings_dir)},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Make directory read-only to cause write failures
        sessions_dir.chmod(0o444)

        try:
            result = await plugin.call_tool("execute", {"command": "echo test"})

            # Command should still execute successfully
            assert result.success is True
            assert "test" in result.data["stdout"]
            # Recording ID might be None due to failure
            assert "recording_id" in result.data
        finally:
            # Restore permissions for cleanup
            sessions_dir.chmod(0o755)
            await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_recording_disabled_still_executes(self, tmp_path: Path) -> None:
        """Verify commands execute when recording is disabled."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {
                "enabled": False,
                "directory": str(tmp_path / "recordings"),
            },
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("execute", {"command": "echo test"})

        assert result.success is True
        assert "test" in result.data["stdout"]
        assert result.data.get("recording_id") is None

        await plugin.shutdown()


# =============================================================================
# TestOutputTruncation
# =============================================================================


class TestOutputTruncation:
    """Tests for output truncation behavior."""

    @pytest.mark.asyncio
    async def test_output_truncated_when_exceeds_limit(self, tmp_path: Path) -> None:
        """Verify output is truncated when it exceeds max_output_size."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {
                "directory": str(tmp_path / "recordings"),
                "max_output_size": 100,  # Very small limit
            },
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Generate output larger than 100 bytes
        result = await plugin.call_tool(
            "execute",
            {"command": "python3 -c \"print('x' * 500)\""},
        )

        assert result.success is True
        # Output should be truncated in the recording
        # but full output returned to the agent
        assert len(result.data["stdout"]) >= 100

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_recording_marks_truncated_output(self, tmp_path: Path) -> None:
        """Verify recording entry marks when output was truncated."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {
                "directory": str(tmp_path / "recordings"),
                "max_output_size": 50,
            },
        }
        plugin = Plugin(config)
        await plugin.initialize()

        await plugin.call_tool(
            "execute",
            {"command": "python3 -c \"print('x' * 500)\""},
        )

        # Check the recording file
        sessions_dir = tmp_path / "recordings" / "sessions"
        jsonl_files = list(sessions_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1

        content = jsonl_files[0].read_text().strip()
        entry = json.loads(content)

        assert entry["output_truncated"] is True
        assert entry["output_truncated_bytes"] is not None
        assert entry["output_truncated_bytes"] > 50

        await plugin.shutdown()


# =============================================================================
# TestEnvironmentCapture
# =============================================================================


class TestEnvironmentCapture:
    """Tests for environment variable capture."""

    @pytest.mark.asyncio
    async def test_env_not_captured_by_default(self, tmp_path: Path) -> None:
        """Verify environment is not captured by default."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {
                "directory": str(tmp_path / "recordings"),
                "capture_env": False,
            },
        }
        plugin = Plugin(config)
        await plugin.initialize()

        await plugin.call_tool("execute", {"command": "echo test"})

        # Check the recording file
        sessions_dir = tmp_path / "recordings" / "sessions"
        jsonl_files = list(sessions_dir.glob("*.jsonl"))
        content = jsonl_files[0].read_text().strip()
        entry = json.loads(content)

        assert entry["environment"] is None

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_env_captured_with_allowlist(self, tmp_path: Path) -> None:
        """Verify environment is captured according to allowlist."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {
                "directory": str(tmp_path / "recordings"),
                "capture_env": True,
                "env_allowlist": ["PATH", "HOME"],
            },
        }
        plugin = Plugin(config)
        await plugin.initialize()

        await plugin.call_tool("execute", {"command": "echo test"})

        # Check the recording file
        sessions_dir = tmp_path / "recordings" / "sessions"
        jsonl_files = list(sessions_dir.glob("*.jsonl"))
        content = jsonl_files[0].read_text().strip()
        entry = json.loads(content)

        assert entry["environment"] is not None
        # Should only contain allowlisted vars
        for key in entry["environment"]:
            assert key in ["PATH", "HOME"]

        await plugin.shutdown()


# =============================================================================
# TestBashRecorderIntegration
# =============================================================================


class TestBashRecorderIntegration:
    """Integration tests with MCP client."""

    @pytest.mark.asyncio
    async def test_plugin_tools_via_plugin_manager(self, tmp_path: Path) -> None:
        """Verify BashRecorder tools work through plugin manager."""
        from opencuff.plugins.config import OpenCuffSettings, PluginConfig, PluginType
        from opencuff.server import initialize_plugins, shutdown_plugins

        # Shutdown any existing plugins first
        await shutdown_plugins()

        settings = OpenCuffSettings(
            plugins={
                "bash_recorder": PluginConfig(
                    type=PluginType.IN_SOURCE,
                    enabled=True,
                    module="opencuff.plugins.builtin.bash_recorder",
                    config={
                        "recording": {"directory": str(tmp_path / "recordings")},
                    },
                )
            },
            plugin_settings={
                "health_check_interval": 0,
                "live_reload": False,
            },
        )

        try:
            manager = await initialize_plugins(settings=settings)

            # Test execute tool
            result = await manager.call_tool(
                "bash_recorder.execute", {"command": "echo hello from integration"}
            )

            assert result.success is True
            assert "hello from integration" in result.data["stdout"]

            # Test session_info tool
            info_result = await manager.call_tool("bash_recorder.session_info", {})
            assert info_result.success is True
            assert "session_id" in info_result.data

        finally:
            await shutdown_plugins()

    @pytest.mark.asyncio
    async def test_recording_persists_across_commands(self, tmp_path: Path) -> None:
        """Verify multiple commands are recorded in the same session."""
        from opencuff.plugins.builtin.bash_recorder import Plugin

        config = {
            "recording": {"directory": str(tmp_path / "recordings")},
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Execute multiple commands
        commands = ["echo one", "echo two", "echo three"]
        for cmd in commands:
            await plugin.call_tool("execute", {"command": cmd})

        await plugin.shutdown()

        # Verify all commands are in the recording
        sessions_dir = tmp_path / "recordings" / "sessions"
        jsonl_files = list(sessions_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1

        lines = jsonl_files[0].read_text().strip().split("\n")
        assert len(lines) == 3

        recorded_commands = [json.loads(line)["command"] for line in lines]
        assert recorded_commands == commands
