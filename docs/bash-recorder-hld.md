# BashRecorder Plugin - High-Level Design

| Field | Value |
|-------|-------|
| **Version** | 1.0 |
| **Date** | 2026-01-18 |
| **Status** | Draft |
| **Author** | OpenCuff Team |

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-18 | OpenCuff Team | Initial draft |
| 1.1 | 2026-01-18 | OpenCuff Team | Added plugin interface, observability, security clarifications |

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [Architecture Overview](#3-architecture-overview)
4. [Plugin Interface](#4-plugin-interface)
5. [Configuration Schema](#5-configuration-schema)
6. [Recording Format](#6-recording-format)
7. [Tool Interface](#7-tool-interface)
8. [CLI Commands](#8-cli-commands)
9. [Security Considerations](#9-security-considerations)
10. [Observability](#10-observability)
11. [Error Handling](#11-error-handling)
12. [Example Configurations](#12-example-configurations)
13. [Future Considerations](#13-future-considerations)

---

## 1. Executive Summary

### 1.1 Purpose

The BashRecorder plugin provides a governed bash execution tool that records all commands executed by AI agents. Unlike standard bash tools, BashRecorder maintains an audit trail of all agent actions, enabling:

- **Audit & Compliance**: Track what commands agents execute
- **Policy Generation**: Automatically create allowlists from recorded sessions
- **Debugging**: Replay and analyze agent behavior
- **Security Analysis**: Identify potentially risky command patterns

### 1.2 Overview

BashRecorder acts as a drop-in replacement for standard bash tools while transparently recording:

- Command text
- Working directory
- Environment variables (configurable)
- Exit codes
- stdout/stderr output
- Timestamps and duration
- Session context (agent ID, conversation ID if available)

Recordings are stored in `.cuff/recordings/` by default, with each session creating a structured JSON file that can be analyzed, replayed, or converted to policy rules.

---

## 2. Goals and Non-Goals

### 2.1 Goals

| Goal | Description |
|------|-------------|
| **G1** | Provide bash execution equivalent to agent built-in tools |
| **G2** | Record all executed commands with full context |
| **G3** | Support configurable recording granularity |
| **G4** | Enable policy generation from recordings |
| **G5** | Provide CLI tools for recording management |
| **G6** | Maintain backward compatibility with standard bash interfaces |
| **G7** | Support session-based recording organization |

### 2.2 Non-Goals

| Non-Goal | Rationale |
|----------|-----------|
| **NG1** | Real-time command blocking (future enhancement) |
| **NG2** | Full terminal emulation (PTY allocation) |
| **NG3** | Interactive command support (stdin interaction) |
| **NG4** | Recording encryption at rest (can be added later) |
| **NG5** | Remote recording storage (local filesystem only) |

---

## 3. Architecture Overview

### 3.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AI Agent (Claude, etc.)                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          OpenCuff MCP Server                         │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                     BashRecorder Plugin                        │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐   │  │
│  │  │   Executor  │  │  Recorder   │  │  Session Manager    │   │  │
│  │  │             │  │             │  │                     │   │  │
│  │  │ - Run cmd   │  │ - Capture   │  │ - Track sessions    │   │  │
│  │  │ - Timeout   │  │ - Serialize │  │ - Generate IDs      │   │  │
│  │  │ - Sandbox   │  │ - Store     │  │ - Manage lifecycle  │   │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘   │  │
│  │         │                │                     │              │  │
│  │         └────────────────┼─────────────────────┘              │  │
│  │                          │                                     │  │
│  │                          ▼                                     │  │
│  │              ┌───────────────────────┐                        │  │
│  │              │    Recording Store    │                        │  │
│  │              │  .cuff/recordings/    │                        │  │
│  │              └───────────────────────┘                        │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          CLI Interface                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐ │
│  │ cuff record  │ │ cuff record  │ │ cuff record  │ │cuff record │ │
│  │    list      │ │    show      │ │    stats     │ │  generate  │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Data Flow

```
┌─────────┐    execute_bash     ┌──────────┐
│  Agent  │ ──────────────────► │ Executor │
└─────────┘                     └────┬─────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            ┌───────────┐    ┌───────────┐    ┌───────────┐
            │  Run Cmd  │    │  Capture  │    │  Record   │
            │ subprocess│    │  Output   │    │  Entry    │
            └─────┬─────┘    └─────┬─────┘    └─────┬─────┘
                  │                │                │
                  └────────────────┼────────────────┘
                                   ▼
                           ┌───────────────┐
                           │ Recording File│
                           │    (JSON)     │
                           └───────┬───────┘
                                   │
                                   ▼
                           ┌───────────────┐
                           │ Tool Result   │
                           │ (to Agent)    │
                           └───────────────┘
```

### 3.3 Core Components

| Component | Responsibility |
|-----------|----------------|
| **Executor** | Run bash commands via subprocess, handle timeouts, capture output |
| **Recorder** | Serialize command execution data, manage recording files |
| **SessionManager** | Track recording sessions, generate unique IDs, handle session lifecycle |
| **RecordingStore** | File I/O for recordings, directory management, rotation |

---

## 4. Plugin Interface

### 4.1 InSourcePlugin Implementation

The BashRecorder plugin implements the `InSourcePlugin` abstract base class from OpenCuff's plugin system.

```python
from opencuff.plugins.base import InSourcePlugin, ToolDefinition, ToolResult
from typing import Any


class BashRecorderPlugin(InSourcePlugin):
    """Bash execution with recording for audit and policy generation."""

    PLUGIN_VERSION = "1.0.0"

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize plugin with configuration.

        Args:
            config: Plugin configuration dictionary
        """
        self._config = BashRecorderConfig.model_validate(config)
        self._session_manager: SessionManager | None = None
        self._recorder: Recorder | None = None
        self._recording_enabled: bool = True

    async def initialize(self) -> None:
        """Initialize plugin resources.

        - Create recording directory if it doesn't exist
        - Initialize session manager
        - Start new recording session
        """
        if self._config.recording.enabled:
            self._ensure_recording_directory()
            self._session_manager = SessionManager(self._config.recording)
            self._recorder = Recorder(
                session_manager=self._session_manager,
                config=self._config.recording,
            )
            await self._session_manager.start_session()

    async def shutdown(self) -> None:
        """Clean up plugin resources.

        - Finalize current recording session
        - Flush any pending writes
        - Update session metadata with final state
        """
        if self._session_manager:
            await self._session_manager.finalize_session(
                status="shutdown"
            )

    async def health_check(self) -> dict[str, Any]:
        """Check plugin health status.

        Returns:
            Health status dictionary with:
            - healthy: bool
            - recording_enabled: bool
            - directory_writable: bool
            - disk_space_ok: bool
            - current_session: str | None
            - entries_recorded: int
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
                if self._session_manager else None
            ),
            "entries_recorded": (
                self._session_manager.entry_count
                if self._session_manager else 0
            ),
        }

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration changes.

        - Validate new configuration
        - If recording directory changes, start new session
        - If recording is disabled, finalize current session
        - Update execution settings immediately

        Args:
            new_config: New configuration dictionary
        """
        old_config = self._config
        self._config = BashRecorderConfig.model_validate(new_config)

        # Handle recording directory change
        if self._config.recording.directory != old_config.recording.directory:
            if self._session_manager:
                await self._session_manager.finalize_session(
                    status="config_reload"
                )
            self._ensure_recording_directory()
            await self._session_manager.start_session()

        # Handle recording enable/disable
        if not self._config.recording.enabled and old_config.recording.enabled:
            if self._session_manager:
                await self._session_manager.finalize_session(
                    status="recording_disabled"
                )

    def get_tools(self) -> list[ToolDefinition]:
        """Return available tools.

        Tools are namespaced as `bash_recorder.{tool_name}` when registered.

        Returns:
            List of tool definitions
        """
        return [
            self._get_execute_tool_definition(),
            self._get_session_info_tool_definition(),
            self._get_list_recent_tool_definition(),
        ]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool (without namespace prefix)
            arguments: Tool arguments

        Returns:
            ToolResult with execution outcome
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
```

### 4.2 Tool Namespacing

Per OpenCuff's plugin namespacing convention, tools are registered with the prefix `bash_recorder.`:

| Internal Name | Registered Name |
|---------------|-----------------|
| `execute` | `bash_recorder.execute` |
| `session_info` | `bash_recorder.session_info` |
| `list_recent` | `bash_recorder.list_recent` |

### 4.3 Agent Context Extraction

The plugin extracts agent context from MCP request metadata when available:

```python
def _extract_agent_context(self, mcp_context: dict[str, Any] | None) -> AgentContext:
    """Extract agent context from MCP request metadata.

    The MCP protocol may include metadata in the request that identifies
    the calling agent and conversation. This is used for session grouping
    and audit trails.

    Args:
        mcp_context: MCP request context (may be None)

    Returns:
        AgentContext with extracted or default values
    """
    if not mcp_context:
        return AgentContext()

    return AgentContext(
        agent_id=mcp_context.get("agent_id"),
        conversation_id=mcp_context.get("conversation_id"),
        tool_call_id=mcp_context.get("tool_call_id"),
    )
```

---

## 5. Configuration Schema

### 5.1 Pydantic Model

```python
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Literal


class RecordingConfig(BaseModel):
    """Recording-specific configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable/disable recording"
    )

    directory: Path = Field(
        default=Path(".cuff/recordings"),
        description="Directory to store recordings"
    )

    capture_env: bool = Field(
        default=False,
        description="Record environment variables (security sensitive)"
    )

    env_allowlist: list[str] = Field(
        default=["PATH", "HOME", "USER", "SHELL", "PWD"],
        description="Environment variables to capture when capture_env=True"
    )

    capture_output: bool = Field(
        default=True,
        description="Record stdout/stderr output"
    )

    max_output_size: int = Field(
        default=1_000_000,  # 1MB
        description="Maximum output size to capture per command (bytes)"
    )

    session_mode: Literal["per_conversation", "per_day", "continuous"] = Field(
        default="per_conversation",
        description=(
            "How to group recordings into sessions. "
            "per_conversation: New session per conversation_id (falls back to per_day if unavailable). "
            "per_day: New session each calendar day. "
            "continuous: Single session until server restart."
        )
    )

    retention_days: int = Field(
        default=30,
        description="Days to retain recordings (0 = forever)"
    )


class ExecutionConfig(BaseModel):
    """Execution-specific configuration."""

    default_timeout: int = Field(
        default=120,
        description="Default command timeout in seconds"
    )

    max_timeout: int = Field(
        default=600,
        description="Maximum allowed timeout in seconds"
    )

    working_directory: Path | None = Field(
        default=None,
        description="Default working directory (None = current directory)"
    )

    shell: str = Field(
        default="/bin/bash",
        description="Shell to use for execution"
    )

    inherit_env: bool = Field(
        default=True,
        description="Inherit parent process environment"
    )

    env_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set/override"
    )


class BashRecorderConfig(BaseModel):
    """Root configuration for BashRecorder plugin."""

    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
```

### 5.2 YAML Configuration Example

```yaml
plugins:
  bash_recorder:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.bash_recorder
    config:
      recording:
        enabled: true
        directory: .cuff/recordings
        capture_env: false
        env_allowlist:
          - PATH
          - HOME
          - USER
          - SHELL
          - PWD
          - VIRTUAL_ENV
        capture_output: true
        max_output_size: 1000000
        session_mode: per_conversation
        retention_days: 30

      execution:
        default_timeout: 120
        max_timeout: 600
        working_directory: null
        shell: /bin/bash
        inherit_env: true
        env_overrides:
          CI: "true"
```

---

## 6. Recording Format

### 6.1 Recording File Structure

Recordings are stored as JSONL (JSON Lines) files, with one JSON object per command execution. This allows efficient append operations and streaming reads.

**File naming convention:**
```
.cuff/recordings/{session_id}.jsonl
```

**Session ID format:**
```
{YYYYMMDD}_{HHMMSS}_{uuid4_prefix}

Example: 20260118_143052_a7b3c9d2-e4f5

The uuid4_prefix is the first 12 characters of a UUID4, providing sufficient
entropy to avoid collisions even in high-throughput scenarios.
```

### 6.2 Recording Entry Schema

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Any


class RecordingEntry(BaseModel):
    """Single command execution record."""

    # Identity
    entry_id: str                    # Unique ID for this entry
    session_id: str                  # Parent session ID
    sequence_number: int             # Order within session (1-indexed)

    # Timing
    timestamp: datetime              # When command started
    duration_ms: int                 # Execution duration in milliseconds

    # Command
    command: str                     # The command that was executed
    description: str | None          # User-provided command description
    working_directory: str           # Directory command ran in
    shell: str                       # Shell used (e.g., /bin/bash)

    # Execution Context
    timeout_seconds: int             # Timeout that was set
    timed_out: bool                  # Whether command timed out

    # Results
    exit_code: int | None            # Exit code (None if timed out/killed)
    stdout: str | None               # Captured stdout (if enabled)
    stderr: str | None               # Captured stderr (if enabled)
    output_truncated: bool           # Whether output was truncated
    output_truncated_bytes: int | None  # Original size if truncated

    # Environment (optional)
    environment: dict[str, str] | None  # Captured env vars (if enabled)

    # Agent Context (if available)
    agent_id: str | None             # Agent identifier
    conversation_id: str | None      # Conversation/session identifier
    tool_call_id: str | None         # MCP tool call ID

    # Metadata
    opencuff_version: str            # OpenCuff version
    plugin_version: str              # BashRecorder plugin version


class SessionMetadata(BaseModel):
    """Session-level metadata stored in separate file."""

    session_id: str
    created_at: datetime
    last_updated: datetime
    entry_count: int
    total_duration_ms: int
    status: Literal["active", "complete", "shutdown", "interrupted"]

    # Aggregates
    commands_succeeded: int
    commands_failed: int
    commands_timed_out: int

    # Context
    working_directory: str           # Initial working directory
    agent_id: str | None
    conversation_id: str | None
```

### 6.3 Index File Schema

The `index.json` file provides quick lookup without scanning all session files:

```python
class SessionIndexEntry(BaseModel):
    """Index entry for a single session."""

    session_id: str
    created_at: datetime
    last_updated: datetime
    entry_count: int
    status: Literal["active", "complete", "shutdown", "interrupted"]
    file_size_bytes: int


class RecordingIndex(BaseModel):
    """Quick lookup index for all sessions."""

    version: str = "1.0"
    last_updated: datetime
    total_sessions: int
    total_entries: int
    sessions: dict[str, SessionIndexEntry]  # Keyed by session_id
```

**Example `index.json`:**
```json
{
  "version": "1.0",
  "last_updated": "2026-01-18T17:30:00Z",
  "total_sessions": 3,
  "total_entries": 26,
  "sessions": {
    "20260118_143052_a7b3c9d2-e4f5": {
      "session_id": "20260118_143052_a7b3c9d2-e4f5",
      "created_at": "2026-01-18T14:30:52Z",
      "last_updated": "2026-01-18T14:45:00Z",
      "entry_count": 15,
      "status": "complete",
      "file_size_bytes": 45678
    },
    "20260118_160315_b8c4d0e3-f6a7": {
      "session_id": "20260118_160315_b8c4d0e3-f6a7",
      "created_at": "2026-01-18T16:03:15Z",
      "last_updated": "2026-01-18T16:15:00Z",
      "entry_count": 8,
      "status": "complete",
      "file_size_bytes": 23456
    },
    "20260118_171522_c9d5e1f4-g8b9": {
      "session_id": "20260118_171522_c9d5e1f4-g8b9",
      "created_at": "2026-01-18T17:15:22Z",
      "last_updated": "2026-01-18T17:30:00Z",
      "entry_count": 3,
      "status": "active",
      "file_size_bytes": 8901
    }
  }
}
```

### 6.4 Directory Structure

```
.cuff/
└── recordings/
    ├── sessions/
    │   ├── 20260118_143052_a7b3c9d2-e4f5.jsonl     # Recording entries
    │   ├── 20260118_143052_a7b3c9d2-e4f5.meta.json # Session metadata
    │   ├── 20260118_160315_b8c4d0e3-f6a7.jsonl
    │   └── 20260118_160315_b8c4d0e3-f6a7.meta.json
    └── index.json                                   # Quick lookup index
```

### 6.5 Atomic Writes and Crash Recovery

Recording writes use an atomic pattern to prevent corruption:

```python
def _append_entry_atomic(self, entry: RecordingEntry) -> None:
    """Append entry atomically using write-then-flush pattern.

    JSONL format provides natural crash recovery: readers should handle
    truncated last lines gracefully by discarding incomplete entries.
    """
    entry_json = entry.model_dump_json() + "\n"

    with open(self.session_file, "a") as f:
        f.write(entry_json)
        f.flush()
        os.fsync(f.fileno())  # Ensure write reaches disk
```

**Crash Recovery:**
- JSONL readers should discard truncated final lines
- Index file is rebuilt on startup if corrupted
- Session metadata includes last known good entry count

### 6.6 Example Recording Entry

```json
{
  "entry_id": "e_20260118_143052_001",
  "session_id": "20260118_143052_a7b3c9d2-e4f5",
  "sequence_number": 1,
  "timestamp": "2026-01-18T14:30:52.123456Z",
  "duration_ms": 1523,
  "command": "git status",
  "description": "Check working tree status",
  "working_directory": "/Users/dev/myproject",
  "shell": "/bin/bash",
  "timeout_seconds": 120,
  "timed_out": false,
  "exit_code": 0,
  "stdout": "On branch main\nYour branch is up to date with 'origin/main'.\n\nnothing to commit, working tree clean\n",
  "stderr": "",
  "output_truncated": false,
  "output_truncated_bytes": null,
  "environment": null,
  "agent_id": "claude-code",
  "conversation_id": "conv_abc123",
  "tool_call_id": "tc_xyz789",
  "opencuff_version": "0.1.0",
  "plugin_version": "1.0.0"
}
```

---

## 7. Tool Interface

### 7.1 Tool Definition

> **Note:** Tool name `execute` is registered as `bash_recorder.execute` per OpenCuff's plugin namespacing convention.

```python
ToolDefinition(
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
                "description": "The bash command to execute"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds",
                "default": 120,
                "minimum": 1,
                "maximum": 600
            },
            "working_directory": {
                "type": "string",
                "description": "Directory to run command in"
            },
            "description": {
                "type": "string",
                "description": "Human-readable description of the command's purpose"
            }
        },
        "required": ["command"]
    }
)
```

### 7.2 Tool Result Schema

```python
@dataclass
class ExecuteResult:
    """Result returned from execute tool."""

    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    recording_id: str
    working_directory: str
```

### 7.3 Additional Tools

The plugin also exposes utility tools for recording management:

| Tool | Description |
|------|-------------|
| `bash_recorder.session_info` | Get information about the current recording session |
| `bash_recorder.list_recent` | List recent recordings (last N commands) |

---

## 8. CLI Commands

### 8.1 Command Overview

```
cuff record <subcommand>

Subcommands:
  list      List recording sessions
  show      Show details of a recording session
  stats     Show statistics across recordings
  generate  Generate policy rules from recordings
  clean     Clean old recordings based on retention policy
  export    Export recordings to various formats
```

### 8.2 Command Details

#### `cuff record list`

List all recording sessions.

```
Usage: cuff record list [OPTIONS]

Options:
  --since TEXT      Show sessions since date (YYYY-MM-DD or relative like "7d")
  --limit INTEGER   Maximum sessions to show (default: 20)
  --format TEXT     Output format: table, json, csv (default: table)
  --verbose         Show additional details

Examples:
  cuff record list
  cuff record list --since 7d --limit 50
  cuff record list --format json
```

**Sample Output:**
```
Session ID                    Started              Commands  Duration   Status
───────────────────────────────────────────────────────────────────────────────
20260118_143052_a7b3c9d2     2026-01-18 14:30:52       15    2m 34s    complete
20260118_160315_b8c4d0e3     2026-01-18 16:03:15        8    1m 12s    complete
20260118_171522_c9d5e1f4     2026-01-18 17:15:22        3      45s    active
```

#### `cuff record show`

Show details of a specific recording session.

```
Usage: cuff record show [OPTIONS] SESSION_ID

Arguments:
  SESSION_ID    Session ID or partial match

Options:
  --entries     Show individual command entries
  --output      Show command output (requires --entries)
  --format      Output format: table, json, yaml (default: table)

Examples:
  cuff record show 20260118_143052_a7b3c9d2
  cuff record show 143052 --entries
  cuff record show 143052 --entries --output
```

**Sample Output:**
```
Session: 20260118_143052_a7b3c9d2
Started: 2026-01-18 14:30:52
Duration: 2m 34s
Working Directory: /Users/dev/myproject

Commands: 15 total
  ✓ Succeeded: 14
  ✗ Failed: 1
  ⏱ Timed out: 0

Top Commands:
  git status          - 5 calls
  npm test            - 3 calls
  cat package.json    - 2 calls
```

#### `cuff record stats`

Show aggregate statistics across recordings.

```
Usage: cuff record stats [OPTIONS]

Options:
  --since TEXT      Analyze sessions since date
  --format TEXT     Output format: table, json (default: table)

Examples:
  cuff record stats
  cuff record stats --since 30d
```

**Sample Output:**
```
Recording Statistics (last 30 days)
────────────────────────────────────

Sessions: 47
Total Commands: 1,523
Total Duration: 4h 23m

Command Frequency:
  git status        - 312 (20.5%)
  npm test          - 187 (12.3%)
  ls -la            - 145 (9.5%)
  cat <file>        - 134 (8.8%)
  git diff          - 98 (6.4%)
  ... and 43 others

Exit Code Distribution:
  0 (success)       - 1,421 (93.3%)
  1 (error)         - 89 (5.8%)
  2+ (other)        - 13 (0.9%)

Average Duration: 1.2s
Longest Command: npm run build (45.3s)
```

#### `cuff record generate`

Generate policy rules from recordings.

```
Usage: cuff record generate [OPTIONS]

Options:
  --since TEXT          Analyze sessions since date
  --min-frequency INT   Minimum command frequency to include (default: 2)
  --format TEXT         Output format: yaml, json (default: yaml)
  --output FILE         Write to file instead of stdout
  --strategy TEXT       Generation strategy: exact, pattern, semantic (default: pattern)

Examples:
  cuff record generate --since 7d
  cuff record generate --strategy exact --output policies.yml
```

**Sample Output:**
```yaml
# Auto-generated policy from recordings
# Generated: 2026-01-18T14:30:52Z
# Source sessions: 47
# Commands analyzed: 1,523

policies:
  - name: git-operations
    description: Git version control commands
    commands:
      - pattern: "git status"
        frequency: 312
      - pattern: "git diff *"
        frequency: 145
      - pattern: "git log *"
        frequency: 67
      - pattern: "git add *"
        frequency: 45

  - name: npm-commands
    description: Node.js package manager commands
    commands:
      - pattern: "npm test"
        frequency: 187
      - pattern: "npm run *"
        frequency: 89
      - pattern: "npm install *"
        frequency: 34

  - name: file-operations
    description: File viewing and listing
    commands:
      - pattern: "ls *"
        frequency: 178
      - pattern: "cat *"
        frequency: 134
```

#### `cuff record clean`

Clean old recordings based on retention policy.

```
Usage: cuff record clean [OPTIONS]

Options:
  --older-than TEXT   Delete recordings older than (default: from config)
  --dry-run           Show what would be deleted without deleting
  --force             Skip confirmation prompt

Examples:
  cuff record clean --dry-run
  cuff record clean --older-than 90d
  cuff record clean --force
```

#### `cuff record export`

Export recordings to various formats.

```
Usage: cuff record export [OPTIONS] SESSION_ID

Arguments:
  SESSION_ID    Session ID or "all" for all sessions

Options:
  --format TEXT     Export format: json, csv, sqlite (default: json)
  --output FILE     Output file path
  --since TEXT      Filter sessions since date (when using "all")

Examples:
  cuff record export 20260118_143052_a7b3c9d2 --format json
  cuff record export all --format sqlite --output recordings.db
```

### 8.3 CLI Error Handling

CLI commands provide helpful error messages for common issues:

**Session not found:**
```
$ cuff record show nonexistent_session
Error: Session 'nonexistent_session' not found

Hint: Use 'cuff record list' to see available sessions
```

**Invalid date format:**
```
$ cuff record list --since invalid
Error: Invalid date format 'invalid'

Expected: YYYY-MM-DD or relative format (e.g., '7d', '2w', '1m')
```

**No recordings found:**
```
$ cuff record stats
No recordings found.

Recordings are created when the BashRecorder plugin executes commands.
Configure the plugin in settings.yml to start recording.
```

**Permission denied:**
```
$ cuff record clean --force
Error: Cannot delete recordings in /var/log/cuff/recordings

Permission denied. Try running with appropriate permissions.
```

---

## 9. Security Considerations

> **WARNING**: BashRecorder executes any command provided by the AI agent **without restriction**.
> This is intentional for the "learning/recording" phase. The plugin is designed to capture
> agent behavior for later analysis and policy generation. For production use with untrusted
> agents, combine with a policy enforcement plugin (see Section 13.1 - Real-time policy enforcement).

### 9.1 Threat Model

| Threat | Risk | Mitigation |
|--------|------|------------|
| **Sensitive data in recordings** | High | Configurable output capture, env allowlist |
| **Recording file tampering** | Medium | File permissions, optional integrity checks |
| **Path traversal in commands** | Medium | Working directory validation |
| **Denial of service (disk fill)** | Medium | Output size limits, retention policies |
| **Information disclosure** | Medium | Recordings stored locally, not transmitted |

### 9.2 Environment Variable Handling

Environment variables may contain sensitive data (API keys, tokens). The plugin:

1. **Does not capture environment by default** (`capture_env: false`)
2. **Uses allowlist when enabled** - only captures explicitly listed variables
3. **Never captures** common sensitive patterns:
   - `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`
   - `AWS_*`, `GITHUB_*`, `OPENAI_*` (unless explicitly allowlisted)

### 9.3 Output Sanitization

The plugin does NOT sanitize command output by default, as this could hide important information. Users should:

1. Set `capture_output: false` for sensitive workloads
2. Use `max_output_size` to limit capture size
3. Be aware recordings may contain sensitive data

### 9.4 File Permissions

Recording files are created with restrictive permissions:

```python
# Directory: 0700 (owner read/write/execute only)
# Files: 0600 (owner read/write only)
```

### 9.5 Command Execution Safety

The plugin inherits standard subprocess security practices:

- Commands run via shell (configurable)
- Working directory is validated to exist
- Timeouts prevent runaway processes
- No special privilege escalation

---

## 10. Observability

### 10.1 Metrics

The plugin exposes the following metrics for monitoring:

| Metric | Type | Description |
|--------|------|-------------|
| `bash_recorder_commands_total` | Counter | Total commands executed (labels: exit_code, timed_out) |
| `bash_recorder_command_duration_seconds` | Histogram | Command execution duration |
| `bash_recorder_recording_errors_total` | Counter | Recording failures (labels: error_type) |
| `bash_recorder_active_sessions` | Gauge | Number of active recording sessions |
| `bash_recorder_entries_recorded_total` | Counter | Total recording entries written |
| `bash_recorder_output_bytes_total` | Counter | Total bytes of output captured |
| `bash_recorder_output_truncated_total` | Counter | Commands with truncated output |

### 10.2 Structured Logging

All log messages include consistent structured fields:

```python
# Standard fields for all BashRecorder logs
log_fields = {
    "plugin": "bash_recorder",
    "session_id": "20260118_143052_a7b3c9d2-e4f5",
    "entry_id": "e_20260118_143052_001",  # When applicable
    "trace_id": "abc123",                  # From MCP context if available
}
```

**Log levels:**

| Level | Usage |
|-------|-------|
| DEBUG | Command execution details, recording writes |
| INFO | Session start/end, configuration changes |
| WARNING | Recording failures (graceful degradation), disk space low |
| ERROR | Unrecoverable errors, permission denied |

**Example log messages:**

```
INFO  bash_recorder: Session started session_id=20260118_143052_a7b3c9d2-e4f5 working_directory=/Users/dev/myproject
DEBUG bash_recorder: Command executed entry_id=e_001 command_hash=sha256:abc123 duration_ms=1523 exit_code=0
WARN  bash_recorder: Recording write failed session_id=20260118_143052_a7b3c9d2-e4f5 error="disk full" degraded=true
INFO  bash_recorder: Session finalized session_id=20260118_143052_a7b3c9d2-e4f5 entry_count=15 total_duration_ms=45000 status=complete
```

### 10.3 Health Check Response

The health check (Section 4.1) returns structured data for monitoring:

```json
{
  "healthy": true,
  "recording_enabled": true,
  "directory_writable": true,
  "disk_space_ok": true,
  "current_session": "20260118_143052_a7b3c9d2-e4f5",
  "entries_recorded": 15
}
```

### 10.4 Trace Context Propagation

When MCP requests include trace context, it is propagated to:

- Log entries (via `trace_id` field)
- Recording entries (via `tool_call_id` field)
- Metrics labels (where applicable)

This enables end-to-end tracing from agent request to recording.

---

## 11. Error Handling

### 11.1 Error Categories

```python
class BashRecorderError(Exception):
    """Base exception for BashRecorder errors."""
    pass


class RecordingError(BashRecorderError):
    """Errors related to recording operations."""
    pass


class ExecutionError(BashRecorderError):
    """Errors related to command execution."""
    pass


class ConfigurationError(BashRecorderError):
    """Errors related to plugin configuration."""
    pass
```

### 11.2 Error Handling Strategy

| Scenario | Behavior |
|----------|----------|
| Recording directory not writable | Log warning, continue without recording |
| Recording write fails | Log error, return command result anyway |
| Command timeout | Kill process, record timeout, return partial output |
| Invalid working directory | Return error, do not execute |
| Disk full | Log error, disable recording, continue execution, auto-recover |

### 11.3 Disk Recovery

When recording is disabled due to disk full:

1. Recording is automatically re-enabled on the next command execution if the recording directory becomes writable again
2. A warning is logged when recording recovers from disabled state
3. A new session is started after recovery (previous session marked as "interrupted")

```python
async def _check_and_recover_recording(self) -> None:
    """Check if recording can be re-enabled after disk full."""
    if not self._recording_enabled and self._config.recording.enabled:
        if self._check_directory_writable():
            self._recording_enabled = True
            await self._session_manager.start_session()
            logger.warning(
                "Recording recovered from disabled state",
                session_id=self._session_manager.current_session_id,
            )
```

### 11.4 Graceful Degradation

The plugin prioritizes command execution over recording. If recording fails:

1. Command execution still proceeds
2. Warning is logged
3. Result includes `recording_id: null`
4. User is informed via tool result metadata

---

## 12. Example Configurations

### 12.1 Development Environment (Verbose)

```yaml
plugins:
  bash_recorder:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.bash_recorder
    config:
      recording:
        enabled: true
        directory: .cuff/recordings
        capture_env: true
        env_allowlist:
          - PATH
          - HOME
          - USER
          - SHELL
          - PWD
          - VIRTUAL_ENV
          - NODE_ENV
        capture_output: true
        max_output_size: 5000000  # 5MB
        session_mode: per_conversation
        retention_days: 90

      execution:
        default_timeout: 300
        max_timeout: 600
        shell: /bin/bash
```

### 12.2 Production/CI Environment (Minimal)

```yaml
plugins:
  bash_recorder:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.bash_recorder
    config:
      recording:
        enabled: true
        directory: /var/log/cuff/recordings
        capture_env: false
        capture_output: false  # Don't capture potentially sensitive output
        session_mode: continuous
        retention_days: 7

      execution:
        default_timeout: 60
        max_timeout: 120
        shell: /bin/sh  # More portable
```

### 12.3 Policy Generation Focus

```yaml
plugins:
  bash_recorder:
    enabled: true
    type: in_source
    module: opencuff.plugins.builtin.bash_recorder
    config:
      recording:
        enabled: true
        directory: .cuff/recordings
        capture_env: false
        capture_output: false  # Only need commands for policy gen
        session_mode: continuous
        retention_days: 0  # Keep forever for analysis

      execution:
        default_timeout: 120
        max_timeout: 600
```

---

## 13. Future Considerations

### 13.1 Planned Enhancements

| Feature | Description | Priority |
|---------|-------------|----------|
| **Real-time policy enforcement** | Block commands not matching policy | High |
| **Recording encryption** | Encrypt recordings at rest | Medium |
| **Remote storage** | Store recordings in S3/GCS | Medium |
| **Recording replay** | Re-execute recorded sessions | Low |
| **Semantic analysis** | AI-powered command classification | Low |

### 13.2 Integration Points

- **Policy Engine**: Feed recordings to policy evaluation
- **Audit Dashboard**: Web UI for recording analysis
- **CI/CD Integration**: Export recordings as build artifacts
- **SIEM Integration**: Forward recordings to security systems

### 13.3 Performance Considerations

For high-volume usage:

- Consider async file writes
- Implement recording batching
- Add compression for stored recordings
- Consider SQLite backend for large datasets

---

## Appendix A: JSON Schema for Recording Entry

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": [
    "entry_id",
    "session_id",
    "sequence_number",
    "timestamp",
    "duration_ms",
    "command",
    "working_directory",
    "shell",
    "timeout_seconds",
    "timed_out",
    "output_truncated",
    "opencuff_version",
    "plugin_version"
  ],
  "properties": {
    "entry_id": { "type": "string" },
    "session_id": { "type": "string" },
    "sequence_number": { "type": "integer", "minimum": 1 },
    "timestamp": { "type": "string", "format": "date-time" },
    "duration_ms": { "type": "integer", "minimum": 0 },
    "command": { "type": "string" },
    "description": { "type": ["string", "null"] },
    "working_directory": { "type": "string" },
    "shell": { "type": "string" },
    "timeout_seconds": { "type": "integer", "minimum": 1 },
    "timed_out": { "type": "boolean" },
    "exit_code": { "type": ["integer", "null"] },
    "stdout": { "type": ["string", "null"] },
    "stderr": { "type": ["string", "null"] },
    "output_truncated": { "type": "boolean" },
    "output_truncated_bytes": { "type": ["integer", "null"] },
    "environment": {
      "type": ["object", "null"],
      "additionalProperties": { "type": "string" }
    },
    "agent_id": { "type": ["string", "null"] },
    "conversation_id": { "type": ["string", "null"] },
    "tool_call_id": { "type": ["string", "null"] },
    "opencuff_version": { "type": "string" },
    "plugin_version": { "type": "string" }
  }
}
```

---

## Appendix B: CLI Command Registration

```python
@classmethod
def get_cli_commands(cls) -> list[CLICommand]:
    return [
        CLICommand(
            name="list",
            help="List recording sessions",
            callback=cls._cli_list,
            options=[
                CLIOption("--since", help="Show sessions since date"),
                CLIOption("--limit", type=int, default=20),
                CLIOption("--format", default="table"),
                CLIOption("--verbose", is_flag=True),
            ]
        ),
        CLICommand(
            name="show",
            help="Show recording session details",
            callback=cls._cli_show,
            arguments=[CLIArgument("session_id", required=True)],
            options=[
                CLIOption("--entries", is_flag=True),
                CLIOption("--output", is_flag=True),
                CLIOption("--format", default="table"),
            ]
        ),
        CLICommand(
            name="stats",
            help="Show recording statistics",
            callback=cls._cli_stats,
            options=[
                CLIOption("--since", help="Analyze since date"),
                CLIOption("--format", default="table"),
            ]
        ),
        CLICommand(
            name="generate",
            help="Generate policy rules from recordings",
            callback=cls._cli_generate,
            options=[
                CLIOption("--since", help="Analyze since date"),
                CLIOption("--min-frequency", type=int, default=2),
                CLIOption("--format", default="yaml"),
                CLIOption("--output", type=Path),
                CLIOption("--strategy", default="pattern"),
            ]
        ),
        CLICommand(
            name="clean",
            help="Clean old recordings",
            callback=cls._cli_clean,
            options=[
                CLIOption("--older-than", help="Delete older than"),
                CLIOption("--dry-run", is_flag=True),
                CLIOption("--force", is_flag=True),
            ]
        ),
        CLICommand(
            name="export",
            help="Export recordings",
            callback=cls._cli_export,
            arguments=[CLIArgument("session_id", required=True)],
            options=[
                CLIOption("--format", default="json"),
                CLIOption("--output", type=Path),
                CLIOption("--since", help="Filter since date"),
            ]
        ),
    ]
```
