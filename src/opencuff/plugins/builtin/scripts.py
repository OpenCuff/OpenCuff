"""Scripts plugin for OpenCuff.

This plugin discovers and exposes shell scripts as MCP tools based on configurable
glob patterns. It allows organizations to provide controlled access to a curated
set of scripts for AI coding agents, without exposing arbitrary command execution.

The plugin supports:
    - Glob-based script selection (e.g., `scripts/*.sh`, `build/*.sh`)
    - Automatic tool generation from matched scripts
    - Support for multiple script types (shell, Python, etc.)
    - Configurable execution parameters (timeout, working directory, environment)
    - Discovery support for `cuff init`

Example configuration:
    plugins:
      scripts:
        type: in_source
        module: opencuff.plugins.builtin.scripts
        config:
          patterns:
            - "scripts/*.sh"
            - "tools/**/*.py"
          base_directory: "."
          exclude:
            - "scripts/internal_*.sh"
          default_timeout: 300

SECURITY WARNING:
    Scripts have full access to the system, environment variables (which may
    contain secrets), and the filesystem. Before enabling this plugin, audit
    the scripts in your project to ensure they do not perform destructive or
    sensitive operations when invoked by AI agents.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from opencuff.plugins.base import (
    CLIArgument,
    CLICommand,
    CLIOption,
    DiscoveryResult,
    InSourcePlugin,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Security Constants
# =============================================================================

# Characters that could enable shell injection if passed to scripts
DANGEROUS_CHARS: frozenset[str] = frozenset(";&|`$(){}[]<>\\'\"!*?~\n\r")

# Environment variables that should never be overridden
BLOCKED_ENV_VARS: frozenset[str] = frozenset(
    {
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",  # macOS equivalent of LD_PRELOAD
        "DYLD_LIBRARY_PATH",  # macOS library path
        "PYTHONPATH",  # Can affect Python script execution
        "NODE_OPTIONS",  # Can inject code into Node.js processes
        "NODE_PATH",  # Can affect Node.js script execution
        "RUBYLIB",  # Can affect Ruby script execution
        "RUBYOPT",  # Can affect Ruby script execution
        "PERL5LIB",  # Can affect Perl script execution
        "PERL5OPT",  # Can affect Perl script execution
        "HOME",  # Can affect config file locations
        "USER",  # Identity spoofing
        "SHELL",  # Shell override
    }
)

# Default interpreters by file extension
DEFAULT_INTERPRETERS: dict[str, str] = {
    ".sh": "/bin/sh",
    ".bash": "/bin/bash",
    ".zsh": "/bin/zsh",
    ".py": "python3",
    ".rb": "ruby",
    ".js": "node",
    ".ts": "npx ts-node",
    ".pl": "perl",
    ".php": "php",
}


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class ScriptInfo:
    """Represents a discovered script.

    Attributes:
        path: Relative path to the script from base_directory.
        interpreter: The interpreter to use for executing the script.
        description: Optional human-readable description extracted from comments.
    """

    path: Path
    interpreter: str | None
    description: str | None = None

    def to_tool_name(self) -> str:
        """Convert script path to valid MCP tool name.

        Transformation rules:
            1. Remove the file extension
            2. Replace path separators with underscores
            3. Replace hyphens and dots with underscores
            4. Prefix with `script_`

        Returns:
            A valid MCP tool name.
        """
        return Plugin._path_to_tool_name(self.path)

    def to_tool_definition(self) -> ToolDefinition:
        """Convert to MCP ToolDefinition.

        Returns:
            A ToolDefinition for this script.
        """
        description = self.description or f"Run {self.path}"

        return ToolDefinition(
            name=self.to_tool_name(),
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Arguments to pass to the script",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds",
                        "minimum": 1,
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Additional environment variables",
                    },
                },
            },
            returns={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "script_path": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                },
            },
        )


# =============================================================================
# Configuration
# =============================================================================


class ScriptsPluginConfig(BaseModel):
    """Configuration for the scripts plugin.

    Attributes:
        patterns: Glob patterns for scripts to expose (required).
        base_directory: Base directory for resolving patterns.
        exclude: Glob patterns for scripts to exclude.
        default_timeout: Default timeout in seconds.
        working_directory: Working directory for script execution.
        environment: Environment variables to pass to scripts.
        expose_list_scripts: Whether to expose a list_scripts tool.
        interpreters: Custom interpreter mapping by extension.
        require_executable: Whether to require scripts to be executable.
        cache_ttl: Cache TTL for script discovery in seconds.
    """

    patterns: list[str] = Field(
        ...,
        min_length=1,
        description="Glob patterns for scripts to expose (required)",
    )

    base_directory: str = Field(
        default=".",
        description="Base directory for resolving patterns",
    )

    exclude: list[str] = Field(
        default_factory=list,
        description="Glob patterns for scripts to exclude",
    )

    default_timeout: int = Field(
        default=300,
        ge=1,
        description="Default timeout in seconds",
    )

    working_directory: str = Field(
        default=".",
        description="Working directory for script execution",
    )

    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to pass to scripts",
    )

    expose_list_scripts: bool = Field(
        default=True,
        description="Whether to expose a list_scripts tool",
    )

    interpreters: dict[str, str] = Field(
        default_factory=dict,
        description="Custom interpreter mapping by extension",
    )

    require_executable: bool = Field(
        default=False,
        description="Whether to require scripts to be executable",
    )

    cache_ttl: int = Field(
        default=300,
        ge=0,
        description="Cache TTL for script discovery in seconds",
    )

    @field_validator("patterns")
    @classmethod
    def validate_patterns_not_empty(cls, v: list[str]) -> list[str]:
        """Validate that patterns list is not empty."""
        if not v:
            raise ValueError("patterns must not be empty")
        return v


# =============================================================================
# Script Filtering
# =============================================================================


class ScriptFilter:
    """Filters scripts based on include/exclude glob patterns."""

    def __init__(
        self,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> None:
        """Initialize the script filter.

        Args:
            include_patterns: Glob patterns for scripts to include.
            exclude_patterns: Glob patterns for scripts to exclude.
        """
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns or []

    def _matches_pattern(self, script_path: Path, pattern: str) -> bool:
        """Check if script path matches a single glob pattern.

        Uses Path.match for ** recursive patterns and fnmatch for simple patterns.

        Args:
            script_path: The script path to check.
            pattern: The glob pattern to match against.

        Returns:
            True if the path matches the pattern.
        """
        # Use Path.match for patterns containing ** (handles recursion)
        # Path.match handles ** patterns correctly
        if script_path.match(pattern):
            return True

        # Also try fnmatch for backward compatibility with simple patterns
        path_str = str(script_path)
        return bool(fnmatch.fnmatch(path_str, pattern))

    def matches(self, script_path: Path) -> bool:
        """Check if script matches filter criteria.

        Exclude patterns are checked first, then include patterns.

        Args:
            script_path: The script path to check.

        Returns:
            True if the script should be included, False otherwise.
        """
        # First check exclude patterns
        for pattern in self.exclude_patterns:
            if self._matches_pattern(script_path, pattern):
                return False

        # Then check include patterns
        for pattern in self.include_patterns:
            if self._matches_pattern(script_path, pattern):
                return True

        return False

    @classmethod
    def from_config(cls, config: ScriptsPluginConfig) -> ScriptFilter:
        """Create filter from configuration.

        Args:
            config: The scripts plugin configuration.

        Returns:
            A configured ScriptFilter instance.
        """
        return cls(config.patterns, config.exclude)


# =============================================================================
# Caching
# =============================================================================


@dataclass
class CacheEntry:
    """Cache entry for discovered scripts.

    Attributes:
        scripts: Cached list of discovered scripts.
        timestamp: Unix timestamp when the cache entry was created.
        config_hash: Hash of relevant configuration fields.
    """

    scripts: list[ScriptInfo]
    timestamp: float
    config_hash: str

    def is_valid(
        self,
        ttl: int,
        current_config_hash: str,
        current_time: float | None = None,
    ) -> bool:
        """Check if cache entry is still valid.

        Args:
            ttl: Cache time-to-live in seconds.
            current_config_hash: Current hash of relevant config fields.
            current_time: Optional current timestamp for testing.

        Returns:
            True if cache is still valid, False otherwise.
        """
        if ttl <= 0:
            return False

        now = current_time if current_time is not None else time.time()
        age = now - self.timestamp
        if age >= ttl:
            return False

        return current_config_hash == self.config_hash


class ScriptCache:
    """Cache for discovered scripts with configuration-based invalidation."""

    def __init__(self) -> None:
        """Initialize the cache."""
        self._cache: dict[str, CacheEntry] = {}

    @staticmethod
    def _hash_config(config: ScriptsPluginConfig) -> str:
        """Compute hash of configuration fields that affect discovery.

        Args:
            config: The plugin configuration.

        Returns:
            Hex-encoded SHA-256 hash of relevant config fields.
        """
        relevant_fields = (
            ",".join(sorted(config.patterns)),
            ",".join(sorted(config.exclude)),
            config.base_directory,
            str(config.require_executable),
        )
        combined = "|".join(str(f) for f in relevant_fields)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def get(
        self,
        base_directory: str,
        ttl: int,
        config: ScriptsPluginConfig,
    ) -> CacheEntry | None:
        """Get cached scripts if valid.

        Args:
            base_directory: The base directory key.
            ttl: Cache time-to-live in seconds.
            config: The plugin configuration.

        Returns:
            The cache entry if valid, None otherwise.
        """
        entry = self._cache.get(base_directory)
        if entry is None:
            return None

        current_config_hash = self._hash_config(config)

        if entry.is_valid(ttl, current_config_hash):
            return entry

        del self._cache[base_directory]
        return None

    def set(
        self,
        base_directory: str,
        scripts: list[ScriptInfo],
        config: ScriptsPluginConfig,
    ) -> None:
        """Cache discovered scripts.

        Args:
            base_directory: The base directory key.
            scripts: The discovered scripts.
            config: The plugin configuration.
        """
        config_hash = self._hash_config(config)

        self._cache[base_directory] = CacheEntry(
            scripts=scripts,
            timestamp=time.time(),
            config_hash=config_hash,
        )

    def invalidate(self, base_directory: str | None = None) -> None:
        """Invalidate cache entries.

        Args:
            base_directory: Path to invalidate, or None to clear all.
        """
        if base_directory is None:
            self._cache.clear()
        elif base_directory in self._cache:
            del self._cache[base_directory]


# =============================================================================
# Plugin Implementation
# =============================================================================


class Plugin(InSourcePlugin):
    """Scripts plugin exposing shell scripts as MCP tools.

    This plugin discovers scripts matching configured glob patterns and exposes
    them as tools that can be invoked by AI coding agents. It supports multiple
    script types and configurable execution parameters.

    Configuration options:
        patterns: Glob patterns for scripts to expose (required)
        base_directory: Base directory for patterns (default: ".")
        exclude: Patterns for scripts to exclude
        default_timeout: Default timeout in seconds (default: 300)
        working_directory: Working directory for execution (default: ".")
    """

    def __init__(
        self,
        config: dict[str, Any],
        instance_name: str = "scripts",
        cache: ScriptCache | None = None,
    ) -> None:
        """Initialize the scripts plugin.

        Args:
            config: Plugin configuration dictionary.
            instance_name: Name for this plugin instance.
            cache: Optional ScriptCache instance for dependency injection.
        """
        super().__init__(config)
        self._plugin_config = ScriptsPluginConfig.model_validate(config)
        self._instance_name = instance_name
        self._scripts: list[ScriptInfo] = []
        self._tool_to_script: dict[str, Path] = {}
        self._cache = cache if cache is not None else ScriptCache()
        self._initialized = False

    # =========================================================================
    # Security Methods
    # =========================================================================

    def _sanitize_args(self, args: list[str]) -> list[str]:
        """Sanitize arguments to prevent injection attacks.

        Args:
            args: List of arguments to sanitize.

        Returns:
            The sanitized arguments (same as input if valid).

        Raises:
            ValueError: If an argument contains dangerous characters.
        """
        for arg in args:
            dangerous_found = DANGEROUS_CHARS.intersection(arg)
            if dangerous_found:
                raise ValueError(
                    f"Argument contains dangerous characters: {dangerous_found}"
                )
        return args

    def _validate_env(self, env: dict[str, str]) -> dict[str, str]:
        """Validate environment variables.

        Args:
            env: Environment variables to validate.

        Returns:
            The validated environment variables.

        Raises:
            ValueError: If a blocked environment variable is provided.
        """
        blocked_found = BLOCKED_ENV_VARS.intersection(env.keys())
        if blocked_found:
            raise ValueError(f"Blocked environment variables: {blocked_found}")
        return env

    def _validate_script_path(self, script_path: str) -> Path:
        """Validate and resolve a script path.

        Ensures the path is within the base directory and matches allowed
        patterns. Handles symlinks safely by resolving them and checking
        the target.

        Args:
            script_path: The relative script path to validate.

        Returns:
            The resolved absolute path to the script.

        Raises:
            ValueError: If the path is invalid or not allowed.
        """
        base = Path(self._plugin_config.base_directory).resolve()
        full_path = (base / script_path).resolve()

        # Ensure path is within base directory (handles symlinks)
        try:
            full_path.relative_to(base)
        except ValueError:
            msg = f"Script resolves outside base directory: {script_path}"
            raise ValueError(msg) from None

        # Check if path matches any allowed pattern
        if not self._matches_allowed_patterns(Path(script_path)):
            raise ValueError(f"Script not in allowed patterns: {script_path}")

        # Check if path is excluded
        if self._matches_exclude_patterns(Path(script_path)):
            raise ValueError(f"Script is excluded: {script_path}")

        # Verify file exists and is a file
        if not full_path.is_file():
            raise ValueError(f"Script not found: {script_path}")

        return full_path

    def _matches_allowed_patterns(self, script_path: Path) -> bool:
        """Check if script path matches any allowed pattern.

        Args:
            script_path: The script path to check.

        Returns:
            True if the path matches at least one allowed pattern.
        """
        path_str = str(script_path)
        for pattern in self._plugin_config.patterns:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    def _matches_exclude_patterns(self, script_path: Path) -> bool:
        """Check if script path matches any exclude pattern.

        Args:
            script_path: The script path to check.

        Returns:
            True if the path matches any exclude pattern.
        """
        path_str = str(script_path)
        for pattern in self._plugin_config.exclude:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    # =========================================================================
    # Script Discovery and Parsing
    # =========================================================================

    @staticmethod
    def _path_to_tool_name(path: Path) -> str:
        """Convert a script path to a tool name.

        Args:
            path: The script path.

        Returns:
            A valid MCP tool name.
        """
        # Remove extension
        name = path.with_suffix("").as_posix()
        # Replace separators and special chars
        name = name.replace("/", "_").replace("-", "_").replace(".", "_")
        return f"script_{name}"

    @staticmethod
    def _extract_description(content: str) -> str | None:
        """Extract description from script comment.

        Looks for the first comment line after the shebang.

        Args:
            content: The script content.

        Returns:
            The description or None if not found.
        """
        lines = content.split("\n")

        for line in lines:
            stripped = line.strip()

            # Skip shebang line
            if stripped.startswith("#!"):
                continue

            # Look for comment line
            if stripped.startswith("#"):
                # Extract comment content
                comment = stripped[1:].strip()
                # Skip empty comments
                if comment:
                    return comment

            # Stop at first non-comment, non-empty line
            if stripped and not stripped.startswith("#"):
                break

        return None

    @staticmethod
    def _detect_interpreter(
        content: str,
        script_path: Path,
        configured_interpreters: dict[str, str],
    ) -> str | None:
        """Detect the interpreter for a script.

        Order of precedence:
            1. Configured interpreter for file extension
            2. Shebang line
            3. Default interpreter for extension

        Args:
            content: The script content.
            script_path: The script path (for extension).
            configured_interpreters: User-configured interpreters.

        Returns:
            The interpreter command or None if not detected.
        """
        extension = script_path.suffix

        # 1. Check configured interpreter
        if extension in configured_interpreters:
            return configured_interpreters[extension]

        # 2. Check shebang line
        if content.startswith("#!"):
            first_line = content.split("\n")[0]
            shebang = first_line[2:].strip()

            # Handle /usr/bin/env
            if "/env " in shebang or shebang.endswith("/env"):
                parts = shebang.split()
                if len(parts) >= 2:
                    return parts[-1]  # Return the interpreter name
            else:
                return shebang

        # 3. Check default interpreter for extension
        if extension in DEFAULT_INTERPRETERS:
            return DEFAULT_INTERPRETERS[extension]

        return None

    async def _discover_scripts(self) -> list[ScriptInfo]:
        """Discover scripts matching configured patterns.

        Returns:
            List of discovered script info objects.
        """
        base_dir = Path(self._plugin_config.base_directory).resolve()
        script_filter = ScriptFilter.from_config(self._plugin_config)
        scripts: list[ScriptInfo] = []
        seen_paths: set[Path] = set()

        for pattern in self._plugin_config.patterns:
            # Use glob to find matching files
            for full_path in base_dir.glob(pattern):
                if not full_path.is_file():
                    continue

                # Get relative path
                try:
                    rel_path = full_path.relative_to(base_dir)
                except ValueError:
                    continue

                # Skip duplicates
                if rel_path in seen_paths:
                    continue
                seen_paths.add(rel_path)

                # Check exclude patterns
                if not script_filter.matches(rel_path):
                    continue

                # Check executable requirement
                if self._plugin_config.require_executable and not os.access(
                    full_path, os.X_OK
                ):
                    logger.debug(
                        "skipping_non_executable: path=%s",
                        str(rel_path),
                    )
                    continue

                # Read script content
                try:
                    content = full_path.read_text(errors="replace")
                except OSError as e:
                    logger.warning(
                        "cannot_read_script: path=%s, error=%s",
                        str(rel_path),
                        str(e),
                    )
                    continue

                # Detect interpreter
                interpreter = self._detect_interpreter(
                    content,
                    rel_path,
                    self._plugin_config.interpreters,
                )

                # Extract description
                description = self._extract_description(content)

                scripts.append(
                    ScriptInfo(
                        path=rel_path,
                        interpreter=interpreter,
                        description=description,
                    )
                )

        return scripts

    async def _refresh_scripts(self) -> None:
        """Refresh the script list, using cache if available."""
        base_dir = str(Path(self._plugin_config.base_directory).resolve())

        # Check cache first
        cached = self._cache.get(
            base_dir,
            self._plugin_config.cache_ttl,
            self._plugin_config,
        )
        if cached is not None:
            logger.debug(
                "cache_hit: base_dir=%s, script_count=%d",
                base_dir,
                len(cached.scripts),
            )
            self._scripts = cached.scripts
            self._build_tool_mapping()
            return

        # Discover scripts
        self._scripts = await self._discover_scripts()
        self._build_tool_mapping()

        # Update cache
        if self._plugin_config.cache_ttl > 0:
            self._cache.set(base_dir, self._scripts, self._plugin_config)

        logger.info(
            "scripts_discovered: base_dir=%s, count=%d",
            base_dir,
            len(self._scripts),
        )

    def _build_tool_mapping(self) -> None:
        """Build the tool name to script path mapping."""
        self._tool_to_script = {s.to_tool_name(): s.path for s in self._scripts}

    # =========================================================================
    # Plugin Lifecycle
    # =========================================================================

    async def initialize(self) -> None:
        """Initialize plugin and discover scripts."""
        await self._refresh_scripts()
        self._initialized = True
        logger.info(
            "plugin_initialized: instance=%s, scripts=%d",
            self._instance_name,
            len(self._scripts),
        )

    async def shutdown(self) -> None:
        """Clean up plugin resources."""
        self._cache.invalidate()
        self._scripts = []
        self._tool_to_script = {}
        self._initialized = False
        logger.info(
            "plugin_shutdown: instance=%s",
            self._instance_name,
        )

    async def health_check(self) -> bool:
        """Check plugin health status.

        Returns:
            True if the plugin is healthy, False otherwise.
        """
        if not self._initialized:
            return False

        base_dir = Path(self._plugin_config.base_directory).resolve()
        return base_dir.exists() and base_dir.is_dir()

    async def detailed_health_check(self) -> dict[str, Any]:
        """Perform a detailed health check with diagnostic information.

        Returns:
            Dictionary containing health status and diagnostic details.
        """
        base_dir = Path(self._plugin_config.base_directory).resolve()
        base_exists = base_dir.exists() and base_dir.is_dir()

        healthy = self._initialized and base_exists

        return {
            "healthy": healthy,
            "initialized": self._initialized,
            "base_directory": str(base_dir),
            "base_directory_exists": base_exists,
            "script_count": len(self._scripts),
            "cache_ttl": self._plugin_config.cache_ttl,
            "patterns": self._plugin_config.patterns,
        }

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration reload.

        Args:
            new_config: The new configuration dictionary.
        """
        new_validated = ScriptsPluginConfig.model_validate(new_config)

        # Check if cache-affecting settings changed
        patterns_changed = new_validated.patterns != self._plugin_config.patterns
        exclude_changed = new_validated.exclude != self._plugin_config.exclude
        new_base_dir = new_validated.base_directory
        old_base_dir = self._plugin_config.base_directory
        base_dir_changed = new_base_dir != old_base_dir
        exec_req_changed = (
            new_validated.require_executable != self._plugin_config.require_executable
        )
        cache_invalidation_needed = (
            patterns_changed or exclude_changed or base_dir_changed or exec_req_changed
        )

        self._plugin_config = new_validated
        self.config = new_config

        if cache_invalidation_needed:
            self._cache.invalidate()
            await self._refresh_scripts()

        logger.info(
            "config_reloaded: instance=%s, cache_invalidated=%s",
            self._instance_name,
            cache_invalidation_needed,
        )

    # =========================================================================
    # Tool Interface
    # =========================================================================

    def get_tools(self) -> list[ToolDefinition]:
        """Return tool definitions for discovered scripts.

        Returns:
            List of ToolDefinition objects for all exposed scripts.
        """
        tools: list[ToolDefinition] = []

        # Optionally add list_scripts tool
        if self._plugin_config.expose_list_scripts:
            tools.append(
                ToolDefinition(
                    name="script_list_scripts",
                    description="List all available scripts",
                    parameters={"type": "object", "properties": {}},
                    returns={
                        "type": "object",
                        "properties": {
                            "scripts": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "path": {"type": "string"},
                                        "description": {"type": "string"},
                                        "interpreter": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                )
            )

        # Add tool for each script
        for script in self._scripts:
            tools.append(script.to_tool_definition())

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute the requested script tool.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Arguments for the tool.

        Returns:
            ToolResult with the execution outcome.
        """
        if not self._initialized:
            return ToolResult(
                success=False,
                error="Plugin not initialized",
            )

        # Handle list_scripts tool
        if tool_name == "script_list_scripts":
            return await self._list_scripts()

        # Find the script
        script_path = self._tool_to_script.get(tool_name)
        if script_path is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
            )

        return await self._execute_script(script_path, arguments)

    async def _list_scripts(self) -> ToolResult:
        """List all available scripts.

        Returns:
            ToolResult with script list.
        """
        scripts_data = [
            {
                "name": s.to_tool_name(),
                "path": str(s.path),
                "description": s.description,
                "interpreter": s.interpreter,
            }
            for s in self._scripts
        ]
        return ToolResult(
            success=True,
            data={"scripts": scripts_data},
        )

    async def _execute_script(
        self,
        script_path: Path,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute a script.

        Args:
            script_path: The relative script path.
            arguments: Execution arguments (args, timeout, env).

        Returns:
            ToolResult with execution outcome.
        """
        # Extract arguments
        args: list[str] = arguments.get("args", [])
        timeout: int = arguments.get("timeout", self._plugin_config.default_timeout)
        env_overrides: dict[str, str] = arguments.get("env", {})

        # Validate arguments
        try:
            self._sanitize_args(args)
        except ValueError as e:
            return ToolResult(
                success=False,
                error=str(e),
            )

        # Validate environment variables
        try:
            self._validate_env(env_overrides)
        except ValueError as e:
            return ToolResult(
                success=False,
                error=str(e),
            )

        # Resolve and validate script path
        try:
            full_path = self._validate_script_path(str(script_path))
        except ValueError as e:
            return ToolResult(
                success=False,
                error=str(e),
            )

        # Get script info for interpreter
        script_info = next(
            (s for s in self._scripts if s.path == script_path),
            None,
        )
        interpreter = script_info.interpreter if script_info else None

        # Build command
        if interpreter:
            cmd = [interpreter, str(full_path)] + args
        else:
            cmd = [str(full_path)] + args

        # Build environment
        env = os.environ.copy()
        env.update(self._plugin_config.environment)
        env.update(env_overrides)

        # Resolve working directory
        working_dir = Path(self._plugin_config.working_directory).resolve()

        # Execute
        start_time = time.time()
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            duration = time.time() - start_time
            exit_code = process.returncode or 0

            error_msg = (
                None if exit_code == 0 else f"Script failed with exit code {exit_code}"
            )

            return ToolResult(
                success=exit_code == 0,
                data={
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "exit_code": exit_code,
                    "script_path": str(script_path),
                    "duration_seconds": round(duration, 2),
                },
                error=error_msg,
            )

        except TimeoutError:
            return ToolResult(
                success=False,
                error=f"Script timed out after {timeout} seconds",
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error=f"Script or interpreter not found: {cmd[0]}",
            )
        except OSError as e:
            return ToolResult(
                success=False,
                error=f"Failed to execute script: {e}",
            )

    # =========================================================================
    # Discovery Interface
    # =========================================================================

    @classmethod
    def discover(cls, directory: Path) -> DiscoveryResult:
        """Discover scripts in the directory.

        Scans for common script patterns and returns suggested configuration.

        Args:
            directory: The directory to scan.

        Returns:
            DiscoveryResult indicating applicability and suggested config.
        """
        # Common script locations to check
        script_patterns = [
            "scripts/*.sh",
            "scripts/*.py",
            "bin/*.sh",
            "tools/*.sh",
            "*.sh",  # Root-level scripts
        ]

        discovered_scripts: list[Path] = []
        matched_patterns: list[str] = []

        for pattern in script_patterns:
            matches = list(directory.glob(pattern))
            if matches:
                discovered_scripts.extend(matches)
                matched_patterns.append(pattern)

        if not discovered_scripts:
            return DiscoveryResult(
                applicable=False,
                confidence=0.0,
                suggested_config={},
                description="No scripts found",
            )

        # Build tool names for discovered_items
        tool_names = ["script_list_scripts"]
        for script in discovered_scripts:
            try:
                rel_path = script.relative_to(directory)
                tool_name = cls._path_to_tool_name(rel_path)
                tool_names.append(tool_name)
            except ValueError:
                pass

        # Generate warnings
        warnings = cls._generate_discovery_warnings(discovered_scripts)

        return DiscoveryResult(
            applicable=True,
            confidence=0.8,  # Lower confidence than Makefile/package.json
            suggested_config={
                "patterns": matched_patterns,
                "base_directory": ".",
                "exclude": [],
                "default_timeout": 300,
                "working_directory": ".",
                "expose_list_scripts": True,
                "cache_ttl": 300,
            },
            description=f"Found {len(discovered_scripts)} scripts",
            discovered_items=tool_names,
            warnings=warnings,
        )

    @staticmethod
    def _generate_discovery_warnings(scripts: list[Path]) -> list[str]:
        """Generate warnings about discovered scripts.

        Args:
            scripts: List of discovered script paths.

        Returns:
            List of warning messages.
        """
        warnings: list[str] = []

        for script in scripts:
            name_lower = script.name.lower()

            # Check for potentially sensitive scripts
            sensitive_words = ["secret", "password", "credential", "key"]
            if any(w in name_lower for w in sensitive_words):
                warnings.append(
                    f"Script '{script}' may contain sensitive operations - "
                    "review before enabling"
                )

            # Check script content
            try:
                content = script.read_bytes()

                # Check if file is binary
                if b"\x00" in content[:8192]:
                    warnings.append(
                        f"Script '{script}' appears to be binary - "
                        "verify this is intentional"
                    )
                    continue

                text_content = content.decode("utf-8", errors="replace")
                first_line = text_content.split("\n")[0] if text_content else ""

                # Check for missing shebang
                if not first_line.startswith("#!"):
                    warnings.append(
                        f"Script '{script}' has no shebang line - "
                        "interpreter will be guessed"
                    )

            except OSError:
                pass

            # Check for world-writable scripts
            try:
                mode = script.stat().st_mode
                if mode & stat.S_IWOTH:
                    warnings.append(
                        f"SECURITY: Script '{script}' is world-writable - "
                        "this allows any user to modify the script"
                    )
            except OSError:
                pass

            # Check for symlinks
            if script.is_symlink():
                warnings.append(
                    f"Script '{script}' is a symlink - "
                    "target will be validated at runtime"
                )

        return warnings

    # =========================================================================
    # CLI Interface
    # =========================================================================

    @classmethod
    def get_cli_commands(cls) -> list[CLICommand]:
        """Return CLI commands this plugin provides.

        Returns:
            List of CLICommand definitions.
        """
        return [
            CLICommand(
                name="list",
                help="List scripts matching configured patterns",
                callback=cls._cli_list_scripts,
                options=[
                    CLIOption(
                        name="--pattern",
                        help="Glob pattern to match (default: scripts/*.sh)",
                        default="scripts/*.sh",
                    ),
                ],
            ),
            CLICommand(
                name="run",
                help="Run a specific script",
                callback=cls._cli_run_script,
                arguments=[
                    CLIArgument(
                        name="script",
                        help="Path to the script to run",
                    ),
                ],
                options=[
                    CLIOption(
                        name="--dry-run",
                        help="Show command without executing",
                        is_flag=True,
                    ),
                    CLIOption(
                        name="--timeout",
                        help="Execution timeout in seconds",
                        default=300,
                        type=int,
                    ),
                ],
            ),
        ]

    @classmethod
    def _cli_list_scripts(cls, pattern: str = "scripts/*.sh") -> None:
        """CLI handler for list command.

        Args:
            pattern: Glob pattern to match scripts.
        """
        directory = Path.cwd()
        scripts = list(directory.glob(pattern))

        if not scripts:
            print(f"No scripts found matching: {pattern}")
            return

        print(f"Scripts matching '{pattern}':")
        for script in scripts:
            rel_path = script.relative_to(directory)
            tool_name = cls._path_to_tool_name(rel_path)
            print(f"  {rel_path} -> {tool_name}")

    @classmethod
    def _cli_run_script(
        cls,
        script: str,
        dry_run: bool = False,
        timeout: int = 300,
    ) -> None:
        """CLI handler for run command.

        Args:
            script: Path to the script to run.
            dry_run: If True, show command without executing.
            timeout: Execution timeout in seconds.
        """
        script_path = Path(script)
        if not script_path.exists():
            print(f"Error: Script not found: {script}")
            return

        # Read script to detect interpreter
        try:
            content = script_path.read_text()
            interpreter = cls._detect_interpreter(content, script_path, {})
        except OSError:
            interpreter = None

        cmd = [interpreter, str(script_path)] if interpreter else [str(script_path)]

        if dry_run:
            print(f"Would execute: {' '.join(cmd)}")
            return

        print(f"Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, timeout=timeout, check=False)
            print(f"Exit code: {result.returncode}")
        except subprocess.TimeoutExpired:
            print(f"Error: Script timed out after {timeout} seconds")
        except FileNotFoundError:
            print("Error: Interpreter or script not found")

    # =========================================================================
    # Plugin Metadata
    # =========================================================================

    @classmethod
    def get_plugin_metadata(cls) -> dict[str, Any]:
        """Return metadata about this plugin for CLI display.

        Returns:
            Dictionary with plugin metadata.
        """
        return {
            "name": "Scripts",
            "description": "Exposes shell scripts as MCP tools based on glob patterns",
        }


# Alias for compatibility
ScriptsPlugin = Plugin
