"""Makefile plugin for OpenCuff.

This plugin discovers and exposes Makefile targets as MCP tools, enabling AI
coding agents to discover and execute build targets, test commands, and other
Makefile-defined operations.

The plugin supports multiple target extraction strategies:
    - simple: Fast regex-based extraction for basic Makefiles
    - make_database: Accurate extraction using `make -pn` for complex Makefiles
    - auto: Intelligent selection based on Makefile characteristics

Example configuration:
    plugins:
      makefile:
        type: in_source
        module: opencuff.plugins.builtin.makefile
        config:
          makefile_path: ./Makefile
          targets: "build,test-*,install-*"
          exclude_targets: "*-internal"
          extractor: auto
          cache_ttl: 300

SECURITY WARNING:
    The `make -pn` command (used by `make_database` and `auto` strategies)
    executes $(shell ...) commands during parsing. For untrusted Makefiles,
    always use `extractor: simple` or set `trust_makefile: false`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import os
import re
import shlex
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from opencuff.plugins.base import InSourcePlugin, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ExtractorStrategy(str, Enum):
    """Target extraction strategy."""

    SIMPLE = "simple"
    MAKE_DATABASE = "make_database"
    AUTO = "auto"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class MakeTarget:
    """Represents a discovered Makefile target.

    Attributes:
        name: The target name as defined in the Makefile.
        description: Description extracted from ## comments.
        is_phony: Whether the target is declared as .PHONY.
        prerequisites: List of prerequisite targets.
        source_file: Path to the Makefile where target is defined.
        line_number: Line number in source file (if available).
    """

    name: str
    description: str | None = None
    is_phony: bool = False
    prerequisites: list[str] = field(default_factory=list)
    source_file: str | None = None
    line_number: int | None = None

    def to_tool_name(self) -> str:
        """Convert target name to valid MCP tool name.

        Returns:
            A tool name prefixed with 'make_' with special characters replaced.
        """
        safe_name = self.name.replace("-", "_").replace(".", "_")
        return f"make_{safe_name}"

    def to_tool_definition(self) -> dict[str, Any]:
        """Convert to MCP ToolDefinition dict.

        Returns:
            Dictionary suitable for constructing a ToolDefinition.
        """
        description = self.description or f"Execute 'make {self.name}'"
        if self.is_phony:
            description += " (phony target)"

        return {
            "name": self.to_tool_name(),
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "extra_args": {
                        "type": "string",
                        "description": "Additional arguments to pass to make",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds",
                        "minimum": 1,
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Print commands without executing (make -n)",
                        "default": False,
                    },
                },
            },
            "returns": {
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "target": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                },
            },
        }


# =============================================================================
# Configuration
# =============================================================================


class MakefilePluginConfig(BaseModel):
    """Configuration for the Makefile plugin.

    Attributes:
        makefile_path: Path to the Makefile (relative or absolute).
        targets: Comma-separated fnmatch patterns for targets to expose.
        exclude_targets: Comma-separated fnmatch patterns for targets to exclude.
        extractor: Target extraction strategy.
        trust_makefile: Whether to trust the Makefile for code execution.
        cache_ttl: Cache TTL in seconds (0 to disable).
        make_command: Path to the make binary.
        working_directory: Working directory for make execution.
        allow_parallel: Allow parallel execution with -j flag.
        parallel_jobs: Number of parallel jobs.
        default_timeout: Default timeout for target execution in seconds.
        environment: Additional environment variables for make execution.
        description_prefix: Comment prefix used for target descriptions.
        expose_list_targets: Expose a 'make_list_targets' tool.
    """

    makefile_path: str = Field(
        default="./Makefile",
        description="Path to the Makefile (relative to working_directory or absolute)",
    )

    targets: str = Field(
        default="*",
        description=(
            "Comma-separated fnmatch patterns for targets to expose. "
            "Uses Unix shell-style wildcards (*, ?, [seq], [!seq])."
        ),
    )

    exclude_targets: str = Field(
        default="",
        description=(
            "Comma-separated fnmatch patterns for targets to exclude. "
            "Uses Unix shell-style wildcards (*, ?, [seq], [!seq])."
        ),
    )

    extractor: ExtractorStrategy = Field(
        default=ExtractorStrategy.AUTO,
        description="Target extraction strategy: 'simple', 'make_database', or 'auto'",
    )

    trust_makefile: bool = Field(
        default=True,
        description=(
            "Whether to trust the Makefile for code execution. "
            "When False, forces 'simple' extractor."
        ),
    )

    cache_ttl: int = Field(
        default=300,
        ge=0,
        description="Cache TTL in seconds for extracted targets (0 to disable)",
    )

    make_command: str = Field(
        default="make",
        description="Path to the make binary",
    )

    working_directory: str | None = Field(
        default=None,
        description="Working directory for make execution",
    )

    allow_parallel: bool = Field(
        default=True,
        description="Allow parallel execution with -j flag",
    )

    parallel_jobs: int | None = Field(
        default=None,
        ge=1,
        description="Number of parallel jobs (default: CPU count)",
    )

    default_timeout: int = Field(
        default=300,
        ge=1,
        description="Default timeout for target execution in seconds",
    )

    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Additional environment variables for make execution",
    )

    description_prefix: str = Field(
        default="##",
        description="Comment prefix used for target descriptions",
    )

    expose_list_targets: bool = Field(
        default=True,
        description="Expose a 'make_list_targets' tool for target discovery",
    )

    @field_validator("targets", "exclude_targets")
    @classmethod
    def validate_patterns(cls, v: str) -> str:
        """Validate wildcard patterns syntax."""
        if not v:
            return v
        for pattern in v.split(","):
            pattern = pattern.strip()
            if not pattern:
                continue
            if "/" in pattern or "\\" in pattern:
                raise ValueError(
                    f"Invalid pattern: {pattern}. "
                    "Patterns should not contain path separators."
                )
        return v


# =============================================================================
# Errors
# =============================================================================


class ExtractorError(Exception):
    """Raised when target extraction fails.

    Attributes:
        message: Human-readable error message.
        makefile_path: Path to the Makefile (if applicable).
        cause: The underlying exception (if any).
    """

    def __init__(
        self,
        message: str,
        makefile_path: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.message = message
        self.makefile_path = makefile_path
        self.cause = cause
        super().__init__(message)


# =============================================================================
# Target Filtering
# =============================================================================


class TargetFilter:
    """Filters targets based on include/exclude patterns.

    Uses fnmatch (Unix shell-style wildcards), NOT regex.
    Supported patterns: *, ?, [seq], [!seq]
    """

    def __init__(
        self,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> None:
        """Initialize the target filter.

        Args:
            include_patterns: Patterns for targets to include.
            exclude_patterns: Patterns for targets to exclude.
        """
        self.include_patterns = include_patterns or ["*"]
        self.exclude_patterns = exclude_patterns or []

    def matches(self, target_name: str) -> bool:
        """Check if target matches filter criteria.

        Exclude patterns are checked first, then include patterns.

        Args:
            target_name: The target name to check.

        Returns:
            True if the target should be included, False otherwise.
        """
        # First check exclude patterns
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(target_name, pattern):
                return False

        # Then check include patterns
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(target_name, pattern):
                return True

        return False

    @classmethod
    def from_config(cls, config: MakefilePluginConfig) -> TargetFilter:
        """Create filter from configuration.

        Args:
            config: The Makefile plugin configuration.

        Returns:
            A configured TargetFilter instance.
        """
        include = [p.strip() for p in config.targets.split(",") if p.strip()]
        exclude = [p.strip() for p in config.exclude_targets.split(",") if p.strip()]
        return cls(include, exclude)


# =============================================================================
# Extractors
# =============================================================================


class ExtractorBase(ABC):
    """Abstract base class for Makefile target extractors."""

    @abstractmethod
    async def extract(self, makefile_path: Path) -> list[MakeTarget]:
        """Extract targets from the specified Makefile.

        Args:
            makefile_path: Path to the Makefile to parse.

        Returns:
            List of discovered targets.

        Raises:
            ExtractorError: If extraction fails.
        """
        ...

    @abstractmethod
    def supports_includes(self) -> bool:
        """Whether this extractor handles include directives."""
        ...

    @abstractmethod
    def supports_dynamic_targets(self) -> bool:
        """Whether this extractor handles dynamically generated targets."""
        ...


class SimpleExtractor(ExtractorBase):
    """Regex-based Makefile target extractor.

    Fast but limited - does not handle includes, variables, or dynamic targets.
    Safe for untrusted Makefiles since no code is executed.
    """

    # Match standard targets: "target: prerequisites" (excluding := assignments)
    TARGET_PATTERN = re.compile(
        r"^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:(?!=)",
        re.MULTILINE,
    )

    # Match .PHONY declarations
    PHONY_PATTERN = re.compile(
        r"^\.PHONY\s*:\s*(.+)$",
        re.MULTILINE,
    )

    def __init__(self, description_prefix: str = "##") -> None:
        """Initialize the extractor.

        Args:
            description_prefix: Comment prefix for target descriptions.
        """
        self.description_prefix = description_prefix
        # Build patterns with custom prefix
        prefix_escaped = re.escape(description_prefix)
        self.desc_pattern = re.compile(
            rf"^{prefix_escaped}\s*(.+)\n([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:",
            re.MULTILINE,
        )
        self.inline_desc_pattern = re.compile(
            rf"^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:[^#\n]*{prefix_escaped}\s*(.+)$",
            re.MULTILINE,
        )

    def _extract_sync(self, makefile_path: Path) -> list[MakeTarget]:
        """Synchronous extraction implementation.

        Args:
            makefile_path: Path to the Makefile.

        Returns:
            List of extracted targets.

        Raises:
            ExtractorError: If the Makefile cannot be read.
        """
        try:
            content = makefile_path.read_text()
        except OSError as e:
            raise ExtractorError(
                f"Cannot read Makefile: {e}",
                makefile_path=str(makefile_path),
                cause=e,
            ) from e

        # Find all .PHONY targets
        phony_targets: set[str] = set()
        for match in self.PHONY_PATTERN.finditer(content):
            phony_targets.update(match.group(1).split())

        # Find descriptions from Format 1 (## comments before targets)
        descriptions: dict[str, str] = {}
        for match in self.desc_pattern.finditer(content):
            descriptions[match.group(2)] = match.group(1).strip()

        # Find descriptions from Format 2 (## inline comments) - takes precedence
        for match in self.inline_desc_pattern.finditer(content):
            target_name = match.group(1)
            descriptions[target_name] = match.group(2).strip()

        # Find all targets
        targets: list[MakeTarget] = []
        seen: set[str] = set()

        for match in self.TARGET_PATTERN.finditer(content):
            name = match.group(1)
            # Skip internal targets (starting with .)
            if name.startswith("."):
                continue
            # Skip duplicates
            if name in seen:
                continue
            seen.add(name)

            targets.append(
                MakeTarget(
                    name=name,
                    description=descriptions.get(name),
                    is_phony=name in phony_targets,
                    source_file=str(makefile_path),
                )
            )

        return targets

    async def extract(self, makefile_path: Path) -> list[MakeTarget]:
        """Extract targets from Makefile using regex.

        This method is async to conform to the ExtractorBase interface.
        File I/O is wrapped with asyncio.to_thread() to avoid blocking.

        Args:
            makefile_path: Path to the Makefile.

        Returns:
            List of discovered targets.
        """
        return await asyncio.to_thread(self._extract_sync, makefile_path)

    def supports_includes(self) -> bool:
        """Simple extractor does not handle includes."""
        return False

    def supports_dynamic_targets(self) -> bool:
        """Simple extractor does not handle dynamic targets."""
        return False


class MakeDatabaseExtractor(ExtractorBase):
    """Target extractor using make's database dump.

    WARNING: This extractor executes $(shell ...) commands during parsing.
    Only use with trusted Makefiles.
    """

    # Pattern to match target entries in make database output
    DB_TARGET_PATTERN = re.compile(
        r"^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:",
        re.MULTILINE,
    )

    def __init__(
        self,
        make_command: str = "make",
        working_directory: str | None = None,
        timeout: float = 30.0,
        description_prefix: str = "##",
    ) -> None:
        """Initialize the extractor.

        Args:
            make_command: Path to the make binary.
            working_directory: Directory to run make in.
            timeout: Timeout for make command in seconds.
            description_prefix: Comment prefix for target descriptions.
        """
        self.make_command = make_command
        self.working_directory = working_directory
        self.timeout = timeout
        self.description_prefix = description_prefix

    async def extract(self, makefile_path: Path) -> list[MakeTarget]:
        """Extract targets using make database dump.

        WARNING: This method executes make -pn which will evaluate
        $(shell ...) and other Make functions.

        Args:
            makefile_path: Path to the Makefile.

        Returns:
            List of discovered targets.

        Raises:
            ExtractorError: If extraction fails.
        """
        cmd = [
            self.make_command,
            "-f",
            str(makefile_path),
            "--print-data-base",
            "--dry-run",
            "--no-builtin-rules",
            "--no-builtin-variables",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_directory or str(makefile_path.parent),
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

            # Note: make -pn may exit with non-zero if targets fail,
            # but database is still output
            output = stdout.decode("utf-8", errors="replace")
            return self._parse_database(output, makefile_path)

        except TimeoutError as e:
            raise ExtractorError(
                f"make database dump timed out after {self.timeout}s",
                makefile_path=str(makefile_path),
            ) from e
        except FileNotFoundError as e:
            raise ExtractorError(
                f"make command not found: {self.make_command}",
                makefile_path=str(makefile_path),
            ) from e
        except OSError as e:
            raise ExtractorError(
                f"Failed to run make: {e}",
                makefile_path=str(makefile_path),
                cause=e,
            ) from e

    def _parse_database(
        self,
        output: str,
        makefile_path: Path,
    ) -> list[MakeTarget]:
        """Parse make database output into targets.

        Args:
            output: Raw output from make -pn.
            makefile_path: Path to the Makefile (for descriptions).

        Returns:
            List of parsed targets.
        """
        targets: list[MakeTarget] = []
        seen: set[str] = set()

        # Find the "# Files" section in the database output
        files_section_match = re.search(r"^# Files$", output, re.MULTILINE)
        if files_section_match:
            output = output[files_section_match.end() :]

        # Find phony targets from the database
        phony_targets: set[str] = set()
        phony_match = re.search(r"^\.PHONY:\s*(.+)$", output, re.MULTILINE)
        if phony_match:
            phony_targets.update(phony_match.group(1).split())

        # Also find phony markers in target metadata
        phony_marker_pattern = re.compile(
            r"^([a-zA-Z_][a-zA-Z0-9_.-]*):\n(?:.*\n)*?#\s+Phony target",
            re.MULTILINE,
        )
        for match in phony_marker_pattern.finditer(output):
            phony_targets.add(match.group(1))

        # Find all targets in the database
        for match in self.DB_TARGET_PATTERN.finditer(output):
            name = match.group(1)

            # Skip internal targets
            if name.startswith(".") or name.startswith("__"):
                continue

            # Skip duplicates
            if name in seen:
                continue
            seen.add(name)

            targets.append(
                MakeTarget(
                    name=name,
                    is_phony=name in phony_targets,
                    source_file=str(makefile_path),
                )
            )

        # Extract descriptions from original file
        descriptions = self._extract_descriptions(makefile_path)
        for target in targets:
            target.description = descriptions.get(target.name)

        return targets

    def _extract_descriptions(self, makefile_path: Path) -> dict[str, str]:
        """Extract descriptions from Makefile comments.

        Args:
            makefile_path: Path to the Makefile.

        Returns:
            Mapping of target names to descriptions.
        """
        descriptions: dict[str, str] = {}

        try:
            content = makefile_path.read_text()
        except OSError:
            return descriptions

        prefix_escaped = re.escape(self.description_prefix)

        # Format 1: ## comment on line before target
        pattern1 = re.compile(
            rf"^{prefix_escaped}\s*(.+)\n([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:",
            re.MULTILINE,
        )
        for match in pattern1.finditer(content):
            descriptions[match.group(2)] = match.group(1).strip()

        # Format 2: target: ## inline comment (takes precedence)
        pattern2 = re.compile(
            rf"^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:[^#\n]*{prefix_escaped}\s*(.+)$",
            re.MULTILINE,
        )
        for match in pattern2.finditer(content):
            descriptions[match.group(1)] = match.group(2).strip()

        return descriptions

    def supports_includes(self) -> bool:
        """Make database extractor handles includes."""
        return True

    def supports_dynamic_targets(self) -> bool:
        """Make database extractor handles dynamic targets."""
        return True


class ExtractorSelector:
    """Coordinates extraction strategy selection based on Makefile analysis.

    This class does not perform extraction itself, but rather selects and
    delegates to the appropriate extractor (SimpleExtractor or
    MakeDatabaseExtractor) based on the configured strategy or auto-detection.

    WARNING: When AUTO is selected, this may use make_database extraction
    which executes $(shell ...) commands.
    """

    # Indicators that require make database extraction
    COMPLEX_INDICATORS = [
        re.compile(r"^include\s+", re.MULTILINE),
        re.compile(r"^-include\s+", re.MULTILINE),
        re.compile(r"^sinclude\s+", re.MULTILINE),
        re.compile(r"\$\(shell\s+", re.MULTILINE),
        re.compile(r"\$\(wildcard\s+", re.MULTILINE),
        re.compile(r"\$\(foreach\s+", re.MULTILINE),
        re.compile(r"\$\(eval\s+", re.MULTILINE),
    ]

    def __init__(
        self,
        simple_extractor: SimpleExtractor,
        database_extractor: MakeDatabaseExtractor,
        trust_makefile: bool = True,
    ) -> None:
        """Initialize the hybrid extractor.

        Args:
            simple_extractor: The simple regex-based extractor.
            database_extractor: The make database extractor.
            trust_makefile: Whether to trust the Makefile.
        """
        self.simple_extractor = simple_extractor
        self.database_extractor = database_extractor
        self.trust_makefile = trust_makefile

    async def extract(
        self,
        makefile_path: Path,
        strategy: ExtractorStrategy = ExtractorStrategy.AUTO,
    ) -> tuple[list[MakeTarget], ExtractorStrategy]:
        """Extract targets using selected or auto-detected strategy.

        Args:
            makefile_path: Path to the Makefile.
            strategy: The extraction strategy to use.

        Returns:
            Tuple of (targets, strategy_used).

        Raises:
            ExtractorError: If extraction fails.
        """
        if strategy == ExtractorStrategy.SIMPLE:
            targets = await self.simple_extractor.extract(makefile_path)
            return targets, ExtractorStrategy.SIMPLE

        if strategy == ExtractorStrategy.MAKE_DATABASE:
            if not self.trust_makefile:
                raise ExtractorError(
                    "make_database strategy requires trust_makefile=True. "
                    "This Makefile is not trusted and make -pn would execute code.",
                    makefile_path=str(makefile_path),
                )
            targets = await self.database_extractor.extract(makefile_path)
            return targets, ExtractorStrategy.MAKE_DATABASE

        # Auto-detect strategy
        selected = self._analyze_complexity(makefile_path)

        # If Makefile is not trusted, force simple strategy
        if not self.trust_makefile and selected == ExtractorStrategy.MAKE_DATABASE:
            logger.warning(
                "untrusted_makefile_forced_simple: "
                "Makefile contains complex features but trust_makefile=False, "
                "path=%s",
                str(makefile_path),
            )
            selected = ExtractorStrategy.SIMPLE

        if selected == ExtractorStrategy.SIMPLE:
            targets = await self.simple_extractor.extract(makefile_path)
        else:
            targets = await self.database_extractor.extract(makefile_path)

        return targets, selected

    def _analyze_complexity(self, makefile_path: Path) -> ExtractorStrategy:
        """Analyze Makefile to determine best extraction strategy.

        Args:
            makefile_path: Path to the Makefile.

        Returns:
            The recommended extraction strategy.
        """
        try:
            content = makefile_path.read_text()
        except OSError:
            # If we can't read the file, try database approach
            return ExtractorStrategy.MAKE_DATABASE

        # Check for complex indicators
        for pattern in self.COMPLEX_INDICATORS:
            if pattern.search(content):
                return ExtractorStrategy.MAKE_DATABASE

        # Simple Makefile, use regex
        return ExtractorStrategy.SIMPLE


# =============================================================================
# Caching
# =============================================================================


@dataclass
class CacheEntry:
    """Cache entry for extracted targets.

    Attributes:
        targets: Cached list of extracted targets.
        strategy_used: The extraction strategy that was used.
        timestamp: Unix timestamp when the cache entry was created.
        makefile_content_hash: SHA-256 hash of the Makefile content.
        included_files: Map of included file paths to their mtimes.
        config_hash: Hash of relevant configuration fields.
    """

    targets: list[MakeTarget]
    strategy_used: ExtractorStrategy
    timestamp: float
    makefile_content_hash: str
    included_files: dict[str, float] = field(default_factory=dict)
    config_hash: str = ""

    def is_valid(
        self,
        ttl: int,
        current_content_hash: str,
        current_included_mtimes: dict[str, float],
        current_config_hash: str,
        current_time: float | None = None,
    ) -> bool:
        """Check if cache entry is still valid.

        Args:
            ttl: Cache time-to-live in seconds.
            current_content_hash: Current SHA-256 hash of Makefile content.
            current_included_mtimes: Current mtimes of included files.
            current_config_hash: Current hash of relevant config fields.
            current_time: Optional current timestamp for testing. If not
                          provided, uses time.time().

        Returns:
            True if cache is still valid, False otherwise.
        """
        if ttl <= 0:
            return False

        # Invalid if TTL expired
        now = current_time if current_time is not None else time.time()
        age = now - self.timestamp
        if age >= ttl:
            return False

        # Invalid if Makefile content changed
        if current_content_hash != self.makefile_content_hash:
            return False

        # Invalid if configuration changed
        if current_config_hash != self.config_hash:
            return False

        # Invalid if any included file was modified
        for path, cached_mtime in self.included_files.items():
            current_mtime = current_included_mtimes.get(path)
            if current_mtime is None:
                # Included file no longer exists
                return False
            if current_mtime > cached_mtime:
                # Included file was modified
                return False

        # Check for new included files
        return all(path in self.included_files for path in current_included_mtimes)


class TargetCache:
    """Cache for extracted Makefile targets with content-based invalidation."""

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
    def _hash_config(config: MakefilePluginConfig) -> str:
        """Compute hash of configuration fields that affect extraction.

        Args:
            config: The plugin configuration.

        Returns:
            Hex-encoded SHA-256 hash of relevant config fields.
        """
        relevant_fields = (
            config.targets,
            config.exclude_targets,
            config.extractor.value,
            str(config.trust_makefile),
            config.description_prefix,
        )
        combined = "|".join(str(f) for f in relevant_fields)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_included_files(makefile_path: Path) -> dict[str, float]:
        """Extract included file paths and their mtimes.

        Args:
            makefile_path: Path to the Makefile.

        Returns:
            Mapping of included file paths to their modification times.
        """
        included_files: dict[str, float] = {}

        try:
            content = makefile_path.read_text()
            base_dir = makefile_path.parent

            # Match include, -include, and sinclude directives
            include_pattern = re.compile(
                r"^(?:-include|sinclude|include)\s+(.+)$",
                re.MULTILINE,
            )

            for match in include_pattern.finditer(content):
                file_specs = match.group(1).split()
                for spec in file_specs:
                    # Skip variable references
                    if "$(" in spec or "${" in spec:
                        continue

                    # Resolve path relative to Makefile directory
                    include_path = base_dir / spec
                    if include_path.exists():
                        try:
                            mtime = include_path.stat().st_mtime
                            included_files[str(include_path.resolve())] = mtime
                        except OSError:
                            pass

        except OSError:
            pass

        return included_files

    def get(
        self,
        makefile_path: str,
        ttl: int,
        config: MakefilePluginConfig,
    ) -> CacheEntry | None:
        """Get cached targets if valid.

        Args:
            makefile_path: Path to the Makefile.
            ttl: Cache time-to-live in seconds.
            config: The plugin configuration.

        Returns:
            The cache entry if valid, None otherwise.
        """
        entry = self._cache.get(makefile_path)
        if entry is None:
            return None

        path = Path(makefile_path)
        try:
            content = path.read_text()
            current_hash = self._hash_content(content)
            current_included_mtimes = self._get_included_files(path)
            current_config_hash = self._hash_config(config)
        except OSError:
            # File not accessible, invalidate cache
            del self._cache[makefile_path]
            return None

        is_valid = entry.is_valid(
            ttl, current_hash, current_included_mtimes, current_config_hash
        )
        if is_valid:
            return entry

        # Cache invalid, remove it
        del self._cache[makefile_path]
        return None

    def set(
        self,
        makefile_path: str,
        targets: list[MakeTarget],
        strategy: ExtractorStrategy,
        config: MakefilePluginConfig,
    ) -> None:
        """Cache extracted targets.

        Args:
            makefile_path: Path to the Makefile.
            targets: The extracted targets.
            strategy: The extraction strategy used.
            config: The plugin configuration.
        """
        path = Path(makefile_path)
        try:
            content = path.read_text()
            content_hash = self._hash_content(content)
            included_files = self._get_included_files(path)
            config_hash = self._hash_config(config)
        except OSError:
            # Cannot cache without content
            return

        self._cache[makefile_path] = CacheEntry(
            targets=targets,
            strategy_used=strategy,
            timestamp=time.time(),
            makefile_content_hash=content_hash,
            included_files=included_files,
            config_hash=config_hash,
        )

    def invalidate(self, makefile_path: str | None = None) -> None:
        """Invalidate cache entries.

        Args:
            makefile_path: Path to invalidate, or None to clear all.
        """
        if makefile_path is None:
            self._cache.clear()
        elif makefile_path in self._cache:
            del self._cache[makefile_path]


# =============================================================================
# Plugin Implementation
# =============================================================================


class Plugin(InSourcePlugin):
    """Makefile plugin exposing targets as MCP tools.

    This plugin discovers Makefile targets and exposes them as tools that can
    be invoked by AI coding agents. It supports multiple extraction strategies
    and configurable target filtering.

    Configuration options:
        makefile_path: Path to the Makefile (default: ./Makefile)
        targets: Comma-separated fnmatch patterns for targets to expose
        exclude_targets: Patterns for targets to exclude
        extractor: Extraction strategy (simple, make_database, auto)
        trust_makefile: Whether to trust the Makefile for code execution
        cache_ttl: Cache TTL in seconds (default: 300)
    """

    def __init__(
        self,
        config: dict[str, Any],
        instance_name: str = "makefile",
        cache: TargetCache | None = None,
    ) -> None:
        """Initialize the Makefile plugin.

        Args:
            config: Plugin configuration dictionary.
            instance_name: Name for this plugin instance.
            cache: Optional TargetCache instance for dependency injection.
                   If not provided, a new TargetCache is created.
        """
        super().__init__(config)
        self._plugin_config = MakefilePluginConfig.model_validate(config)
        self._instance_name = instance_name
        self._targets: list[MakeTarget] = []
        self._tool_to_target: dict[str, str] = {}
        self._cache = cache if cache is not None else TargetCache()
        self._extractor: ExtractorSelector | None = None
        self._initialized = False

    def _create_extractor(self) -> ExtractorSelector:
        """Create the hybrid extractor with current configuration.

        Returns:
            Configured ExtractorSelector instance.
        """
        simple = SimpleExtractor(
            description_prefix=self._plugin_config.description_prefix,
        )
        database = MakeDatabaseExtractor(
            make_command=self._plugin_config.make_command,
            working_directory=self._plugin_config.working_directory,
            description_prefix=self._plugin_config.description_prefix,
        )
        return ExtractorSelector(
            simple_extractor=simple,
            database_extractor=database,
            trust_makefile=self._plugin_config.trust_makefile,
        )

    def _resolve_makefile_path(self, base_directory: Path | None = None) -> Path:
        """Resolve and validate Makefile path.

        Args:
            base_directory: Optional base directory for resolving relative paths.
                            If not provided, uses working_directory from config
                            or Path.cwd().

        Returns:
            Absolute path to the Makefile.
        """
        makefile = Path(self._plugin_config.makefile_path)

        if base_directory is not None:
            base_dir = base_directory.resolve()
        elif self._plugin_config.working_directory:
            base_dir = Path(self._plugin_config.working_directory).resolve()
        else:
            base_dir = Path.cwd()

        if not makefile.is_absolute():
            makefile = base_dir / makefile

        return makefile.resolve()

    async def _refresh_targets(self) -> None:
        """Refresh target list with fallback strategies."""
        makefile_path = self._resolve_makefile_path()

        # Check if Makefile exists
        if not makefile_path.exists():
            logger.warning(
                "makefile_not_found: path=%s",
                str(makefile_path),
            )
            self._targets = []
            self._tool_to_target = {}
            return

        # Check cache first
        makefile_str = str(makefile_path)
        cached = self._cache.get(
            makefile_str,
            self._plugin_config.cache_ttl,
            self._plugin_config,
        )
        if cached is not None:
            logger.debug(
                "cache_hit: path=%s, target_count=%d",
                makefile_str,
                len(cached.targets),
            )
            self._targets = cached.targets
            self._build_tool_mapping()
            return

        # Extract targets
        if self._extractor is None:
            self._extractor = self._create_extractor()

        try:
            targets, strategy = await self._extractor.extract(
                makefile_path,
                self._plugin_config.extractor,
            )
            logger.info(
                "targets_extracted: path=%s, count=%d, strategy=%s",
                makefile_str,
                len(targets),
                strategy.value,
            )
        except ExtractorError as e:
            logger.error(
                "extraction_failed: error=%s, cause=%s",
                str(e),
                str(e.cause) if e.cause else None,
            )

            # Fallback to simple extractor if database failed
            if self._plugin_config.extractor != ExtractorStrategy.SIMPLE:
                try:
                    targets = await self._extractor.simple_extractor.extract(
                        makefile_path
                    )
                    strategy = ExtractorStrategy.SIMPLE
                    logger.info(
                        "fallback_extraction_succeeded: count=%d",
                        len(targets),
                    )
                except ExtractorError as fallback_error:
                    logger.error(
                        "fallback_extraction_failed: error=%s",
                        str(fallback_error),
                    )
                    targets = []
                    strategy = ExtractorStrategy.SIMPLE
            else:
                targets = []
                strategy = ExtractorStrategy.SIMPLE

        # Filter targets
        target_filter = TargetFilter.from_config(self._plugin_config)
        self._targets = [t for t in targets if target_filter.matches(t.name)]

        # Build tool mapping
        self._build_tool_mapping()

        # Update cache
        if self._plugin_config.cache_ttl > 0:
            self._cache.set(makefile_str, self._targets, strategy, self._plugin_config)

    def _build_tool_mapping(self) -> None:
        """Build the tool name to target name mapping."""
        self._tool_to_target = {t.to_tool_name(): t.name for t in self._targets}

    async def initialize(self) -> None:
        """Initialize plugin and discover targets."""
        self._extractor = self._create_extractor()
        await self._refresh_targets()
        self._initialized = True
        logger.info(
            "plugin_initialized: instance=%s, targets=%d",
            self._instance_name,
            len(self._targets),
        )

    async def shutdown(self) -> None:
        """Clean up plugin resources."""
        self._cache.invalidate()
        self._targets = []
        self._tool_to_target = {}
        self._extractor = None
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

        makefile_path = self._resolve_makefile_path()

        # Check if Makefile exists and is readable
        if not makefile_path.exists():
            return False

        try:
            makefile_path.read_text()
        except OSError:
            return False

        # Check if make command is available (for make_database strategy)
        uses_make_db = self._plugin_config.extractor in (
            ExtractorStrategy.MAKE_DATABASE,
            ExtractorStrategy.AUTO,
        )
        if uses_make_db:
            make_path = shutil.which(self._plugin_config.make_command)
            explicitly_requires_make = (
                self._plugin_config.extractor == ExtractorStrategy.MAKE_DATABASE
            )
            if make_path is None and explicitly_requires_make:
                return False

        return True

    async def detailed_health_check(self) -> dict[str, Any]:
        """Perform a detailed health check with diagnostic information.

        Returns:
            Dictionary containing health status and diagnostic details:
                - healthy: Overall health status (bool)
                - initialized: Whether the plugin is initialized (bool)
                - makefile_path: Resolved path to the Makefile (str)
                - makefile_exists: Whether the Makefile exists (bool)
                - makefile_readable: Whether the Makefile is readable (bool)
                - make_command: Configured make command (str)
                - make_available: Whether make command is available (bool)
                - make_path: Resolved path to make binary (str | None)
                - extractor_strategy: Configured extraction strategy (str)
                - target_count: Number of discovered targets (int)
                - cache_ttl: Configured cache TTL (int)
                - trust_makefile: Whether Makefile is trusted (bool)
        """
        makefile_path = self._resolve_makefile_path()
        makefile_exists = makefile_path.exists()

        makefile_readable = False
        if makefile_exists:
            try:
                makefile_path.read_text()
                makefile_readable = True
            except OSError:
                pass

        make_path = shutil.which(self._plugin_config.make_command)
        make_available = make_path is not None

        # Determine overall health
        healthy = self._initialized and makefile_exists and makefile_readable

        # For make_database strategy, make must be available
        if self._plugin_config.extractor == ExtractorStrategy.MAKE_DATABASE:
            healthy = healthy and make_available

        return {
            "healthy": healthy,
            "initialized": self._initialized,
            "makefile_path": str(makefile_path),
            "makefile_exists": makefile_exists,
            "makefile_readable": makefile_readable,
            "make_command": self._plugin_config.make_command,
            "make_available": make_available,
            "make_path": make_path,
            "extractor_strategy": self._plugin_config.extractor.value,
            "target_count": len(self._targets),
            "cache_ttl": self._plugin_config.cache_ttl,
            "trust_makefile": self._plugin_config.trust_makefile,
        }

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration reload.

        Args:
            new_config: The new configuration dictionary.
        """
        new_validated = MakefilePluginConfig.model_validate(new_config)

        # Check if cache-affecting settings changed
        cache_invalidation_needed = (
            new_validated.makefile_path != self._plugin_config.makefile_path
            or new_validated.extractor != self._plugin_config.extractor
            or new_validated.trust_makefile != self._plugin_config.trust_makefile
            or new_validated.targets != self._plugin_config.targets
            or new_validated.exclude_targets != self._plugin_config.exclude_targets
        )

        old_trust = self._plugin_config.trust_makefile
        self._plugin_config = new_validated
        self.config = new_config

        # Recreate extractor if trust setting changed
        if new_validated.trust_makefile != old_trust:
            self._extractor = self._create_extractor()

        # Invalidate cache and refresh if needed
        if cache_invalidation_needed:
            self._cache.invalidate()
            await self._refresh_targets()

        logger.info(
            "config_reloaded: instance=%s, cache_invalidated=%s",
            self._instance_name,
            cache_invalidation_needed,
        )

    def get_tools(self) -> list[ToolDefinition]:
        """Return tool definitions for discovered targets.

        Returns:
            List of ToolDefinition objects for all exposed targets.
        """
        tools: list[ToolDefinition] = []

        # Optionally add list_targets tool
        if self._plugin_config.expose_list_targets:
            tools.append(
                ToolDefinition(
                    name="make_list_targets",
                    description="List all available Makefile targets",
                    parameters={"type": "object", "properties": {}},
                    returns={
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "is_phony": {"type": "boolean"},
                            },
                        },
                    },
                )
            )

        # Add tool for each target
        for target in self._targets:
            tool_def = target.to_tool_definition()
            tools.append(ToolDefinition(**tool_def))

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute the requested make target.

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

        if tool_name == "make_list_targets":
            return await self._list_targets()

        target = self._tool_to_target.get(tool_name)
        if target is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
            )

        return await self._execute_target(target, arguments)

    async def _list_targets(self) -> ToolResult:
        """List all available targets.

        Returns:
            ToolResult with target list.
        """
        targets_data = [
            {
                "name": t.name,
                "description": t.description,
                "is_phony": t.is_phony,
            }
            for t in self._targets
        ]
        return ToolResult(
            success=True,
            data=targets_data,
        )

    async def _execute_target(
        self,
        target: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute a make target.

        Args:
            target: The target name to execute.
            arguments: Execution arguments (extra_args, timeout, dry_run).

        Returns:
            ToolResult with execution outcome.
        """
        makefile_path = self._resolve_makefile_path()
        timeout = arguments.get("timeout", self._plugin_config.default_timeout)
        dry_run = arguments.get("dry_run", False)
        extra_args = arguments.get("extra_args", "")

        # Build command
        cmd = [self._plugin_config.make_command, "-f", str(makefile_path)]

        # Add parallel jobs if allowed
        if self._plugin_config.allow_parallel:
            jobs = self._plugin_config.parallel_jobs or os.cpu_count() or 1
            cmd.extend(["-j", str(jobs)])

        # Add dry-run flag if requested
        if dry_run:
            cmd.append("-n")

        # Add extra arguments if provided
        if extra_args:
            try:
                cmd.extend(shlex.split(extra_args))
            except ValueError as e:
                return ToolResult(
                    success=False,
                    error=f"Invalid extra_args: {e}",
                )

        # Add target
        cmd.append(target)

        # Build environment
        env = os.environ.copy()
        env.update(self._plugin_config.environment)

        # Execute
        start_time = time.time()
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._plugin_config.working_directory or str(makefile_path.parent),
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            duration = time.time() - start_time
            exit_code = process.returncode
            error_msg = (
                None
                if exit_code == 0
                else f"make {target} failed with exit code {exit_code}"
            )

            return ToolResult(
                success=exit_code == 0,
                data={
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "exit_code": exit_code,
                    "target": target,
                    "duration_seconds": round(duration, 2),
                },
                error=error_msg,
            )

        except TimeoutError:
            return ToolResult(
                success=False,
                error=f"Target execution timed out after {timeout}s",
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error=f"make command not found: {self._plugin_config.make_command}",
            )
        except OSError as e:
            return ToolResult(
                success=False,
                error=f"Failed to execute make: {e}",
            )


# Alias for compatibility
MakefilePlugin = Plugin
