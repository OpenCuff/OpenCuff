# OpenCuff Makefile Plugin - High-Level Design

**Version:** 1.1
**Date:** 2026-01-18
**Status:** Draft

**Revision History:**
| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-18 | Initial draft |
| 1.1 | 2026-01-18 | Added security warning for `make -pn` code execution; improved cache invalidation with content hashing; added observability section; fixed async/sync inconsistency; clarified pattern matching syntax; added tool namespacing documentation; added shutdown/on_config_reload methods; added health check implementation; added Format 2 description pattern support; documented behavior with generated Makefiles |

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Goals and Non-Goals](#goals-and-non-goals)
3. [Architecture Overview](#architecture-overview)
4. [Target Extraction Strategies](#target-extraction-strategies)
5. [Configuration Schema](#configuration-schema)
6. [Interface Definitions](#interface-definitions)
7. [Target Discovery Flow](#target-discovery-flow)
8. [Caching Strategy](#caching-strategy)
9. [Observability](#observability)
10. [Example Configurations](#example-configurations)
11. [Error Handling](#error-handling)
12. [Security Considerations](#security-considerations)
13. [Future Considerations](#future-considerations)

---

## Executive Summary

This document describes the design for the OpenCuff Makefile Plugin, an in-source plugin that discovers and exposes Makefile targets as MCP tools. The plugin enables AI coding agents to discover and execute build targets, test commands, and other Makefile-defined operations in a governed manner.

The plugin supports multiple target extraction strategies with varying trade-offs between speed and accuracy:
- **Simple parsing**: Fast regex-based extraction for basic use cases
- **Make database dump**: Accurate extraction using `make -pn` for complex Makefiles
- **Hybrid/Auto**: Intelligent selection based on Makefile characteristics

---

## Goals and Non-Goals

### Goals

- **Accurate Target Discovery**: Support multiple extraction strategies to handle Makefiles of varying complexity
- **Configurable Exposure**: Allow fine-grained control over which targets are exposed as tools
- **Description Extraction**: Parse and expose target descriptions from comments
- **Performance**: Minimize startup latency through caching and strategy selection
- **Graceful Degradation**: Handle Makefile errors without crashing the plugin

### Non-Goals

- **Makefile Editing**: The plugin only reads and executes; it does not modify Makefiles
- **Full Make Emulation**: Complex features like recursive make are out of scope
- **Cross-Platform Make Variants**: Initial focus is on GNU Make only
- **Dependency Graph Visualization**: Target dependency analysis is out of scope

---

## Architecture Overview

### Component Diagram

```
+-----------------------------------------------------------------------------------+
|                              Makefile Plugin                                      |
|                                                                                   |
|  +-------------------+    +-------------------+    +-------------------+          |
|  |   Configuration   |    |  Target Extractor |    |   Tool Generator  |          |
|  |     Manager       |    |     (Strategy)    |    |                   |          |
|  +--------+----------+    +--------+----------+    +--------+----------+          |
|           |                        |                        |                     |
|           v                        v                        v                     |
|  +-------------------+    +-------------------+    +-------------------+          |
|  |  MakefileConfig   |    | ExtractorFactory  |    |  ToolDefinition   |          |
|  |  (Pydantic)       |    |                   |    |  Builder          |          |
|  +-------------------+    +-------------------+    +-------------------+          |
|                                    |                                              |
|                    +---------------+---------------+                              |
|                    |               |               |                              |
|                    v               v               v                              |
|           +--------+------+ +------+------+ +-----+-------+                       |
|           |   Simple      | |   Make DB   | |   Hybrid    |                       |
|           |   Extractor   | |   Extractor | |   Extractor |                       |
|           | (regex-based) | | (make -pn)  | |   (auto)    |                       |
|           +---------------+ +-------------+ +-------------+                       |
|                                                                                   |
|  +-------------------+    +-------------------+                                   |
|  |   Target Cache    |    |  Make Executor    |                                   |
|  |   (TTL-based)     |    |  (subprocess)     |                                   |
|  +-------------------+    +-------------------+                                   |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

### Integration with Plugin System

```
+------------------+     Plugin Protocol      +------------------+
|                  |<------------------------>|                  |
|  Plugin Manager  |                          |  MakefilePlugin  |
|  (OpenCuff Core) |                          |  (InSourcePlugin)|
|                  |                          |                  |
+------------------+                          +--------+---------+
                                                       |
                                              +--------+---------+
                                              |                  |
                                              |  Target          |
                                              |  Extractor       |
                                              |  (Strategy)      |
                                              |                  |
                                              +--------+---------+
                                                       |
                                              +--------+---------+
                                              |                  |
                                              |  Make            |
                                              |  (subprocess)    |
                                              |                  |
                                              +------------------+
```

### Tool Namespacing Integration

The Makefile plugin integrates with OpenCuff's plugin system tool namespacing. Tools are exposed using the `{plugin_name}.{tool_name}` format as defined in the plugin system HLD.

**Example Tool Names:**
| Plugin Instance | Target | Exposed Tool Name |
|----------------|--------|-------------------|
| `makefile` | `test` | `makefile.make_test` |
| `makefile` | `install` | `makefile.make_install` |
| `makefile_backend` | `build` | `makefile_backend.make_build` |

**Implementation:**
```python
class MakefilePlugin(InSourcePlugin):
    """Plugin exposing targets with namespaced tool names."""

    @property
    def plugin_name(self) -> str:
        """Return the plugin instance name for namespacing."""
        return self._instance_name

    def get_tools(self) -> list[ToolDefinition]:
        """Return tool definitions with namespaced names."""
        tools = []

        for target in self._targets:
            tool_def = target.to_tool_definition()
            # Tool name is already prefixed with 'make_'
            # Plugin manager will add '{plugin_name}.' prefix
            # Final format: makefile.make_test
            tools.append(ToolDefinition(**tool_def))

        return tools
```

The plugin manager handles the namespacing by prefixing tool names with the plugin instance name when registering tools. This allows multiple Makefile plugin instances (e.g., for monorepos) without tool name conflicts.

### Data Flow

```
                    Plugin Initialization
                              |
                              v
                    +-------------------+
                    | Load Configuration|
                    +-------------------+
                              |
                              v
                    +-------------------+
                    | Select Extraction |
                    | Strategy          |
                    +-------------------+
                              |
              +---------------+---------------+
              |               |               |
              v               v               v
        +-----------+   +-----------+   +-----------+
        |  Simple   |   |  Make DB  |   |   Auto    |
        |  Extract  |   |  Extract  |   |  (choose) |
        +-----------+   +-----------+   +-----------+
              |               |               |
              +---------------+---------------+
                              |
                              v
                    +-------------------+
                    | Filter by Pattern |
                    +-------------------+
                              |
                              v
                    +-------------------+
                    | Generate Tool     |
                    | Definitions       |
                    +-------------------+
                              |
                              v
                    +-------------------+
                    | Cache Results     |
                    +-------------------+
                              |
                              v
                    +-------------------+
                    | Register with     |
                    | Plugin Manager    |
                    +-------------------+
```

---

## Target Extraction Strategies

### Strategy 1: Simple/Naive Parsing

**Description**: Regex-based parsing directly on Makefile text.

**Implementation Approach**:
```python
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MakeTarget:
    """Represents a discovered Makefile target."""
    name: str
    description: str | None = None
    is_phony: bool = False
    prerequisites: list[str] | None = None


class SimpleExtractor:
    """Regex-based Makefile target extractor."""

    # Match standard targets: "target: prerequisites"
    TARGET_PATTERN = re.compile(
        r'^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:(?!=)',
        re.MULTILINE
    )

    # Match .PHONY declarations
    PHONY_PATTERN = re.compile(
        r'^\.PHONY\s*:\s*(.+)$',
        re.MULTILINE
    )

    # Match comment descriptions (line before target) - Format 1
    DESCRIPTION_PATTERN = re.compile(
        r'^##\s*(.+)\n([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:',
        re.MULTILINE
    )

    # Match inline comment descriptions (same line as target) - Format 2
    # Supports: "target: deps ## description" or "target: ## description"
    INLINE_DESCRIPTION_PATTERN = re.compile(
        r'^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:[^#\n]*##\s*(.+)$',
        re.MULTILINE
    )

    def _extract_sync(self, makefile_path: Path) -> list[MakeTarget]:
        """Synchronous extraction implementation."""
        content = makefile_path.read_text()

        # Find all .PHONY targets
        phony_targets = set()
        for match in self.PHONY_PATTERN.finditer(content):
            phony_targets.update(match.group(1).split())

        # Find descriptions from Format 1 (## comments before targets)
        descriptions: dict[str, str] = {}
        for match in self.DESCRIPTION_PATTERN.finditer(content):
            descriptions[match.group(2)] = match.group(1).strip()

        # Find descriptions from Format 2 (## inline comments)
        for match in self.INLINE_DESCRIPTION_PATTERN.finditer(content):
            target_name = match.group(1)
            # Inline comments take precedence over preceding comments
            descriptions[target_name] = match.group(2).strip()

        # Find all targets
        targets = []
        for match in self.TARGET_PATTERN.finditer(content):
            name = match.group(1)
            # Skip internal targets (starting with .)
            if name.startswith('.'):
                continue
            targets.append(MakeTarget(
                name=name,
                description=descriptions.get(name),
                is_phony=name in phony_targets
            ))

        return targets

    async def extract(self, makefile_path: Path) -> list[MakeTarget]:
        """Extract targets from Makefile using regex.

        This method is async to conform to the ExtractorProtocol interface.
        File I/O is wrapped with asyncio.to_thread() to avoid blocking
        the event loop.
        """
        return await asyncio.to_thread(self._extract_sync, makefile_path)
```

**Pros**:
| Advantage | Description |
|-----------|-------------|
| Fast | No subprocess invocation; pure Python parsing |
| No dependencies | Works without `make` binary being available |
| Predictable | Consistent behavior across environments |
| Low resource usage | Minimal memory and CPU footprint |
| Safe for untrusted files | No code execution during parsing |

**Cons**:
| Limitation | Description |
|------------|-------------|
| No `include` support | Cannot resolve included Makefiles |
| No variable expansion | `$(VAR)` patterns not resolved |
| No dynamic targets | `$(shell ...)` generated targets missed |
| No pattern rules | `%.o: %.c` style rules not handled |
| Limited accuracy | May produce false positives/negatives |

**Best For**: Simple, single-file Makefiles with static targets, or untrusted Makefiles where security is a concern.

---

### Strategy 2: Make Database Dump

**Description**: Uses `make --print-data-base --dry-run` to get the fully resolved target database.

> **SECURITY WARNING**: The `make -pn` (database dump) command executes `$(shell ...)` commands
> and other Make functions during parsing. This can run arbitrary code from the Makefile.
> **Never use the `make_database` or `auto` extractor strategy with untrusted Makefiles.**
> For untrusted Makefiles, always use the `simple` extraction strategy or set `trust_makefile: false`.

**Implementation Approach**:
```python
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path


class MakeDatabaseExtractor:
    """Target extractor using make's database dump.

    WARNING: This extractor executes $(shell ...) commands during parsing.
    Only use with trusted Makefiles. See Security Considerations section.
    """

    # Pattern to match target entries in make database
    # Format: "target: prerequisites"
    DB_TARGET_PATTERN = re.compile(
        r'^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:(?!=)\s*(.*)$',
        re.MULTILINE
    )

    # Pattern to detect if target is marked as phony
    DB_PHONY_PATTERN = re.compile(
        r'^#\s+Phony target',
        re.MULTILINE
    )

    def __init__(
        self,
        make_command: str = "make",
        working_directory: str | None = None,
        timeout: float = 30.0
    ) -> None:
        self.make_command = make_command
        self.working_directory = working_directory
        self.timeout = timeout

    async def extract(self, makefile_path: Path) -> list[MakeTarget]:
        """Extract targets using make database dump.

        WARNING: This method executes make -pn which will evaluate
        $(shell ...) and other Make functions. This can execute
        arbitrary code from the Makefile.
        """
        cmd = [
            self.make_command,
            "-f", str(makefile_path),
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
                cwd=self.working_directory
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout
            )

            # Note: make -pn may exit with non-zero if targets fail,
            # but database is still output
            output = stdout.decode("utf-8", errors="replace")
            return self._parse_database(output, makefile_path)

        except asyncio.TimeoutError:
            raise ExtractorError(
                f"make database dump timed out after {self.timeout}s"
            )
        except FileNotFoundError:
            raise ExtractorError(
                f"make command not found: {self.make_command}"
            )

    def _parse_database(
        self,
        output: str,
        makefile_path: Path
    ) -> list[MakeTarget]:
        """Parse make database output into targets."""
        targets = []

        # Split by target sections
        # Each target in database starts with its name followed by metadata
        sections = re.split(r'\n(?=[a-zA-Z_])', output)

        for section in sections:
            lines = section.strip().split('\n')
            if not lines:
                continue

            # Check if this is a target definition
            first_line = lines[0]
            match = self.DB_TARGET_PATTERN.match(first_line)
            if not match:
                continue

            name = match.group(1)

            # Skip internal targets
            if name.startswith('.') or name.startswith('__'):
                continue

            # Check metadata for phony status
            is_phony = any(
                self.DB_PHONY_PATTERN.match(line)
                for line in lines[1:10]  # Check first few metadata lines
            )

            targets.append(MakeTarget(
                name=name,
                is_phony=is_phony,
                prerequisites=match.group(2).split() if match.group(2) else None
            ))

        # Also extract descriptions from original file
        descriptions = self._extract_descriptions(makefile_path)
        for target in targets:
            target.description = descriptions.get(target.name)

        return targets

    def _extract_descriptions(self, makefile_path: Path) -> dict[str, str]:
        """Extract ## comments as descriptions (both Format 1 and Format 2)."""
        content = makefile_path.read_text()
        descriptions = {}

        # Format 1: ## comment on line before target
        pattern1 = re.compile(r'^##\s*(.+)\n([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:', re.MULTILINE)
        for match in pattern1.finditer(content):
            descriptions[match.group(2)] = match.group(1).strip()

        # Format 2: target: ## inline comment (takes precedence)
        pattern2 = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_.-]*)\s*:[^#\n]*##\s*(.+)$', re.MULTILINE)
        for match in pattern2.finditer(content):
            descriptions[match.group(1)] = match.group(2).strip()

        return descriptions
```

**Pros**:
| Advantage | Description |
|-----------|-------------|
| Full accuracy | All resolved targets including dynamic ones |
| `include` support | Handles all included Makefiles |
| Variable expansion | All `$(VAR)` patterns resolved |
| Pattern rules | Discovers instantiated pattern targets |

**Cons**:
| Limitation | Description |
|------------|-------------|
| Slower startup | Requires subprocess execution |
| Requires `make` | GNU Make must be installed |
| **Executes code** | **`$(shell ...)` evaluations run - security risk** |
| Environment dependent | Results vary by environment |
| Potential side effects | Some Makefiles have side effects on parse |

**Best For**: Complex Makefiles with includes, variables, and generated targets - **only when the Makefile is trusted**.

---

### Strategy 3: Hybrid/Auto Approach

**Description**: Automatically selects the best strategy based on Makefile characteristics.

> **SECURITY WARNING**: The `auto` strategy may select `make_database` extraction,
> which executes `$(shell ...)` commands. For untrusted Makefiles, explicitly set
> `extractor: simple` or `trust_makefile: false` to prevent code execution.

**Implementation Approach**:
```python
from enum import Enum


class ExtractorStrategy(str, Enum):
    SIMPLE = "simple"
    MAKE_DATABASE = "make_database"
    AUTO = "auto"


class HybridExtractor:
    """Automatically selects extraction strategy based on Makefile analysis.

    WARNING: When AUTO is selected, this may use make_database extraction
    which executes $(shell ...) commands. See Security Considerations.
    """

    # Indicators that require make database extraction
    COMPLEX_INDICATORS = [
        re.compile(r'^include\s+', re.MULTILINE),           # include directive
        re.compile(r'\$\(shell\s+', re.MULTILINE),          # shell function
        re.compile(r'\$\(wildcard\s+', re.MULTILINE),       # wildcard function
        re.compile(r'\$\(foreach\s+', re.MULTILINE),        # foreach function
        re.compile(r'\$\(eval\s+', re.MULTILINE),           # eval function
        re.compile(r'^-include\s+', re.MULTILINE),          # optional include
        re.compile(r'^sinclude\s+', re.MULTILINE),          # sinclude
        re.compile(r'^\w+\s*:=.*\$\(', re.MULTILINE),       # variable with function
    ]

    def __init__(
        self,
        simple_extractor: SimpleExtractor,
        database_extractor: MakeDatabaseExtractor,
        trust_makefile: bool = True
    ) -> None:
        self.simple_extractor = simple_extractor
        self.database_extractor = database_extractor
        self.trust_makefile = trust_makefile

    async def extract(
        self,
        makefile_path: Path,
        strategy: ExtractorStrategy = ExtractorStrategy.AUTO
    ) -> tuple[list[MakeTarget], ExtractorStrategy]:
        """Extract targets using selected or auto-detected strategy."""

        if strategy == ExtractorStrategy.SIMPLE:
            targets = await self.simple_extractor.extract(makefile_path)
            return targets, ExtractorStrategy.SIMPLE

        if strategy == ExtractorStrategy.MAKE_DATABASE:
            if not self.trust_makefile:
                raise ExtractorError(
                    "make_database strategy requires trust_makefile=True. "
                    "This Makefile is not trusted and make -pn would execute code."
                )
            targets = await self.database_extractor.extract(makefile_path)
            return targets, ExtractorStrategy.MAKE_DATABASE

        # Auto-detect strategy
        selected = self._analyze_complexity(makefile_path)

        # If Makefile is not trusted, force simple strategy
        if not self.trust_makefile and selected == ExtractorStrategy.MAKE_DATABASE:
            logger.warning(
                "untrusted_makefile_forced_simple",
                reason="Makefile contains complex features but trust_makefile=False",
                path=str(makefile_path)
            )
            selected = ExtractorStrategy.SIMPLE

        if selected == ExtractorStrategy.SIMPLE:
            targets = await self.simple_extractor.extract(makefile_path)
        else:
            targets = await self.database_extractor.extract(makefile_path)

        return targets, selected

    def _analyze_complexity(self, makefile_path: Path) -> ExtractorStrategy:
        """Analyze Makefile to determine best extraction strategy."""
        try:
            content = makefile_path.read_text()
        except Exception:
            # If we can't read the file, try database approach
            return ExtractorStrategy.MAKE_DATABASE

        # Check for complex indicators
        for pattern in self.COMPLEX_INDICATORS:
            if pattern.search(content):
                return ExtractorStrategy.MAKE_DATABASE

        # Simple Makefile, use regex
        return ExtractorStrategy.SIMPLE
```

**Decision Matrix**:
| Makefile Characteristic | Selected Strategy |
|------------------------|-------------------|
| Contains `include` directive | make_database |
| Contains `$(shell ...)` | make_database |
| Contains `$(wildcard ...)` | make_database |
| Contains `$(foreach ...)` | make_database |
| Contains `$(eval ...)` | make_database |
| Simple static targets only | simple |
| File unreadable | make_database (fallback) |
| `trust_makefile: false` | **simple (forced)** |

---

### Strategy Comparison Summary

| Aspect | Simple | Make Database | Auto |
|--------|--------|---------------|------|
| Speed | Fast (~1ms) | Slower (~100-500ms) | Varies |
| Accuracy | Limited | Full | Optimal |
| `include` support | No | Yes | Yes |
| Dynamic targets | No | Yes | Yes |
| Dependencies | None | GNU Make | GNU Make |
| Side effects | None | Possible | Possible |
| **Code execution** | **None** | **Yes - security risk** | **Possible** |
| Recommended for | CI/CD, simple builds, **untrusted files** | Complex projects (trusted) | Trusted projects |

---

## Configuration Schema

### Pydantic Configuration Model

```python
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field, field_validator


class ExtractorStrategy(str, Enum):
    """Target extraction strategy."""
    SIMPLE = "simple"
    MAKE_DATABASE = "make_database"
    AUTO = "auto"


class MakefilePluginConfig(BaseModel):
    """Configuration for the Makefile plugin."""

    makefile_path: str = Field(
        default="./Makefile",
        description="Path to the Makefile (relative to working_directory or absolute)"
    )

    targets: str = Field(
        default="*",
        description=(
            "Comma-separated fnmatch patterns for targets to expose. "
            "Uses Unix shell-style wildcards (*, ?, [seq], [!seq]). "
            "Examples: 'test-*', 'build', 'install-*,deploy-*'"
        )
    )

    exclude_targets: str = Field(
        default="",
        description=(
            "Comma-separated fnmatch patterns for targets to exclude. "
            "Uses Unix shell-style wildcards (*, ?, [seq], [!seq]). "
            "Examples: '*-internal', '_*', 'test-slow-*'"
        )
    )

    extractor: ExtractorStrategy = Field(
        default=ExtractorStrategy.AUTO,
        description="Target extraction strategy: 'simple', 'make_database', or 'auto'"
    )

    trust_makefile: bool = Field(
        default=True,
        description=(
            "Whether to trust the Makefile for code execution. "
            "When False, forces 'simple' extractor even if 'auto' or 'make_database' "
            "is configured. Set to False for untrusted Makefiles to prevent "
            "$(shell ...) execution during parsing."
        )
    )

    cache_ttl: int = Field(
        default=300,
        ge=0,
        description="Cache TTL in seconds for extracted targets (0 to disable)"
    )

    make_command: str = Field(
        default="make",
        description="Path to the make binary"
    )

    working_directory: str | None = Field(
        default=None,
        description="Working directory for make execution (default: directory containing Makefile)"
    )

    allow_parallel: bool = Field(
        default=True,
        description="Allow parallel execution with -j flag"
    )

    parallel_jobs: int | None = Field(
        default=None,
        ge=1,
        description="Number of parallel jobs (default: CPU count)"
    )

    default_timeout: int = Field(
        default=300,
        ge=1,
        description="Default timeout for target execution in seconds"
    )

    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Additional environment variables for make execution"
    )

    description_prefix: str = Field(
        default="##",
        description="Comment prefix used for target descriptions"
    )

    expose_list_targets: bool = Field(
        default=True,
        description="Expose a 'make_list_targets' tool for target discovery"
    )

    @field_validator('targets', 'exclude_targets')
    @classmethod
    def validate_patterns(cls, v: str) -> str:
        """Validate wildcard patterns syntax."""
        if not v:
            return v
        for pattern in v.split(','):
            pattern = pattern.strip()
            # Basic validation - patterns should not contain path separators
            if '/' in pattern or '\\' in pattern:
                raise ValueError(f"Invalid pattern: {pattern}. Patterns should not contain path separators.")
        return v
```

### Pattern Matching Syntax (fnmatch)

The `targets` and `exclude_targets` configuration options use **fnmatch** (Unix shell-style wildcards), **NOT regular expressions**.

**Supported Wildcards:**
| Pattern | Meaning | Example | Matches | Does Not Match |
|---------|---------|---------|---------|----------------|
| `*` | Matches everything | `test-*` | `test-unit`, `test-integration` | `unittest`, `test` |
| `?` | Matches any single character | `build?` | `build1`, `buildx` | `build`, `build12` |
| `[seq]` | Matches any character in seq | `test-[abc]` | `test-a`, `test-b` | `test-d`, `test-ab` |
| `[!seq]` | Matches any character not in seq | `test-[!0-9]` | `test-a`, `test-x` | `test-1`, `test-9` |

**Common Pattern Examples:**
| Use Case | Pattern | Matches |
|----------|---------|---------|
| All test targets | `test-*` | `test-unit`, `test-integration`, `test-e2e` |
| All targets | `*` | Everything |
| Specific targets | `build,test,lint` | Only `build`, `test`, `lint` |
| Exclude internal | `*-internal` (in exclude_targets) | Excludes `deploy-internal`, `build-internal` |
| Multiple patterns | `install-*,deploy-*` | `install-dev`, `install-prod`, `deploy-staging` |

**Important**: These are **NOT** regular expressions. Use `*` instead of `.*`, and `?` instead of `.`.

### JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Makefile Plugin Configuration",
  "type": "object",
  "properties": {
    "makefile_path": {
      "type": "string",
      "default": "./Makefile",
      "description": "Path to the Makefile"
    },
    "targets": {
      "type": "string",
      "default": "*",
      "description": "Comma-separated fnmatch wildcard patterns for targets to expose"
    },
    "exclude_targets": {
      "type": "string",
      "default": "",
      "description": "Comma-separated fnmatch wildcard patterns for targets to exclude"
    },
    "extractor": {
      "type": "string",
      "enum": ["simple", "make_database", "auto"],
      "default": "auto",
      "description": "Target extraction strategy"
    },
    "trust_makefile": {
      "type": "boolean",
      "default": true,
      "description": "Whether to trust the Makefile for code execution during parsing"
    },
    "cache_ttl": {
      "type": "integer",
      "minimum": 0,
      "default": 300,
      "description": "Cache TTL in seconds (0 to disable)"
    },
    "make_command": {
      "type": "string",
      "default": "make",
      "description": "Path to make binary"
    },
    "working_directory": {
      "type": "string",
      "description": "Working directory for make execution"
    },
    "allow_parallel": {
      "type": "boolean",
      "default": true,
      "description": "Allow parallel execution"
    },
    "parallel_jobs": {
      "type": "integer",
      "minimum": 1,
      "description": "Number of parallel jobs"
    },
    "default_timeout": {
      "type": "integer",
      "minimum": 1,
      "default": 300,
      "description": "Default timeout in seconds"
    },
    "environment": {
      "type": "object",
      "additionalProperties": {"type": "string"},
      "default": {},
      "description": "Additional environment variables"
    },
    "description_prefix": {
      "type": "string",
      "default": "##",
      "description": "Comment prefix for descriptions"
    },
    "expose_list_targets": {
      "type": "boolean",
      "default": true,
      "description": "Expose make_list_targets tool"
    }
  }
}
```

---

## Interface Definitions

### MakeTarget Data Model

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MakeTarget:
    """Represents a discovered Makefile target."""

    name: str
    """The target name as defined in the Makefile."""

    description: str | None = None
    """Description extracted from ## comments."""

    is_phony: bool = False
    """Whether the target is declared as .PHONY."""

    prerequisites: list[str] = field(default_factory=list)
    """List of prerequisite targets."""

    source_file: str | None = None
    """Path to the Makefile where target is defined (for includes)."""

    line_number: int | None = None
    """Line number in source file (if available)."""

    def to_tool_name(self) -> str:
        """Convert target name to valid MCP tool name."""
        # Replace characters not allowed in tool names
        safe_name = self.name.replace('-', '_').replace('.', '_')
        return f"make_{safe_name}"

    def to_tool_definition(self) -> dict[str, Any]:
        """Convert to MCP ToolDefinition dict."""
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
                        "description": "Additional arguments to pass to make"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds",
                        "minimum": 1
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Print commands without executing (make -n)",
                        "default": False
                    }
                }
            },
            "returns": {
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "target": {"type": "string"},
                    "duration_seconds": {"type": "number"}
                }
            }
        }
```

### Extractor Protocol

```python
from abc import ABC, abstractmethod
from pathlib import Path


class ExtractorProtocol(ABC):
    """Protocol for Makefile target extractors."""

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


class ExtractorError(Exception):
    """Raised when target extraction fails."""

    def __init__(
        self,
        message: str,
        makefile_path: str | None = None,
        cause: Exception | None = None
    ) -> None:
        self.message = message
        self.makefile_path = makefile_path
        self.cause = cause
        super().__init__(message)
```

### Plugin Interface

```python
import shutil
from typing import Any
from opencuff.plugins.base import InSourcePlugin, ToolDefinition, ToolResult


class MakefilePlugin(InSourcePlugin):
    """Makefile plugin exposing targets as MCP tools."""

    def __init__(self, config: dict[str, Any], instance_name: str = "makefile") -> None:
        super().__init__(config)
        self._config = MakefilePluginConfig.model_validate(config)
        self._instance_name = instance_name
        self._targets: list[MakeTarget] = []
        self._tool_to_target: dict[str, str] = {}
        self._cache: TargetCache = TargetCache()
        self._extractor: HybridExtractor | None = None

    async def initialize(self) -> None:
        """Initialize plugin and discover targets."""
        self._extractor = self._create_extractor()
        await self._refresh_targets()

    async def shutdown(self) -> None:
        """Clean up plugin resources.

        Called when the plugin is being unloaded or the application is shutting down.
        """
        # Clear cache to free memory
        self._cache.invalidate()
        self._targets = []
        self._tool_to_target = {}
        self._extractor = None

        logger.info(
            "plugin_shutdown",
            plugin="makefile",
            instance=self._instance_name
        )

    async def on_config_reload(self, new_config: dict[str, Any]) -> None:
        """Handle configuration reload.

        Called when the plugin configuration is updated at runtime.
        Validates new configuration and refreshes targets if needed.

        Args:
            new_config: The new configuration dictionary.

        Raises:
            ValidationError: If the new configuration is invalid.
        """
        # Validate new configuration before applying
        new_validated = MakefilePluginConfig.model_validate(new_config)

        # Check if cache-affecting settings changed
        cache_invalidation_needed = (
            new_validated.makefile_path != self._config.makefile_path or
            new_validated.extractor != self._config.extractor or
            new_validated.trust_makefile != self._config.trust_makefile or
            new_validated.targets != self._config.targets or
            new_validated.exclude_targets != self._config.exclude_targets
        )

        # Apply new configuration
        old_config = self._config
        self._config = new_validated

        # Recreate extractor if trust setting changed
        if new_validated.trust_makefile != old_config.trust_makefile:
            self._extractor = self._create_extractor()

        # Invalidate cache and refresh if needed
        if cache_invalidation_needed:
            self._cache.invalidate()
            await self._refresh_targets()

        logger.info(
            "config_reloaded",
            plugin="makefile",
            instance=self._instance_name,
            cache_invalidated=cache_invalidation_needed
        )

    async def health_check(self) -> dict[str, Any]:
        """Check plugin health status.

        Verifies that the Makefile exists and is readable, and that
        the make command is available if using make_database extraction.

        Returns:
            Health check result with status and details.
        """
        health = {
            "healthy": True,
            "checks": {},
            "plugin": "makefile",
            "instance": self._instance_name,
        }

        # Check 1: Makefile exists and is readable
        makefile_path = self._resolve_makefile_path()
        try:
            if makefile_path.exists():
                # Try to read the file
                makefile_path.read_text()
                health["checks"]["makefile_readable"] = {
                    "status": "pass",
                    "path": str(makefile_path),
                }
            else:
                health["checks"]["makefile_readable"] = {
                    "status": "fail",
                    "path": str(makefile_path),
                    "error": "File does not exist",
                }
                health["healthy"] = False
        except PermissionError:
            health["checks"]["makefile_readable"] = {
                "status": "fail",
                "path": str(makefile_path),
                "error": "Permission denied",
            }
            health["healthy"] = False
        except Exception as e:
            health["checks"]["makefile_readable"] = {
                "status": "fail",
                "path": str(makefile_path),
                "error": str(e),
            }
            health["healthy"] = False

        # Check 2: Make command available (if using make_database strategy)
        if self._config.extractor in (ExtractorStrategy.MAKE_DATABASE, ExtractorStrategy.AUTO):
            make_path = shutil.which(self._config.make_command)
            if make_path:
                health["checks"]["make_command"] = {
                    "status": "pass",
                    "command": self._config.make_command,
                    "path": make_path,
                }
            else:
                health["checks"]["make_command"] = {
                    "status": "fail",
                    "command": self._config.make_command,
                    "error": "Command not found in PATH",
                }
                # Only mark unhealthy if make_database is explicitly required
                if self._config.extractor == ExtractorStrategy.MAKE_DATABASE:
                    health["healthy"] = False

        # Check 3: Targets discovered
        health["checks"]["targets_discovered"] = {
            "status": "pass" if self._targets else "warn",
            "count": len(self._targets),
        }

        return health

    def get_tools(self) -> list[ToolDefinition]:
        """Return tool definitions for discovered targets."""
        tools = []

        # Optionally add list_targets tool
        if self._config.expose_list_targets:
            tools.append(ToolDefinition(
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
                            "is_phony": {"type": "boolean"}
                        }
                    }
                }
            ))

        # Add tool for each target
        for target in self._targets:
            tool_def = target.to_tool_definition()
            tools.append(ToolDefinition(**tool_def))

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute the requested make target."""
        if tool_name == "make_list_targets":
            return await self._list_targets()

        target = self._tool_to_target.get(tool_name)
        if target is None:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}"
            )

        return await self._execute_target(target, arguments)
```

---

## Target Discovery Flow

### Initialization Sequence

```
Plugin Load
     |
     v
+--------------------+
| Parse Config       |
| (MakefilePluginConfig)
+--------------------+
     |
     v
+--------------------+
| Resolve Makefile   |
| Path               |
+--------------------+
     |
     v
+--------------------+
| Check Makefile     |
| Exists & Readable  |
+--------------------+
     |
     +-------+-------+
     |               |
     v               v
  Exists         Not Found
     |               |
     v               v
+----------+    +----------+
| Create   |    | Log      |
| Extractor|    | Warning  |
+----------+    +----------+
     |               |
     v               v
+----------+    +----------+
| Extract  |    | Return   |
| Targets  |    | Empty    |
+----------+    +----------+
     |
     v
+--------------------+
| Filter by Patterns |
| (include/exclude)  |
+--------------------+
     |
     v
+--------------------+
| Build Tool Name    |
| Mapping            |
+--------------------+
     |
     v
+--------------------+
| Cache Results      |
+--------------------+
     |
     v
+--------------------+
| Register Tools     |
+--------------------+
```

### Pattern Matching Logic

```python
import fnmatch


class TargetFilter:
    """Filters targets based on include/exclude patterns.

    Uses fnmatch (Unix shell-style wildcards), NOT regex.
    Supported patterns: *, ?, [seq], [!seq]
    """

    def __init__(
        self,
        include_patterns: list[str],
        exclude_patterns: list[str]
    ) -> None:
        self.include_patterns = include_patterns or ["*"]
        self.exclude_patterns = exclude_patterns or []

    def matches(self, target_name: str) -> bool:
        """Check if target matches filter criteria.

        Uses fnmatch for Unix shell-style wildcard matching:
        - * matches everything
        - ? matches any single character
        - [seq] matches any character in seq
        - [!seq] matches any character not in seq

        Note: This is NOT regex matching.
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
    def from_config(cls, config: MakefilePluginConfig) -> "TargetFilter":
        """Create filter from configuration."""
        include = [
            p.strip()
            for p in config.targets.split(",")
            if p.strip()
        ]
        exclude = [
            p.strip()
            for p in config.exclude_targets.split(",")
            if p.strip()
        ]
        return cls(include, exclude)
```

---

## Caching Strategy

### Cache Implementation

```python
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CacheEntry:
    """Cache entry for extracted targets with comprehensive invalidation tracking."""

    targets: list[MakeTarget]
    """Cached list of extracted targets."""

    strategy_used: ExtractorStrategy
    """The extraction strategy that was used."""

    timestamp: float
    """Unix timestamp when the cache entry was created."""

    makefile_content_hash: str
    """SHA-256 hash of the Makefile content for change detection."""

    included_files: dict[str, float] = field(default_factory=dict)
    """Map of included file paths to their mtimes at cache time."""

    config_hash: str = ""
    """Hash of relevant configuration fields that affect extraction."""

    def is_valid(
        self,
        ttl: int,
        current_content_hash: str,
        current_included_mtimes: dict[str, float],
        current_config_hash: str
    ) -> bool:
        """Check if cache entry is still valid.

        Args:
            ttl: Cache time-to-live in seconds.
            current_content_hash: Current SHA-256 hash of Makefile content.
            current_included_mtimes: Current mtimes of included files.
            current_config_hash: Current hash of relevant config fields.

        Returns:
            True if cache is still valid, False otherwise.
        """
        if ttl <= 0:
            return False

        # Invalid if TTL expired
        age = time.time() - self.timestamp
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
        for path in current_included_mtimes:
            if path not in self.included_files:
                # New included file appeared
                return False

        return True


class TargetCache:
    """Cache for extracted Makefile targets with content-based invalidation."""

    def __init__(self) -> None:
        self._cache: dict[str, CacheEntry] = {}

    @staticmethod
    def _hash_content(content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    @staticmethod
    def _hash_config(config: MakefilePluginConfig) -> str:
        """Compute hash of configuration fields that affect extraction.

        Includes fields that would change the extraction results.
        """
        relevant_fields = (
            config.targets,
            config.exclude_targets,
            config.extractor.value,
            config.trust_makefile,
            config.description_prefix,
        )
        combined = "|".join(str(f) for f in relevant_fields)
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()

    @staticmethod
    def _get_included_files(makefile_path: Path) -> dict[str, float]:
        """Extract included file paths and their mtimes.

        Parses include/sinclude/-include directives from the Makefile.
        """
        included_files = {}
        try:
            content = makefile_path.read_text()
            base_dir = makefile_path.parent

            # Match include, -include, and sinclude directives
            include_pattern = re.compile(
                r'^(?:-include|sinclude|include)\s+(.+)$',
                re.MULTILINE
            )

            for match in include_pattern.finditer(content):
                # Handle multiple files on one line and wildcards
                file_specs = match.group(1).split()
                for spec in file_specs:
                    # Skip variable references
                    if '$(' in spec or '${' in spec:
                        continue

                    # Resolve path relative to Makefile directory
                    include_path = base_dir / spec
                    if include_path.exists():
                        try:
                            included_files[str(include_path.resolve())] = include_path.stat().st_mtime
                        except OSError:
                            pass

        except Exception:
            pass

        return included_files

    def get(
        self,
        makefile_path: str,
        ttl: int,
        config: MakefilePluginConfig
    ) -> CacheEntry | None:
        """Get cached targets if valid."""
        entry = self._cache.get(makefile_path)
        if entry is None:
            return None

        path = Path(makefile_path)
        try:
            # Compute current content hash
            content = path.read_text()
            current_hash = self._hash_content(content)

            # Get current included file mtimes
            current_included_mtimes = self._get_included_files(path)

            # Compute current config hash
            current_config_hash = self._hash_config(config)

        except OSError:
            # File not accessible, invalidate cache
            del self._cache[makefile_path]
            return None

        if entry.is_valid(ttl, current_hash, current_included_mtimes, current_config_hash):
            return entry

        # Cache invalid, remove it
        del self._cache[makefile_path]
        return None

    def set(
        self,
        makefile_path: str,
        targets: list[MakeTarget],
        strategy: ExtractorStrategy,
        config: MakefilePluginConfig
    ) -> None:
        """Cache extracted targets."""
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
        """Invalidate cache entries."""
        if makefile_path is None:
            self._cache.clear()
        elif makefile_path in self._cache:
            del self._cache[makefile_path]
```

### Cache Invalidation Triggers

| Trigger | Detection Method | Action |
|---------|------------------|--------|
| TTL expiration | Compare timestamp | Re-extract on next access |
| Makefile content change | SHA-256 hash comparison | Invalidate and re-extract |
| Included file modification | Compare stored mtimes | Invalidate and re-extract |
| New included file | Check for unknown paths | Invalidate and re-extract |
| Configuration change | Hash of relevant config fields | Invalidate and re-extract |
| Manual invalidation | Via `make_refresh_targets` tool | Full cache clear |
| Plugin config reload | `on_config_reload()` callback | Conditional invalidation |

---

## Observability

### Metrics

The Makefile plugin exposes the following metrics for monitoring and alerting.

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `makefile_extraction_duration_seconds` | Histogram | `strategy`, `instance` | Time taken to extract targets from Makefile |
| `makefile_extraction_total` | Counter | `strategy`, `instance`, `status` | Total number of extraction attempts |
| `makefile_cache_hit_total` | Counter | `instance` | Total number of cache hits |
| `makefile_cache_miss_total` | Counter | `instance`, `reason` | Total number of cache misses with reason |
| `makefile_target_execution_duration_seconds` | Histogram | `target`, `instance` | Time taken to execute a make target |
| `makefile_target_execution_total` | Counter | `target`, `instance`, `status` | Total number of target executions |
| `makefile_targets_discovered` | Gauge | `instance` | Number of targets currently discovered |
| `makefile_health_check_status` | Gauge | `instance`, `check` | Health check status (1=pass, 0=fail) |

**Example Prometheus metrics output:**
```
# HELP makefile_extraction_duration_seconds Time taken to extract targets
# TYPE makefile_extraction_duration_seconds histogram
makefile_extraction_duration_seconds_bucket{strategy="simple",instance="makefile",le="0.01"} 45
makefile_extraction_duration_seconds_bucket{strategy="make_database",instance="makefile",le="0.5"} 12

# HELP makefile_cache_hit_total Total cache hits
# TYPE makefile_cache_hit_total counter
makefile_cache_hit_total{instance="makefile"} 156

# HELP makefile_target_execution_duration_seconds Target execution time
# TYPE makefile_target_execution_duration_seconds histogram
makefile_target_execution_duration_seconds_bucket{target="test",instance="makefile",le="60"} 23
```

### Structured Logging

The plugin uses structured logging with consistent field names for correlation and analysis.

**Log Levels:**
| Level | Usage |
|-------|-------|
| `DEBUG` | Detailed extraction steps, cache operations, pattern matching |
| `INFO` | Target discovery completion, configuration reload, plugin lifecycle |
| `WARNING` | Fallback to simple extraction, untrusted Makefile forced simple, deprecated config |
| `ERROR` | Extraction failures, execution failures, health check failures |

**Standard Log Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `plugin` | string | Always "makefile" |
| `instance` | string | Plugin instance name (e.g., "makefile", "makefile_backend") |
| `trace_id` | string | Correlation ID from parent plugin system |
| `makefile_path` | string | Path to the Makefile being processed |
| `strategy` | string | Extraction strategy used |
| `target` | string | Target name (for execution logs) |
| `duration_ms` | number | Operation duration in milliseconds |
| `error` | string | Error message (for error logs) |
| `cause` | string | Root cause exception (for error logs) |

**Example Log Messages:**
```json
{
  "level": "INFO",
  "message": "targets_extracted",
  "plugin": "makefile",
  "instance": "makefile",
  "trace_id": "abc123",
  "makefile_path": "/app/Makefile",
  "strategy": "simple",
  "target_count": 15,
  "duration_ms": 2.5
}

{
  "level": "WARNING",
  "message": "untrusted_makefile_forced_simple",
  "plugin": "makefile",
  "instance": "makefile",
  "trace_id": "def456",
  "makefile_path": "/untrusted/Makefile",
  "reason": "trust_makefile=False, complex features detected"
}

{
  "level": "ERROR",
  "message": "target_execution_failed",
  "plugin": "makefile",
  "instance": "makefile",
  "trace_id": "ghi789",
  "target": "build",
  "exit_code": 2,
  "duration_ms": 5432,
  "error": "Recipe for target 'build' failed"
}
```

### Correlation with Parent Plugin System

The Makefile plugin integrates with OpenCuff's observability infrastructure.

**Trace Context Propagation:**
```python
class MakefilePlugin(InSourcePlugin):
    """Plugin with observability integration."""

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext  # Provided by plugin manager
    ) -> ToolResult:
        """Execute tool with trace context."""
        # Extract trace ID from context for log correlation
        trace_id = context.trace_id

        with self._metrics.timer(
            "makefile_target_execution_duration_seconds",
            labels={"target": tool_name, "instance": self._instance_name}
        ):
            logger.info(
                "target_execution_started",
                plugin="makefile",
                instance=self._instance_name,
                trace_id=trace_id,
                target=tool_name,
            )

            result = await self._execute_target(tool_name, arguments)

            logger.info(
                "target_execution_completed",
                plugin="makefile",
                instance=self._instance_name,
                trace_id=trace_id,
                target=tool_name,
                exit_code=result.exit_code,
                duration_ms=result.duration_seconds * 1000,
            )

            return result
```

**Parent System Integration Points:**
| Integration | Method |
|-------------|--------|
| Metrics registry | Metrics are registered with the parent plugin manager's registry |
| Log correlation | `trace_id` from `ToolContext` is included in all log entries |
| Health aggregation | `health_check()` results are aggregated by the plugin manager |
| Config reload events | `on_config_reload()` is called by the plugin manager on SIGHUP |

---

## Example Configurations

### Basic Configuration

```yaml
plugins:
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./Makefile
```

### Development Project

```yaml
plugins:
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./Makefile
      targets: "install,install-*,test,test-*,lint,format,build,clean"
      exclude_targets: "*-internal,_*"
      extractor: auto
      cache_ttl: 300
      allow_parallel: true
      default_timeout: 600
      environment:
        CI: "false"
        DEBUG: "1"
```

### CI/CD Pipeline

```yaml
plugins:
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: /workspace/Makefile
      targets: "ci-*,release-*"
      extractor: simple  # Faster, CI Makefiles are typically simple
      cache_ttl: 0  # Disable caching in CI
      allow_parallel: true
      parallel_jobs: 4
      default_timeout: 1800
      working_directory: /workspace
```

### Monorepo with Multiple Makefiles

```yaml
plugins:
  makefile_root:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./Makefile
      targets: "all,clean,test-all"
      extractor: make_database

  makefile_backend:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./backend/Makefile
      targets: "*"
      working_directory: ./backend

  makefile_frontend:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./frontend/Makefile
      targets: "*"
      working_directory: ./frontend
```

### Restricted Access Configuration

```yaml
plugins:
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./Makefile
      targets: "test-*,lint,check"  # Only expose safe targets
      exclude_targets: "deploy-*,release-*,clean-all"  # Block destructive targets
      extractor: simple
      allow_parallel: false
      default_timeout: 120
      expose_list_targets: false  # Don't reveal all available targets
```

### Untrusted Makefile Configuration

```yaml
# Use this configuration for Makefiles from untrusted sources
# (e.g., cloned repositories, user-provided files)
plugins:
  makefile:
    type: in_source
    enabled: true
    module: opencuff.plugins.makefile
    config:
      makefile_path: ./untrusted-project/Makefile
      trust_makefile: false  # CRITICAL: Prevents $(shell ...) execution
      extractor: simple      # Redundant but explicit - trust_makefile:false forces this
      targets: "build,test"  # Limit to known safe targets
      exclude_targets: "*"   # Exclude everything except explicit targets
      default_timeout: 60    # Short timeout for safety
      allow_parallel: false  # Reduce attack surface
```

---

## Error Handling

### Error Categories

```python
from enum import Enum


class MakefilePluginErrorCode(str, Enum):
    """Error codes specific to Makefile plugin."""

    # Configuration errors
    INVALID_CONFIG = "MAKEFILE_INVALID_CONFIG"
    INVALID_PATTERN = "MAKEFILE_INVALID_PATTERN"

    # File errors
    MAKEFILE_NOT_FOUND = "MAKEFILE_NOT_FOUND"
    MAKEFILE_NOT_READABLE = "MAKEFILE_NOT_READABLE"
    MAKEFILE_PARSE_ERROR = "MAKEFILE_PARSE_ERROR"

    # Extraction errors
    EXTRACTOR_ERROR = "MAKEFILE_EXTRACTOR_ERROR"
    MAKE_NOT_FOUND = "MAKEFILE_MAKE_NOT_FOUND"
    MAKE_TIMEOUT = "MAKEFILE_MAKE_TIMEOUT"
    UNTRUSTED_MAKEFILE = "MAKEFILE_UNTRUSTED"

    # Execution errors
    TARGET_NOT_FOUND = "MAKEFILE_TARGET_NOT_FOUND"
    TARGET_EXECUTION_FAILED = "MAKEFILE_TARGET_EXECUTION_FAILED"
    TARGET_TIMEOUT = "MAKEFILE_TARGET_TIMEOUT"


class MakefilePluginError(Exception):
    """Exception for Makefile plugin errors."""

    def __init__(
        self,
        code: MakefilePluginErrorCode,
        message: str,
        target: str | None = None,
        cause: Exception | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.target = target
        self.cause = cause
        super().__init__(f"[{code}] {message}")
```

### Error Handling Strategies

```
                    Error Detected
                          |
                          v
                +-------------------+
                | Categorize Error  |
                +-------------------+
                          |
        +-----------------+-----------------+
        |                 |                 |
        v                 v                 v
  Config Error      Extraction Error   Execution Error
        |                 |                 |
        v                 v                 v
  +----------+      +----------+      +----------+
  | Log &    |      | Fallback |      | Return   |
  | Fail     |      | Strategy |      | Error    |
  | Init     |      |          |      | Result   |
  +----------+      +----------+      +----------+
                          |
              +-----------+-----------+
              |                       |
              v                       v
        make_database           simple failed?
        failed?                       |
              |                       v
              v                 +----------+
        +----------+            | Return   |
        | Try      |            | Empty    |
        | simple   |            | Targets  |
        +----------+            +----------+
```

### Graceful Degradation

```python
class MakefilePlugin(InSourcePlugin):
    """Plugin with graceful error handling."""

    async def _refresh_targets(self) -> None:
        """Refresh target list with fallback strategies."""
        makefile_path = self._resolve_makefile_path()

        # Check if Makefile exists
        if not makefile_path.exists():
            logger.warning(
                "makefile_not_found",
                path=str(makefile_path)
            )
            self._targets = []
            return

        # Try extraction with fallback
        try:
            targets, strategy = await self._extractor.extract(
                makefile_path,
                self._config.extractor
            )
            logger.info(
                "targets_extracted",
                count=len(targets),
                strategy=strategy.value
            )
        except ExtractorError as e:
            logger.error(
                "extraction_failed",
                error=str(e),
                cause=str(e.cause) if e.cause else None
            )

            # Fallback to simple extractor if database failed
            if self._config.extractor != ExtractorStrategy.SIMPLE:
                try:
                    targets = await self._simple_extractor.extract(makefile_path)
                    strategy = ExtractorStrategy.SIMPLE
                    logger.info(
                        "fallback_extraction_succeeded",
                        count=len(targets)
                    )
                except Exception as fallback_error:
                    logger.error(
                        "fallback_extraction_failed",
                        error=str(fallback_error)
                    )
                    targets = []
            else:
                targets = []

        # Filter targets
        target_filter = TargetFilter.from_config(self._config)
        self._targets = [t for t in targets if target_filter.matches(t.name)]

        # Build tool mapping
        self._tool_to_target = {
            t.to_tool_name(): t.name for t in self._targets
        }

        # Update cache
        if self._config.cache_ttl > 0:
            self._cache.set(str(makefile_path), self._targets, strategy, self._config)
```

---

## Security Considerations

> **CRITICAL SECURITY WARNING**
>
> The `make -pn` command (used by `make_database` and `auto` extraction strategies) **executes
> `$(shell ...)` commands** and other Make functions during parsing. This means:
>
> - **Arbitrary code can be executed** just by parsing a Makefile
> - Malicious Makefiles can run commands, exfiltrate data, or compromise the system
> - This happens during **target discovery**, not target execution
>
> **Mitigations:**
> 1. Set `trust_makefile: false` for any Makefile from an untrusted source
> 2. Use `extractor: simple` explicitly for untrusted Makefiles
> 3. Review Makefiles before enabling `trust_makefile: true`
> 4. Run OpenCuff with minimal privileges when processing untrusted Makefiles

### 1. Command Injection Prevention

```python
import shlex


class MakeExecutor:
    """Secure make command executor."""

    ALLOWED_MAKE_FLAGS = {
        "-j", "--jobs",
        "-n", "--dry-run",
        "-s", "--silent",
        "-k", "--keep-going",
        "-B", "--always-make",
    }

    def _validate_extra_args(self, extra_args: str) -> list[str]:
        """Validate and sanitize extra arguments."""
        if not extra_args:
            return []

        # Parse arguments safely
        try:
            args = shlex.split(extra_args)
        except ValueError as e:
            raise MakefilePluginError(
                code=MakefilePluginErrorCode.INVALID_CONFIG,
                message=f"Invalid extra arguments: {e}"
            )

        # Validate each argument
        validated = []
        i = 0
        while i < len(args):
            arg = args[i]

            # Check for command injection attempts
            if any(c in arg for c in [';', '|', '&', '`', '$', '(', ')']):
                raise MakefilePluginError(
                    code=MakefilePluginErrorCode.INVALID_CONFIG,
                    message=f"Invalid character in argument: {arg}"
                )

            # Allow known safe flags
            if arg.startswith('-'):
                flag = arg.split('=')[0]
                if flag not in self.ALLOWED_MAKE_FLAGS:
                    logger.warning(
                        "unknown_make_flag",
                        flag=flag
                    )

            validated.append(arg)
            i += 1

        return validated

    def _build_command(
        self,
        target: str,
        config: MakefilePluginConfig,
        extra_args: list[str],
        dry_run: bool = False
    ) -> list[str]:
        """Build the make command safely."""
        cmd = [config.make_command]

        # Add Makefile path
        cmd.extend(["-f", str(config.makefile_path)])

        # Add parallel jobs if allowed
        if config.allow_parallel:
            jobs = config.parallel_jobs or self._get_cpu_count()
            cmd.extend(["-j", str(jobs)])

        # Add dry-run flag if requested
        if dry_run:
            cmd.append("-n")

        # Add validated extra args
        cmd.extend(extra_args)

        # Add target (already validated during extraction)
        cmd.append(target)

        return cmd
```

### 2. Path Traversal Prevention

```python
from pathlib import Path


def _resolve_makefile_path(self) -> Path:
    """Resolve and validate Makefile path."""
    makefile = Path(self._config.makefile_path)

    # Resolve working directory
    if self._config.working_directory:
        base_dir = Path(self._config.working_directory).resolve()
    else:
        base_dir = Path.cwd()

    # Resolve Makefile path relative to working directory
    if not makefile.is_absolute():
        makefile = base_dir / makefile

    makefile = makefile.resolve()

    # Verify the path is within allowed boundaries
    # (Optional: configure allowed directories)
    try:
        makefile.relative_to(base_dir)
    except ValueError:
        raise MakefilePluginError(
            code=MakefilePluginErrorCode.INVALID_CONFIG,
            message=f"Makefile path escapes working directory: {makefile}"
        )

    return makefile
```

### 3. Target Name Validation

```python
import re


class TargetValidator:
    """Validates Makefile target names."""

    # Valid target name pattern (conservative)
    VALID_TARGET_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.-]*$')

    # Disallowed target names (internal Make targets, dangerous patterns)
    BLOCKED_TARGETS = {
        '.DEFAULT',
        '.DELETE_ON_ERROR',
        '.EXPORT_ALL_VARIABLES',
        '.IGNORE',
        '.INTERMEDIATE',
        '.LOW_RESOLUTION_TIME',
        '.NOTINTERMEDIATE',
        '.NOTPARALLEL',
        '.ONESHELL',
        '.PHONY',
        '.POSIX',
        '.PRECIOUS',
        '.SECONDARY',
        '.SECONDEXPANSION',
        '.SHELLFLAGS',
        '.SILENT',
        '.SUFFIXES',
    }

    @classmethod
    def is_valid(cls, target: str) -> bool:
        """Check if target name is valid and safe."""
        if not target:
            return False

        if target in cls.BLOCKED_TARGETS:
            return False

        if target.startswith('.'):
            return False

        if not cls.VALID_TARGET_PATTERN.match(target):
            return False

        return True
```

### 4. Environment Variable Security

```python
import os


def _build_environment(self, config: MakefilePluginConfig) -> dict[str, str]:
    """Build secure environment for make execution."""
    # Start with minimal environment
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }

    # Add configured environment variables
    for key, value in config.environment.items():
        # Validate key
        if not re.match(r'^[A-Z_][A-Z0-9_]*$', key):
            logger.warning(
                "invalid_env_var_name",
                key=key
            )
            continue

        # Expand environment variable references
        expanded = os.path.expandvars(value)
        env[key] = expanded

    return env
```

### 5. Resource Limits

```python
import asyncio
import resource


class MakeExecutor:
    """Executor with resource limits."""

    async def execute(
        self,
        cmd: list[str],
        config: MakefilePluginConfig,
        timeout: int
    ) -> dict[str, Any]:
        """Execute make with resource limits."""

        def set_limits() -> None:
            """Set resource limits for subprocess."""
            # Limit CPU time
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (timeout, timeout)
            )
            # Limit memory (1GB)
            resource.setrlimit(
                resource.RLIMIT_AS,
                (1024 * 1024 * 1024, 1024 * 1024 * 1024)
            )
            # Limit number of processes
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (100, 100)
            )

        env = self._build_environment(config)

        start_time = time.time()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=config.working_directory,
            env=env,
            preexec_fn=set_limits
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            duration = time.time() - start_time

            return {
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "exit_code": process.returncode,
                "duration_seconds": round(duration, 2)
            }

        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise MakefilePluginError(
                code=MakefilePluginErrorCode.TARGET_TIMEOUT,
                message=f"Target execution timed out after {timeout}s"
            )
```

### Security Checklist

| Risk | Mitigation | Priority |
|------|------------|----------|
| **Code execution via `make -pn`** | **Use `trust_makefile: false` for untrusted Makefiles; forces simple extractor** | **CRITICAL** |
| Command injection via target name | Validate target names against strict pattern | High |
| Command injection via extra_args | Parse with shlex, whitelist flags, block shell metacharacters | High |
| Path traversal | Resolve paths, validate within allowed boundaries | High |
| Environment leakage | Use minimal environment, validate variable names | Medium |
| Resource exhaustion | Apply CPU, memory, and process limits | Medium |
| Sensitive output exposure | Log only metadata, not command output | Medium |
| Side effects from extraction | Document that `make_database` may execute shell commands; use `trust_makefile` | High |

---

## Future Considerations

### Potential Enhancements (Not in Scope for v1)

1. **Target Dependency Visualization**
   - Parse and expose dependency graph
   - Provide tool for querying dependencies
   - Integration with visualization tools

2. **Incremental Target Detection**
   - Watch Makefile for changes
   - Automatically refresh targets
   - Notify on target changes

3. **Make Variable Exposure**
   - Extract and expose Make variables
   - Allow variable overrides per invocation
   - Support variable documentation

4. **Multi-Makefile Support**
   - Single plugin instance handling multiple Makefiles
   - Unified target namespace with prefixes
   - Cross-Makefile dependency resolution

5. **Target Execution History**
   - Track execution history and outcomes
   - Provide statistics and timing data
   - Support replay/retry functionality

6. **Alternative Make Implementations**
   - Support for BSD Make
   - Support for CMake integration
   - Support for Ninja build files

---

## Appendix A: Recommended Dependencies

| Package | Purpose | Version |
|---------|---------|---------|
| `pydantic` | Configuration validation | >=2.0 |
| None additional | Core functionality uses stdlib | - |

Note: The Makefile plugin intentionally minimizes dependencies, using only Python stdlib for parsing and subprocess management. This ensures reliability and reduces attack surface.

---

## Appendix B: Make Database Output Format

Example output from `make -pn`:

```
# GNU Make 4.3
# Built for x86_64-pc-linux-gnu

# Make data base, printed on Sun Jan 18 10:00:00 2026

# Variables

# ...variable definitions...

# Files

# Not a target:
.SUFFIXES:

test: src/*.py
#  Phony target (prerequisite of .PHONY).
#  Implicit rule search has not been done.
#  File does not exist.
#  File has not been updated.
#  recipe to execute (from 'Makefile', line 15):
	pytest tests/

install: setup.py
#  Implicit rule search has not been done.
#  File does not exist.
#  File has not been updated.
#  recipe to execute (from 'Makefile', line 10):
	pip install -e .

.PHONY: test install clean
#  Implicit rule search has not been done.
#  Implicit/static pattern stem: ''
#  File does not exist.
#  File has been updated.
#  Successfully updated.
```

---

## Appendix C: Description Comment Conventions

The plugin supports extracting target descriptions from specially formatted comments.

### Supported Formats

**Format 1: Double hash prefix (default)**
```makefile
## Install project dependencies
install:
	pip install -r requirements.txt

## Run all unit tests with coverage
test:
	pytest --cov=src tests/
```

**Format 2: Inline comment (self-documenting targets)**
```makefile
install: ## Install project dependencies
	pip install -r requirements.txt

test: ## Run all unit tests with coverage
	pytest --cov=src tests/

build: deps ## Build the project (runs deps first)
	go build ./...
```

Both formats are detected and extracted by the plugin. When both formats are present for the same target, the inline format (Format 2) takes precedence.

The inline format is particularly useful for generating help output with:

```makefile
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'
```

---

## Appendix D: Generated Makefiles (Automake/CMake)

The Makefile plugin can work with Makefiles generated by build system generators like Automake and CMake, but there are important considerations.

### Automake-Generated Makefiles

Automake generates `Makefile.in` templates that are processed by `configure` to produce the final `Makefile`.

**Considerations:**
- Generated Makefiles often contain many internal targets (prefixed with `am__`)
- Use `exclude_targets: "am__*,DIST*,dist*"` to filter out internal targets
- The `make_database` strategy works well since Automake generates valid GNU Makefiles
- Targets may change after re-running `./configure`

**Recommended Configuration:**
```yaml
plugins:
  makefile:
    config:
      makefile_path: ./Makefile
      extractor: make_database  # Handles includes and variables
      exclude_targets: "am__*,DIST*,dist*,*-local,*-am"
      targets: "all,install,check,clean,distclean"
```

### CMake-Generated Makefiles

CMake can generate Unix Makefiles via `cmake -G "Unix Makefiles"`.

**Considerations:**
- CMake generates complex Makefiles with many internal targets
- Targets are often prefixed with the project structure
- The `make_database` strategy is recommended for full target discovery
- Targets change when CMakeLists.txt is modified and CMake is re-run

**Recommended Configuration:**
```yaml
plugins:
  makefile:
    config:
      makefile_path: ./build/Makefile
      working_directory: ./build
      extractor: make_database
      exclude_targets: "cmake_*,CMakeFiles/*,edit_cache,rebuild_cache"
      targets: "all,install,test,clean,*_tests"
```

### General Guidance for Generated Makefiles

1. **Cache invalidation**: Generated Makefiles may not include all source files. Consider setting `cache_ttl: 0` or using content hashing.

2. **Re-generation detection**: The plugin does not detect when the generator (configure, cmake) needs to be re-run. Users should manually refresh targets after regeneration.

3. **Target filtering**: Generated Makefiles often have hundreds of targets. Use specific `targets` patterns rather than `*` to expose only relevant targets.

4. **Trust considerations**: Generated Makefiles from build system generators are generally safe to trust if you trust the source project. However, if the project itself is untrusted, use `trust_makefile: false`.
