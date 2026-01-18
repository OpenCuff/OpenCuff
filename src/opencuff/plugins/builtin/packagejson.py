"""Package.json plugin for OpenCuff.

This plugin discovers and exposes npm/pnpm scripts from package.json files as MCP
tools, enabling AI coding agents to discover and execute project scripts through
governed, observable tool calls.

The plugin supports:
    - Script extraction from package.json scripts field
    - Optional descriptions from scripts-info field (OpenCuff convention)
    - Package manager auto-detection from lock files
    - Tool naming with character sanitization
    - Content-hash based caching

Example configuration:
    plugins:
      packagejson:
        type: in_source
        module: opencuff.plugins.builtin.packagejson
        config:
          package_json_path: ./package.json
          package_manager: auto
          scripts: "*"
          exclude_scripts: ""
          exclude_lifecycle_scripts: true
          cache_ttl: 300

SECURITY WARNING:
    Package.json scripts can execute arbitrary shell commands. Scripts have full
    access to the system, environment variables (which may contain secrets), and
    the filesystem. Before enabling this plugin, audit the package.json scripts
    in your project to ensure they do not perform destructive or sensitive
    operations when invoked by AI agents.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
# Constants
# =============================================================================

DANGEROUS_CHARS: set[str] = {";", "|", "&", "`", "$", "(", ")", "\n", "\r"}

LIFECYCLE_SCRIPTS: set[str] = {
    "preinstall",
    "install",
    "postinstall",
    "preuninstall",
    "uninstall",
    "postuninstall",
    "prepublish",
    "prepare",
    "prepublishOnly",
    "prepack",
    "postpack",
}

SAFE_SCRIPT_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_:.\-]*$")

ALLOWED_PACKAGE_MANAGERS = {"npm", "pnpm"}

# Security-sensitive environment variables that cannot be overridden via tool args
BLOCKED_ENV_VARS: set[str] = {
    "PATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "NODE_OPTIONS",
    "PYTHONPATH",
    "RUBYOPT",
    "PERL5OPT",
}


# =============================================================================
# Security Functions
# =============================================================================


def sanitize_arguments(args: str) -> list[str]:
    """Sanitize extra arguments and split into list.

    Blocks shell metacharacters that could enable command injection.

    Args:
        args: Raw argument string from user.

    Returns:
        List of sanitized argument tokens.

    Raises:
        ValueError: If dangerous characters are detected.
    """
    for char in DANGEROUS_CHARS:
        if char in args:
            raise ValueError(f"Dangerous character in arguments: {repr(char)}")
    return shlex.split(args)


def validate_script_name(name: str) -> bool:
    """Ensure script name is safe.

    Args:
        name: Script name to validate.

    Returns:
        True if the name is safe, False otherwise.
    """
    return bool(SAFE_SCRIPT_PATTERN.match(name))


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class NpmScript:
    """Represents an npm/pnpm script.

    Attributes:
        name: The script name as defined in package.json.
        command: The shell command that the script executes.
        description: Optional human-readable description.
    """

    name: str
    command: str
    description: str | None = None

    def to_tool_name(self, package_manager: str) -> str:
        """Convert script name to valid MCP tool name.

        Sanitization rules:
            - `-` (hyphen) -> `_` (underscore)
            - `:` (colon) -> `__` (double underscore)
            - `.` (dot) -> `_` (underscore)

        Args:
            package_manager: The package manager prefix (npm or pnpm).

        Returns:
            A tool name prefixed with the package manager.
        """
        sanitized = self.name.replace("-", "_").replace(":", "__").replace(".", "_")
        return f"{package_manager}_{sanitized}"

    def to_tool_definition(self, package_manager: str) -> ToolDefinition:
        """Convert to MCP ToolDefinition.

        Args:
            package_manager: The package manager (npm or pnpm).

        Returns:
            A ToolDefinition for this script.
        """
        description = self.description or f"Run '{package_manager} run {self.name}'"

        return ToolDefinition(
            name=self.to_tool_name(package_manager),
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "extra_args": {
                        "type": "string",
                        "description": "Additional arguments to pass to the script",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds",
                        "minimum": 1,
                    },
                    "env": {
                        "type": "object",
                        "description": "Additional environment variables",
                        "additionalProperties": {"type": "string"},
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Print command without executing",
                    },
                },
            },
            returns={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "script_name": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                },
            },
        )


# =============================================================================
# Configuration
# =============================================================================


class PackageJsonPluginConfig(BaseModel):
    """Configuration for the package.json plugin.

    Attributes:
        package_json_path: Path to package.json (relative or absolute).
        package_manager: Package manager: npm, pnpm, or auto.
        scripts: Comma-separated fnmatch patterns for scripts to expose.
        exclude_scripts: Comma-separated fnmatch patterns for scripts to exclude.
        exclude_lifecycle_scripts: Exclude npm lifecycle scripts.
        cache_ttl: Cache TTL in seconds (0 to disable).
        working_directory: Working directory for script execution.
        default_timeout: Default timeout for script execution in seconds.
        environment: Additional environment variables for all scripts.
        expose_list_scripts: Expose a list_scripts tool.
    """

    package_json_path: str = Field(
        default="./package.json",
        description="Path to package.json (relative to working_directory or absolute)",
    )

    package_manager: Literal["npm", "pnpm", "auto"] = Field(
        default="auto",
        description="Package manager: npm, pnpm, or auto (detect from lock files)",
    )

    scripts: str = Field(
        default="*",
        description=(
            "Comma-separated fnmatch patterns for scripts to expose. "
            "Uses Unix shell-style wildcards (*, ?, [seq], [!seq])."
        ),
    )

    exclude_scripts: str = Field(
        default="",
        description=(
            "Comma-separated fnmatch patterns for scripts to exclude. "
            "Uses Unix shell-style wildcards (*, ?, [seq], [!seq])."
        ),
    )

    exclude_lifecycle_scripts: bool = Field(
        default=True,
        description="Exclude npm lifecycle scripts (pre/post install, prepare, etc.)",
    )

    cache_ttl: int = Field(
        default=300,
        ge=0,
        description="Cache TTL in seconds for extracted scripts (0 to disable)",
    )

    working_directory: str = Field(
        default=".",
        description="Working directory for script execution",
    )

    default_timeout: int = Field(
        default=300,
        ge=1,
        description="Default timeout for script execution in seconds",
    )

    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Additional environment variables for script execution",
    )

    expose_list_scripts: bool = Field(
        default=True,
        description="Expose a list_scripts tool for script discovery",
    )

    @field_validator("package_manager")
    @classmethod
    def validate_package_manager(cls, v: str) -> str:
        """Validate package manager value."""
        if v not in ALLOWED_PACKAGE_MANAGERS and v != "auto":
            allowed = ", ".join(sorted(ALLOWED_PACKAGE_MANAGERS))
            msg = f"Invalid package_manager: {v}. Must be one of {allowed} or 'auto'"
            raise ValueError(msg)
        return v


# =============================================================================
# Script Extraction
# =============================================================================


class ScriptExtractor:
    """Extracts scripts from package.json files."""

    async def extract(self, package_json_path: Path) -> list[NpmScript]:
        """Parse package.json and extract scripts.

        Args:
            package_json_path: Path to the package.json file.

        Returns:
            List of discovered scripts.

        Raises:
            FileNotFoundError: If package.json does not exist.
            ValueError: If package.json contains invalid JSON.
        """
        return await asyncio.to_thread(self._extract_sync, package_json_path)

    def _extract_sync(self, package_json_path: Path) -> list[NpmScript]:
        """Synchronous extraction implementation.

        Args:
            package_json_path: Path to the package.json file.

        Returns:
            List of extracted scripts.
        """
        if not package_json_path.exists():
            raise FileNotFoundError(f"package.json not found: {package_json_path}")

        try:
            content = package_json_path.read_text()
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {package_json_path}: {e}") from e

        scripts_dict = data.get("scripts", {})
        scripts_info = data.get("scripts-info", {})

        scripts: list[NpmScript] = []
        for name, command in scripts_dict.items():
            if not validate_script_name(name):
                logger.warning("skipping_invalid_script_name: name=%s", name)
                continue

            scripts.append(
                NpmScript(
                    name=name,
                    command=command,
                    description=scripts_info.get(name),
                )
            )

        return scripts


# =============================================================================
# Package Manager Detection
# =============================================================================


class PackageManagerDetector:
    """Detects the package manager to use based on lock files."""

    LOCK_FILE_MAPPING: dict[str, str] = {
        "pnpm-lock.yaml": "pnpm",
        "package-lock.json": "npm",
    }

    def detect(self, working_directory: Path, default: str = "npm") -> str:
        """Auto-detect package manager from lock files.

        Lock file precedence: pnpm-lock.yaml > package-lock.json

        Args:
            working_directory: Directory to search for lock files.
            default: Default package manager if no lock file found.

        Returns:
            The detected or default package manager.
        """
        for lock_file, manager in self.LOCK_FILE_MAPPING.items():
            if (working_directory / lock_file).exists():
                return manager
        return default


# =============================================================================
# Script Filtering
# =============================================================================


class ScriptFilter:
    """Filters scripts based on include/exclude patterns.

    Uses fnmatch (Unix shell-style wildcards), NOT regex.
    Supported patterns: *, ?, [seq], [!seq]
    """

    def __init__(
        self,
        include_patterns: list[str],
        exclude_patterns: list[str],
        exclude_lifecycle: bool = True,
    ) -> None:
        """Initialize the script filter.

        Args:
            include_patterns: Patterns for scripts to include.
            exclude_patterns: Patterns for scripts to exclude.
            exclude_lifecycle: Whether to exclude lifecycle scripts.
        """
        self.include_patterns = include_patterns or ["*"]
        self.exclude_patterns = exclude_patterns or []
        self.exclude_lifecycle = exclude_lifecycle

    def matches(self, script_name: str) -> bool:
        """Check if script matches filter criteria.

        Exclusions are checked first (lifecycle, then patterns), then inclusions.

        Args:
            script_name: The script name to check.

        Returns:
            True if the script should be included, False otherwise.
        """
        # First check lifecycle exclusion
        if self.exclude_lifecycle and script_name in LIFECYCLE_SCRIPTS:
            return False

        # Then check exclude patterns
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(script_name, pattern):
                return False

        # Finally check include patterns
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(script_name, pattern):
                return True

        return False

    @classmethod
    def from_config(cls, config: PackageJsonPluginConfig) -> ScriptFilter:
        """Create filter from configuration.

        Args:
            config: The package.json plugin configuration.

        Returns:
            A configured ScriptFilter instance.
        """
        include = [p.strip() for p in config.scripts.split(",") if p.strip()]
        exclude = [p.strip() for p in config.exclude_scripts.split(",") if p.strip()]
        return cls(include, exclude, config.exclude_lifecycle_scripts)


# =============================================================================
# Caching
# =============================================================================


@dataclass
class CacheEntry:
    """Cache entry for extracted scripts.

    Attributes:
        scripts: Cached list of extracted scripts.
        timestamp: Unix timestamp when the cache entry was created.
        package_json_hash: SHA-256 hash of the package.json content.
        config_hash: Hash of relevant configuration fields.
    """

    scripts: list[NpmScript]
    timestamp: float
    package_json_hash: str
    config_hash: str

    def is_valid(
        self,
        ttl: int,
        current_hash: str,
        current_config_hash: str,
        current_time: float | None = None,
    ) -> bool:
        """Check if cache entry is still valid.

        Args:
            ttl: Cache time-to-live in seconds.
            current_hash: Current SHA-256 hash of package.json content.
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

        if current_hash != self.package_json_hash:
            return False

        return current_config_hash == self.config_hash


class ScriptCache:
    """Cache for extracted package.json scripts with content-based invalidation."""

    def __init__(self) -> None:
        """Initialize the cache."""
        self._cache: dict[str, CacheEntry] = {}

    @staticmethod
    def _hash_content(content: str) -> str:
        """Compute SHA-256 hash of content.

        Args:
            content: The content to hash.

        Returns:
            Hex-encoded SHA-256 hash.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_config(config: PackageJsonPluginConfig) -> str:
        """Compute hash of configuration fields that affect extraction.

        Args:
            config: The plugin configuration.

        Returns:
            Hex-encoded SHA-256 hash of relevant config fields.
        """
        relevant_fields = (
            config.scripts,
            config.exclude_scripts,
            str(config.exclude_lifecycle_scripts),
        )
        combined = "|".join(str(f) for f in relevant_fields)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def get(
        self,
        package_json_path: str,
        ttl: int,
        config: PackageJsonPluginConfig,
    ) -> CacheEntry | None:
        """Get cached scripts if valid.

        Args:
            package_json_path: Path to the package.json.
            ttl: Cache time-to-live in seconds.
            config: The plugin configuration.

        Returns:
            The cache entry if valid, None otherwise.
        """
        entry = self._cache.get(package_json_path)
        if entry is None:
            return None

        path = Path(package_json_path)
        try:
            content = path.read_text()
            current_hash = self._hash_content(content)
            current_config_hash = self._hash_config(config)
        except OSError:
            del self._cache[package_json_path]
            return None

        if entry.is_valid(ttl, current_hash, current_config_hash):
            return entry

        del self._cache[package_json_path]
        return None

    def set(
        self,
        package_json_path: str,
        scripts: list[NpmScript],
        config: PackageJsonPluginConfig,
    ) -> None:
        """Cache extracted scripts.

        Args:
            package_json_path: Path to the package.json.
            scripts: The extracted scripts.
            config: The plugin configuration.
        """
        path = Path(package_json_path)
        try:
            content = path.read_text()
            content_hash = self._hash_content(content)
            config_hash = self._hash_config(config)
        except OSError:
            return

        self._cache[package_json_path] = CacheEntry(
            scripts=scripts,
            timestamp=time.time(),
            package_json_hash=content_hash,
            config_hash=config_hash,
        )

    def invalidate(self, package_json_path: str | None = None) -> None:
        """Invalidate cache entries.

        Args:
            package_json_path: Path to invalidate, or None to clear all.
        """
        if package_json_path is None:
            self._cache.clear()
        elif package_json_path in self._cache:
            del self._cache[package_json_path]

    def has_entries(self) -> bool:
        """Check if cache has any entries.

        Note: This does not validate entry freshness. Use get() to check
        if a specific entry is still valid.

        Returns:
            True if cache is not empty, False otherwise.
        """
        return len(self._cache) > 0


# =============================================================================
# Plugin Implementation
# =============================================================================


class Plugin(InSourcePlugin):
    """Package.json plugin exposing npm/pnpm scripts as MCP tools.

    This plugin discovers package.json scripts and exposes them as tools that
    can be invoked by AI coding agents. It supports package manager auto-detection
    and configurable script filtering.

    Configuration options:
        package_json_path: Path to package.json (default: ./package.json)
        package_manager: npm, pnpm, or auto (default: auto)
        scripts: Comma-separated fnmatch patterns for scripts to expose
        exclude_scripts: Patterns for scripts to exclude
        exclude_lifecycle_scripts: Exclude lifecycle scripts (default: true)
        cache_ttl: Cache TTL in seconds (default: 300)
    """

    def __init__(
        self,
        config: dict[str, Any],
        instance_name: str = "packagejson",
        cache: ScriptCache | None = None,
    ) -> None:
        """Initialize the package.json plugin.

        Args:
            config: Plugin configuration dictionary.
            instance_name: Name for this plugin instance.
            cache: Optional ScriptCache instance for dependency injection.
        """
        super().__init__(config)
        self._plugin_config = PackageJsonPluginConfig.model_validate(config)
        self._instance_name = instance_name
        self._scripts: list[NpmScript] = []
        self._tool_to_script: dict[str, str] = {}
        self._cache = cache if cache is not None else ScriptCache()
        self._extractor = ScriptExtractor()
        self._detector = PackageManagerDetector()
        self._package_manager: str | None = None
        self._initialized = False

    def _resolve_package_json_path(self) -> Path:
        """Resolve and validate package.json path.

        Returns:
            Absolute path to the package.json file.
        """
        package_json = Path(self._plugin_config.package_json_path)
        working_dir = Path(self._plugin_config.working_directory).resolve()

        if not package_json.is_absolute():
            package_json = working_dir / package_json

        return package_json.resolve()

    def _resolve_working_directory(self) -> Path:
        """Resolve the working directory.

        Returns:
            Absolute path to the working directory.
        """
        return Path(self._plugin_config.working_directory).resolve()

    async def _detect_package_manager(self) -> None:
        """Detect and set the package manager."""
        if self._plugin_config.package_manager != "auto":
            self._package_manager = self._plugin_config.package_manager
            return

        working_dir = self._resolve_working_directory()
        self._package_manager = self._detector.detect(working_dir)
        logger.info(
            "package_manager_detected: manager=%s, directory=%s",
            self._package_manager,
            str(working_dir),
        )

    async def _refresh_scripts(self) -> None:
        """Refresh script list from package.json."""
        package_json_path = self._resolve_package_json_path()

        if not package_json_path.exists():
            logger.warning(
                "package_json_not_found: path=%s",
                str(package_json_path),
            )
            self._scripts = []
            self._tool_to_script = {}
            return

        # Check cache first
        package_json_str = str(package_json_path)
        cached = self._cache.get(
            package_json_str,
            self._plugin_config.cache_ttl,
            self._plugin_config,
        )
        if cached is not None:
            logger.debug(
                "cache_hit: path=%s, script_count=%d",
                package_json_str,
                len(cached.scripts),
            )
            self._scripts = cached.scripts
            self._apply_filters()
            self._build_tool_mapping()
            return

        # Extract scripts
        try:
            all_scripts = await self._extractor.extract(package_json_path)
            logger.info(
                "scripts_extracted: path=%s, count=%d",
                package_json_str,
                len(all_scripts),
            )
        except (FileNotFoundError, ValueError) as e:
            logger.error(
                "extraction_failed: error=%s",
                str(e),
            )
            self._scripts = []
            self._tool_to_script = {}
            return

        # Store all scripts before filtering (for caching)
        self._scripts = all_scripts

        # Apply filters
        self._apply_filters()

        # Build tool mapping
        self._build_tool_mapping()

        # Update cache with unfiltered scripts
        if self._plugin_config.cache_ttl > 0:
            self._cache.set(package_json_str, all_scripts, self._plugin_config)

    def _apply_filters(self) -> None:
        """Apply include/exclude filters to scripts."""
        script_filter = ScriptFilter.from_config(self._plugin_config)
        self._scripts = [s for s in self._scripts if script_filter.matches(s.name)]

    def _build_tool_mapping(self) -> None:
        """Build the tool name to script name mapping."""
        if self._package_manager is None:
            return
        self._tool_to_script = {
            s.to_tool_name(self._package_manager): s.name for s in self._scripts
        }

    async def initialize(self) -> None:
        """Initialize plugin and discover scripts."""
        await self._detect_package_manager()
        await self._refresh_scripts()
        self._initialized = True
        logger.info(
            "plugin_initialized: instance=%s, scripts=%d, package_manager=%s",
            self._instance_name,
            len(self._scripts),
            self._package_manager,
        )

    async def shutdown(self) -> None:
        """Clean up plugin resources."""
        self._cache.invalidate()
        self._scripts = []
        self._tool_to_script = {}
        self._package_manager = None
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

        package_json_path = self._resolve_package_json_path()
        return package_json_path.exists()

    async def detailed_health_check(self) -> dict[str, Any]:
        """Perform a detailed health check with diagnostic information.

        Returns:
            Dictionary containing health status and diagnostic details.
        """
        package_json_path = self._resolve_package_json_path()
        package_json_exists = package_json_path.exists()

        healthy = self._initialized and package_json_exists

        return {
            "healthy": healthy,
            "initialized": self._initialized,
            "package_json_path": str(package_json_path),
            "package_json_exists": package_json_exists,
            "package_manager": self._package_manager,
            "script_count": len(self._scripts),
            "cache_has_entries": self._cache.has_entries(),
            "cache_ttl": self._plugin_config.cache_ttl,
        }

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration reload.

        Args:
            new_config: The new configuration dictionary.
        """
        new_validated = PackageJsonPluginConfig.model_validate(new_config)

        # Check if cache-affecting settings changed
        cache_invalidation_needed = (
            new_validated.package_json_path != self._plugin_config.package_json_path
            or new_validated.scripts != self._plugin_config.scripts
            or new_validated.exclude_scripts != self._plugin_config.exclude_scripts
            or new_validated.exclude_lifecycle_scripts
            != self._plugin_config.exclude_lifecycle_scripts
        )

        # Check if package manager changed
        pm_changed = (
            new_validated.package_manager != self._plugin_config.package_manager
        )

        self._plugin_config = new_validated
        self.config = new_config

        # Re-detect package manager if needed
        if pm_changed:
            await self._detect_package_manager()

        # Invalidate cache and refresh if needed
        if cache_invalidation_needed or pm_changed:
            self._cache.invalidate()
            await self._refresh_scripts()

        logger.info(
            "config_reloaded: instance=%s, cache_invalidated=%s",
            self._instance_name,
            cache_invalidation_needed,
        )

    def get_tools(self) -> list[ToolDefinition]:
        """Return tool definitions for discovered scripts.

        Returns:
            List of ToolDefinition objects for all exposed scripts.
        """
        tools: list[ToolDefinition] = []

        if self._package_manager is None:
            return tools

        # Optionally add list_scripts tool
        if self._plugin_config.expose_list_scripts:
            tools.append(
                ToolDefinition(
                    name=f"{self._package_manager}_list_scripts",
                    description=f"List all available {self._package_manager} scripts",
                    parameters={"type": "object", "properties": {}},
                    returns={
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "command": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                    },
                )
            )

        # Add tool for each script
        for script in self._scripts:
            tools.append(script.to_tool_definition(self._package_manager))

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute the requested npm/pnpm script.

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

        if self._package_manager is None:
            return ToolResult(
                success=False,
                error="Package manager not detected",
            )

        # Handle list_scripts tool
        if tool_name == f"{self._package_manager}_list_scripts":
            return await self._list_scripts()

        # Find the script
        script_name = self._tool_to_script.get(tool_name)
        if script_name is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
            )

        return await self._execute_script(script_name, arguments)

    async def _list_scripts(self) -> ToolResult:
        """List all available scripts.

        Returns:
            ToolResult with script list.
        """
        scripts_data = [
            {
                "name": s.name,
                "command": s.command,
                "description": s.description,
            }
            for s in self._scripts
        ]
        return ToolResult(
            success=True,
            data=scripts_data,
        )

    async def _execute_script(
        self,
        script_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute an npm/pnpm script.

        Args:
            script_name: The script name to execute.
            arguments: Execution arguments (extra_args, timeout, dry_run, env).

        Returns:
            ToolResult with execution outcome.
        """
        extra_args = arguments.get("extra_args", "")
        timeout = arguments.get("timeout", self._plugin_config.default_timeout)
        env_overrides: dict[str, str] = arguments.get("env", {})
        dry_run = arguments.get("dry_run", False)

        # Validate environment variable overrides (security check)
        blocked_keys = set(env_overrides.keys()) & BLOCKED_ENV_VARS
        if blocked_keys:
            blocked_list = ", ".join(sorted(blocked_keys))
            return ToolResult(
                success=False,
                error=f"Cannot override security-sensitive env vars: {blocked_list}",
            )

        # Type narrowing: _package_manager is checked in call_tool before reaching here
        assert self._package_manager is not None, "Package manager not initialized"

        # Build command
        cmd: list[str] = [self._package_manager, "run", script_name]

        # Add extra arguments if provided
        if extra_args:
            try:
                sanitized = sanitize_arguments(extra_args)
                cmd.extend(["--", *sanitized])
            except ValueError as e:
                return ToolResult(
                    success=False,
                    error=f"Invalid extra_args: {e}",
                )

        # Handle dry run
        if dry_run:
            return ToolResult(
                success=True,
                data=f"Would execute: {' '.join(cmd)}",
            )

        # Build environment
        env = os.environ.copy()
        env.update(self._plugin_config.environment)
        env.update(env_overrides)

        # Execute
        working_dir = self._resolve_working_directory()
        start_time = time.time()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(working_dir),
                env=env,
            )

            stdout, _ = await asyncio.wait_for(
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
                    "exit_code": exit_code,
                    "script_name": script_name,
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
                error=f"Package manager not found: {self._package_manager}",
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
        """Discover if this plugin is applicable to the given directory.

        Checks for the presence of package.json and extracts script information.
        Also detects the package manager from lock files.

        Args:
            directory: The directory to scan for package.json.

        Returns:
            DiscoveryResult indicating applicability and suggested config.
        """
        package_json_path = directory / "package.json"

        if not package_json_path.exists():
            return DiscoveryResult(
                applicable=False,
                confidence=0.0,
                suggested_config={},
                description="No package.json found",
            )

        # Try to parse package.json
        try:
            content = package_json_path.read_text()
            data = json.loads(content)
        except json.JSONDecodeError:
            return DiscoveryResult(
                applicable=False,
                confidence=0.0,
                suggested_config={},
                description="package.json contains invalid JSON",
            )

        # Extract scripts
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        script_names = list(scripts.keys())

        # Detect package manager
        package_manager = cls._detect_package_manager_static(directory)

        # Build description
        script_count = len(script_names)
        description = (
            f"Found package.json with {script_count} scripts ({package_manager})"
        )

        # Build suggested configuration
        suggested_config = {
            "package_json_path": "./package.json",
            "package_manager": "auto",
            "scripts": "*",
            "exclude_scripts": "",
            "exclude_lifecycle_scripts": True,
            "cache_ttl": 300,
            "working_directory": ".",
            "default_timeout": 300,
            "expose_list_scripts": True,
        }

        # Build warnings list
        warnings: list[str] = []

        # Build tool names as they would appear in the MCP server
        tool_names = [f"{package_manager}_list_scripts"]
        tool_names.extend(f"{package_manager}_{name}" for name in script_names)

        return DiscoveryResult(
            applicable=True,
            confidence=1.0,
            suggested_config=suggested_config,
            description=description,
            warnings=warnings,
            discovered_items=tool_names,
        )

    @staticmethod
    def _detect_package_manager_static(directory: Path) -> str:
        """Detect package manager from lock files (static method for discovery).

        Lock file precedence (first match wins):
            1. pnpm-lock.yaml -> pnpm
            2. yarn.lock -> yarn
            3. bun.lockb -> bun
            4. package-lock.json -> npm
            5. (default) -> npm

        Args:
            directory: The directory to check for lock files.

        Returns:
            The detected package manager name.
        """
        if (directory / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (directory / "yarn.lock").exists():
            return "yarn"
        if (directory / "bun.lockb").exists():
            return "bun"
        if (directory / "package-lock.json").exists():
            return "npm"
        return "npm"  # Default

    # =========================================================================
    # CLI Interface
    # =========================================================================

    @classmethod
    def get_cli_commands(cls) -> list[CLICommand]:
        """Return CLI commands this plugin provides.

        Commands:
            - list-scripts: List available npm/pnpm scripts
            - run-script: Run a specific script

        Returns:
            List of CLICommand definitions.
        """
        return [
            CLICommand(
                name="list-scripts",
                help="List available npm/pnpm scripts",
                callback=cls._cli_list_scripts,
                options=[
                    CLIOption(
                        name="--package-json",
                        help="Path to package.json",
                        default="./package.json",
                    ),
                ],
            ),
            CLICommand(
                name="run-script",
                help="Run a specific npm/pnpm script",
                callback=cls._cli_run_script,
                arguments=[
                    CLIArgument(
                        name="script",
                        help="Script name to run",
                        required=True,
                    ),
                ],
                options=[
                    CLIOption(
                        name="--dry-run",
                        help="Show command without executing",
                        is_flag=True,
                        default=False,
                    ),
                    CLIOption(
                        name="--timeout",
                        help="Execution timeout in seconds",
                        default=300,
                        type=int,
                    ),
                    CLIOption(
                        name="--package-json",
                        help="Path to package.json",
                        default="./package.json",
                    ),
                ],
            ),
        ]

    @classmethod
    def _cli_list_scripts(cls, package_json: str = "./package.json") -> None:
        """CLI handler for list-scripts command.

        Args:
            package_json: Path to the package.json file.
        """
        path = Path(package_json)
        if not path.exists():
            print(f"Error: package.json not found: {package_json}")
            return

        try:
            content = path.read_text()
            data = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in {package_json}: {e}")
            return

        scripts = data.get("scripts", {})
        if not scripts:
            print("No scripts found in package.json")
            return

        # Detect package manager
        directory = path.parent
        pm = cls._detect_package_manager_static(directory)

        print(f"Package Manager: {pm}")
        print("Available scripts:")
        for name, command in scripts.items():
            print(f"  {name:<20} {command}")

    @classmethod
    def _cli_run_script(
        cls,
        script: str,
        dry_run: bool = False,
        timeout: int = 300,
        package_json: str = "./package.json",
    ) -> None:
        """CLI handler for run-script command.

        Args:
            script: Name of the script to run.
            dry_run: If True, show command without executing.
            timeout: Execution timeout in seconds.
            package_json: Path to the package.json file.
        """
        import subprocess

        path = Path(package_json)
        if not path.exists():
            print(f"Error: package.json not found: {package_json}")
            return

        try:
            content = path.read_text()
            data = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in {package_json}: {e}")
            return

        scripts = data.get("scripts", {})
        if script not in scripts:
            print(f"Error: Script '{script}' not found in package.json")
            print(f"Available scripts: {', '.join(scripts.keys())}")
            return

        # Detect package manager
        directory = path.parent
        pm = cls._detect_package_manager_static(directory)

        cmd = [pm, "run", script]

        if dry_run:
            print(f"Would execute: {' '.join(cmd)}")
            return

        print(f"Running: {pm} run {script}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(directory),
                timeout=timeout,
            )
            print(f"Exit code: {result.returncode}")
        except subprocess.TimeoutExpired:
            print(f"Error: Script timed out after {timeout} seconds")
        except FileNotFoundError:
            print(f"Error: Package manager '{pm}' not found")

    # =========================================================================
    # Plugin Metadata
    # =========================================================================

    @classmethod
    def get_plugin_metadata(cls) -> dict[str, Any]:
        """Return metadata about this plugin for CLI display.

        Returns:
            Dictionary with plugin metadata including name and description.
        """
        return {
            "name": "Package.json",
            "description": "Exposes npm/pnpm scripts from package.json as MCP tools",
        }


# Alias for compatibility
PackageJsonPlugin = Plugin
