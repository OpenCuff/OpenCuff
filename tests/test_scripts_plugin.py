"""Tests for the scripts plugin.

Tests cover:
    - Configuration validation (TestScriptsPluginConfig)
    - Security constants (TestSecurityConstants)
    - Argument sanitization (TestArgumentSanitization)
    - Environment validation (TestEnvironmentValidation)
    - Path validation (TestPathValidation)
    - Tool naming (TestToolNaming)
    - Description extraction (TestDescriptionExtraction)
    - Interpreter detection (TestInterpreterDetection)
    - Script filtering (TestScriptFilter)
    - Script caching (TestScriptCache)
    - Plugin lifecycle (TestScriptsPlugin)
    - Health checks (TestHealthCheck)
    - Script execution (TestScriptExecution)
    - Discovery interface (TestDiscovery)
    - CLI commands (TestCLICommands)
    - Integration tests (TestScriptsPluginIntegration)
"""

from __future__ import annotations

import stat
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from opencuff.plugins.builtin.scripts import (
    BLOCKED_ENV_VARS,
    DANGEROUS_CHARS,
    DEFAULT_INTERPRETERS,
    CacheEntry,
    Plugin,
    ScriptCache,
    ScriptFilter,
    ScriptInfo,
    ScriptsPluginConfig,
)

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "scripts"


# =============================================================================
# TestScriptsPluginConfig
# =============================================================================


class TestScriptsPluginConfig:
    """Tests for ScriptsPluginConfig validation."""

    def test_default_values(self) -> None:
        """Verify default configuration values."""
        config = ScriptsPluginConfig(patterns=["scripts/*.sh"])

        assert config.patterns == ["scripts/*.sh"]
        assert config.base_directory == "."
        assert config.exclude == []
        assert config.default_timeout == 300
        assert config.working_directory == "."
        assert config.environment == {}
        assert config.expose_list_scripts is True
        assert config.interpreters == {}
        assert config.require_executable is False
        assert config.cache_ttl == 300

    def test_custom_values(self) -> None:
        """Verify custom configuration values are accepted."""
        config = ScriptsPluginConfig(
            patterns=["scripts/*.sh", "tools/**/*.py"],
            base_directory="/workspace",
            exclude=["*_test.sh", "internal_*"],
            default_timeout=600,
            working_directory="/work",
            environment={"CI": "true", "VERBOSE": "1"},
            expose_list_scripts=False,
            interpreters={".sh": "/bin/bash", ".py": "python3"},
            require_executable=True,
            cache_ttl=600,
        )

        assert config.patterns == ["scripts/*.sh", "tools/**/*.py"]
        assert config.base_directory == "/workspace"
        assert config.exclude == ["*_test.sh", "internal_*"]
        assert config.default_timeout == 600
        assert config.working_directory == "/work"
        assert config.environment == {"CI": "true", "VERBOSE": "1"}
        assert config.expose_list_scripts is False
        assert config.interpreters == {".sh": "/bin/bash", ".py": "python3"}
        assert config.require_executable is True
        assert config.cache_ttl == 600

    def test_patterns_required(self) -> None:
        """Verify patterns field is required."""
        with pytest.raises(ValidationError):
            ScriptsPluginConfig()

    def test_empty_patterns_rejected(self) -> None:
        """Verify empty patterns list is rejected."""
        with pytest.raises(ValidationError):
            ScriptsPluginConfig(patterns=[])

    def test_cache_ttl_validation(self) -> None:
        """Verify cache_ttl cannot be negative."""
        with pytest.raises(ValidationError):
            ScriptsPluginConfig(patterns=["*.sh"], cache_ttl=-1)

    def test_default_timeout_validation(self) -> None:
        """Verify default_timeout must be positive."""
        with pytest.raises(ValidationError):
            ScriptsPluginConfig(patterns=["*.sh"], default_timeout=0)


# =============================================================================
# TestSecurityConstants
# =============================================================================


class TestSecurityConstants:
    """Tests for security-related constants."""

    def test_dangerous_chars_defined(self) -> None:
        """Verify DANGEROUS_CHARS contains expected shell metacharacters."""
        expected = frozenset(";&|`$(){}[]<>\\'\"!*?~\n\r")
        assert expected == DANGEROUS_CHARS

    def test_blocked_env_vars_defined(self) -> None:
        """Verify BLOCKED_ENV_VARS contains critical security-sensitive vars."""
        critical_vars = {
            "PATH",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES",
            "DYLD_LIBRARY_PATH",
            "PYTHONPATH",
            "NODE_PATH",
            "RUBYLIB",
            "PERL5LIB",
            "HOME",
            "USER",
            "SHELL",
        }
        assert critical_vars.issubset(BLOCKED_ENV_VARS)

    def test_default_interpreters_defined(self) -> None:
        """Verify DEFAULT_INTERPRETERS contains common script types."""
        expected_extensions = {".sh", ".bash", ".py", ".rb", ".js", ".pl"}
        assert expected_extensions.issubset(set(DEFAULT_INTERPRETERS.keys()))


# =============================================================================
# TestArgumentSanitization
# =============================================================================


class TestArgumentSanitization:
    """Tests for argument sanitization via _sanitize_args."""

    @pytest.mark.asyncio
    async def test_valid_arguments(self) -> None:
        """Verify valid arguments pass sanitization."""
        plugin = Plugin({"patterns": ["*.sh"]})

        valid_args = [
            ["--verbose", "--coverage"],
            ["file.txt", "output.log"],
            ["--flag=value"],
            ["-j", "4"],
        ]
        for args in valid_args:
            result = plugin._sanitize_args(args)
            assert result == args

    @pytest.mark.asyncio
    async def test_dangerous_semicolon_blocked(self) -> None:
        """Verify semicolon is blocked to prevent command chaining."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._sanitize_args(["--flag; rm -rf /"])
        assert "dangerous" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_dangerous_pipe_blocked(self) -> None:
        """Verify pipe is blocked."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._sanitize_args(["--flag | cat /etc/passwd"])
        assert "dangerous" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_dangerous_ampersand_blocked(self) -> None:
        """Verify ampersand is blocked."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._sanitize_args(["--flag & malicious"])
        assert "dangerous" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_dangerous_backtick_blocked(self) -> None:
        """Verify backtick command substitution is blocked."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._sanitize_args(["`whoami`"])
        assert "dangerous" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_dangerous_dollar_blocked(self) -> None:
        """Verify dollar sign variable expansion is blocked."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._sanitize_args(["$HOME"])
        assert "dangerous" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_dangerous_parentheses_blocked(self) -> None:
        """Verify parentheses are blocked."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._sanitize_args(["$(cmd)"])
        assert "dangerous" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_all_dangerous_chars_blocked(self) -> None:
        """Verify all dangerous characters are blocked."""
        plugin = Plugin({"patterns": ["*.sh"]})

        for char in DANGEROUS_CHARS:
            with pytest.raises(ValueError):
                plugin._sanitize_args([f"arg{char}value"])


# =============================================================================
# TestEnvironmentValidation
# =============================================================================


class TestEnvironmentValidation:
    """Tests for environment variable validation via _validate_env."""

    @pytest.mark.asyncio
    async def test_valid_env_vars(self) -> None:
        """Verify valid environment variables pass validation."""
        plugin = Plugin({"patterns": ["*.sh"]})

        valid_env = {"MY_VAR": "value", "ANOTHER_VAR": "123"}
        result = plugin._validate_env(valid_env)
        assert result == valid_env

    @pytest.mark.asyncio
    async def test_blocked_path_rejected(self) -> None:
        """Verify PATH cannot be overridden."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_env({"PATH": "/malicious/path"})
        assert "Blocked" in str(exc_info.value)
        assert "PATH" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_blocked_ld_preload_rejected(self) -> None:
        """Verify LD_PRELOAD cannot be overridden."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_env({"LD_PRELOAD": "/malicious.so"})
        assert "Blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_multiple_blocked_vars_reported(self) -> None:
        """Verify multiple blocked vars are all reported."""
        plugin = Plugin({"patterns": ["*.sh"]})

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_env({"PATH": "/bad", "LD_PRELOAD": "/bad.so"})
        error_msg = str(exc_info.value)
        assert "PATH" in error_msg
        assert "LD_PRELOAD" in error_msg


# =============================================================================
# TestPathValidation
# =============================================================================


class TestPathValidation:
    """Tests for path validation and symlink handling."""

    @pytest.mark.asyncio
    async def test_valid_path_in_base_directory(self, tmp_path: Path) -> None:
        """Verify valid paths within base directory are accepted."""
        # Create a script in the base directory
        script = tmp_path / "scripts" / "build.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\necho hello")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        plugin = Plugin(
            {
                "patterns": ["scripts/*.sh"],
                "base_directory": str(tmp_path),
            }
        )
        await plugin.initialize()

        # This should not raise
        path = plugin._validate_script_path("scripts/build.sh")
        assert path == script.resolve()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Verify path traversal attacks are blocked."""
        script = tmp_path / "scripts" / "build.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\necho hello")

        plugin = Plugin(
            {
                "patterns": ["scripts/*.sh"],
                "base_directory": str(tmp_path),
            }
        )
        await plugin.initialize()

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_script_path("../../../etc/passwd")
        # Accept either "traversal" or "outside base directory" in error message
        error_lower = str(exc_info.value).lower()
        assert "traversal" in error_lower or "outside" in error_lower

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_symlink_inside_base_accepted(self, tmp_path: Path) -> None:
        """Verify symlinks that resolve within base directory are accepted."""
        # Create a real script
        real_script = tmp_path / "real" / "actual.sh"
        real_script.parent.mkdir(parents=True)
        real_script.write_text("#!/bin/bash\necho hello")
        real_script.chmod(real_script.stat().st_mode | stat.S_IXUSR)

        # Create a symlink inside base directory
        link = tmp_path / "scripts" / "link.sh"
        link.parent.mkdir(parents=True)
        link.symlink_to(real_script)

        plugin = Plugin(
            {
                "patterns": ["scripts/*.sh", "real/*.sh"],
                "base_directory": str(tmp_path),
            }
        )
        await plugin.initialize()

        # Should accept the symlink (resolves within base)
        path = plugin._validate_script_path("scripts/link.sh")
        assert path == real_script.resolve()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_symlink_escape_blocked(self, tmp_path: Path) -> None:
        """Verify symlinks that escape base directory are blocked."""
        # Create a script outside base directory
        outside = tmp_path / "outside"
        outside.mkdir()
        evil_script = outside / "evil.sh"
        evil_script.write_text("#!/bin/bash\nrm -rf /")
        evil_script.chmod(evil_script.stat().st_mode | stat.S_IXUSR)

        # Create base directory
        base = tmp_path / "base"
        base.mkdir()

        # Create a symlink that escapes
        link = base / "scripts" / "evil.sh"
        link.parent.mkdir(parents=True)
        link.symlink_to(evil_script)

        plugin = Plugin(
            {
                "patterns": ["scripts/*.sh"],
                "base_directory": str(base),
            }
        )
        await plugin.initialize()

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_script_path("scripts/evil.sh")
        error_msg = str(exc_info.value).lower()
        assert "outside" in error_msg or "escape" in error_msg

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_script_not_found(self, tmp_path: Path) -> None:
        """Verify non-existent scripts raise error."""
        plugin = Plugin(
            {
                "patterns": ["scripts/*.sh"],
                "base_directory": str(tmp_path),
            }
        )
        await plugin.initialize()

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_script_path("scripts/nonexistent.sh")
        assert "not found" in str(exc_info.value).lower()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_script_not_matching_patterns(self, tmp_path: Path) -> None:
        """Verify scripts not matching patterns are rejected."""
        script = tmp_path / "other" / "script.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\necho hello")

        plugin = Plugin(
            {
                "patterns": ["scripts/*.sh"],  # Only matches scripts/ directory
                "base_directory": str(tmp_path),
            }
        )
        await plugin.initialize()

        with pytest.raises(ValueError) as exc_info:
            plugin._validate_script_path("other/script.sh")
        assert "not in allowed patterns" in str(exc_info.value).lower()

        await plugin.shutdown()


# =============================================================================
# TestToolNaming
# =============================================================================


class TestToolNaming:
    """Tests for tool name generation from script paths."""

    def test_simple_script_name(self) -> None:
        """Verify simple script name conversion."""
        assert Plugin._path_to_tool_name(Path("build.sh")) == "script_build"

    def test_script_in_directory(self) -> None:
        """Verify directory path is included in tool name."""
        result = Plugin._path_to_tool_name(Path("scripts/build.sh"))
        assert result == "script_scripts_build"

    def test_nested_directory(self) -> None:
        """Verify nested directory paths are handled."""
        path = Path("scripts/ci/test.sh")
        assert Plugin._path_to_tool_name(path) == "script_scripts_ci_test"

    def test_hyphen_to_underscore(self) -> None:
        """Verify hyphens are converted to underscores."""
        path = Path("scripts/deploy-prod.sh")
        assert Plugin._path_to_tool_name(path) == "script_scripts_deploy_prod"

    def test_dot_to_underscore(self) -> None:
        """Verify dots in basename (excluding extension) are converted."""
        path = Path("scripts/build.prod.sh")
        assert Plugin._path_to_tool_name(path) == "script_scripts_build_prod"

    def test_python_script(self) -> None:
        """Verify Python script extension is removed."""
        path = Path("tools/db-migrate.py")
        assert Plugin._path_to_tool_name(path) == "script_tools_db_migrate"

    def test_no_extension(self) -> None:
        """Verify script without extension works."""
        path = Path("bin/run_server")
        assert Plugin._path_to_tool_name(path) == "script_bin_run_server"


# =============================================================================
# TestDescriptionExtraction
# =============================================================================


class TestDescriptionExtraction:
    """Tests for extracting descriptions from script comments."""

    def test_extract_first_comment_line(self) -> None:
        """Verify first comment after shebang is used as description."""
        content = """#!/bin/bash
# Build the project
echo "Building..."
"""
        desc = Plugin._extract_description(content)
        assert desc == "Build the project"

    def test_extract_multiline_comment_block(self) -> None:
        """Verify only first line of comment block is used."""
        content = """#!/bin/bash
# Deploy the application
#
# This script handles the full deployment process.
echo "Deploying..."
"""
        desc = Plugin._extract_description(content)
        assert desc == "Deploy the application"

    def test_no_comment_returns_none(self) -> None:
        """Verify None is returned when no comment present."""
        content = """#!/bin/bash
echo "No comment"
"""
        desc = Plugin._extract_description(content)
        assert desc is None

    def test_shebang_not_included(self) -> None:
        """Verify shebang line is not treated as description."""
        content = """#!/usr/bin/env python3
print("hello")
"""
        desc = Plugin._extract_description(content)
        assert desc is None

    def test_python_script_comment(self) -> None:
        """Verify Python script comments are extracted."""
        content = """#!/usr/bin/env python3
# A Python script for testing
print("Hello from Python")
"""
        desc = Plugin._extract_description(content)
        assert desc == "A Python script for testing"

    def test_empty_comment_skipped(self) -> None:
        """Verify empty comment lines are skipped."""
        content = """#!/bin/bash
#
# Actual description
echo "hello"
"""
        desc = Plugin._extract_description(content)
        assert desc == "Actual description"


# =============================================================================
# TestInterpreterDetection
# =============================================================================


class TestInterpreterDetection:
    """Tests for interpreter detection from shebang and extension."""

    def test_shebang_bash(self) -> None:
        """Verify bash shebang is detected."""
        content = "#!/bin/bash\necho hello"
        interp = Plugin._detect_interpreter(content, Path("script.sh"), {})
        assert interp == "/bin/bash"

    def test_shebang_env_python3(self) -> None:
        """Verify env python3 shebang is detected."""
        content = "#!/usr/bin/env python3\nprint('hello')"
        interp = Plugin._detect_interpreter(content, Path("script.py"), {})
        assert interp == "python3"

    def test_shebang_env_bash(self) -> None:
        """Verify env bash shebang is detected."""
        content = "#!/usr/bin/env bash\necho hello"
        interp = Plugin._detect_interpreter(content, Path("script.sh"), {})
        assert interp == "bash"

    def test_configured_interpreter_takes_precedence(self) -> None:
        """Verify configured interpreter overrides shebang."""
        content = "#!/bin/bash\necho hello"
        interpreters = {".sh": "/opt/local/bin/bash"}
        interp = Plugin._detect_interpreter(content, Path("script.sh"), interpreters)
        assert interp == "/opt/local/bin/bash"

    def test_default_interpreter_by_extension(self) -> None:
        """Verify default interpreter is used when no shebang."""
        content = "# No shebang\necho hello"
        interp = Plugin._detect_interpreter(content, Path("script.sh"), {})
        assert interp == "/bin/sh"  # Default for .sh

    def test_python_default_interpreter(self) -> None:
        """Verify Python default interpreter."""
        content = "# No shebang\nprint('hello')"
        interp = Plugin._detect_interpreter(content, Path("script.py"), {})
        assert interp == "python3"

    def test_unknown_extension_returns_none(self) -> None:
        """Verify unknown extension with no shebang returns None."""
        content = "some content"
        interp = Plugin._detect_interpreter(content, Path("script.unknown"), {})
        assert interp is None


# =============================================================================
# TestScriptFilter
# =============================================================================


class TestScriptFilter:
    """Tests for ScriptFilter glob patterns."""

    def test_include_all_pattern(self) -> None:
        """Verify * pattern matches all scripts."""
        script_filter = ScriptFilter(["scripts/*.sh"], [])

        assert script_filter.matches(Path("scripts/build.sh")) is True
        assert script_filter.matches(Path("scripts/test.sh")) is True

    def test_exclude_pattern(self) -> None:
        """Verify exclude patterns work."""
        script_filter = ScriptFilter(["scripts/*.sh"], ["scripts/*_test.sh"])

        assert script_filter.matches(Path("scripts/build.sh")) is True
        assert script_filter.matches(Path("scripts/build_test.sh")) is False

    def test_recursive_pattern(self) -> None:
        """Verify ** recursive patterns work.

        Note: In standard glob semantics, ** matches one or more directories.
        So tools/**/*.sh matches tools/ci/test.sh but NOT tools/deploy.sh.
        To match both, use tools/*.sh or tools/**/*.sh together.
        """
        script_filter = ScriptFilter(["tools/**/*.sh"], [])

        # ** matches one or more directories, so direct files don't match
        assert script_filter.matches(Path("tools/deploy.sh")) is False
        # But nested files DO match
        assert script_filter.matches(Path("tools/ci/test.sh")) is True
        assert script_filter.matches(Path("scripts/build.sh")) is False

        # With both patterns, all files match
        filter2 = ScriptFilter(["tools/*.sh", "tools/**/*.sh"], [])
        assert filter2.matches(Path("tools/deploy.sh")) is True
        assert filter2.matches(Path("tools/ci/test.sh")) is True

    def test_exclude_takes_precedence(self) -> None:
        """Verify exclude patterns take precedence over include."""
        script_filter = ScriptFilter(["scripts/*.sh"], ["scripts/internal_*.sh"])

        assert script_filter.matches(Path("scripts/build.sh")) is True
        assert script_filter.matches(Path("scripts/internal_cleanup.sh")) is False

    def test_multiple_include_patterns(self) -> None:
        """Verify multiple include patterns work."""
        script_filter = ScriptFilter(["scripts/*.sh", "tools/*.py"], [])

        assert script_filter.matches(Path("scripts/build.sh")) is True
        assert script_filter.matches(Path("tools/migrate.py")) is True
        assert script_filter.matches(Path("other/file.sh")) is False

    def test_from_config(self) -> None:
        """Verify filter creation from config."""
        config = ScriptsPluginConfig(
            patterns=["scripts/*.sh", "tools/*.py"],
            exclude=["*_test.sh", "internal_*"],
        )

        script_filter = ScriptFilter.from_config(config)

        assert script_filter.matches(Path("scripts/build.sh")) is True
        assert script_filter.matches(Path("scripts/build_test.sh")) is False
        assert script_filter.matches(Path("scripts/internal_cleanup.sh")) is False


# =============================================================================
# TestScriptInfo
# =============================================================================


class TestScriptInfo:
    """Tests for ScriptInfo data model."""

    def test_basic_script_info(self) -> None:
        """Verify basic script info creation."""
        info = ScriptInfo(
            path=Path("scripts/build.sh"),
            interpreter="/bin/bash",
        )

        assert info.path == Path("scripts/build.sh")
        assert info.interpreter == "/bin/bash"
        assert info.description is None

    def test_script_info_with_description(self) -> None:
        """Verify script info with description."""
        info = ScriptInfo(
            path=Path("scripts/build.sh"),
            interpreter="/bin/bash",
            description="Build the project",
        )

        assert info.description == "Build the project"

    def test_to_tool_name(self) -> None:
        """Verify tool name generation."""
        info = ScriptInfo(
            path=Path("scripts/build.sh"),
            interpreter="/bin/bash",
        )

        assert info.to_tool_name() == "script_scripts_build"

    def test_to_tool_definition(self) -> None:
        """Verify tool definition structure."""
        info = ScriptInfo(
            path=Path("scripts/build.sh"),
            interpreter="/bin/bash",
            description="Build the project",
        )

        tool_def = info.to_tool_definition()

        assert tool_def.name == "script_scripts_build"
        assert "Build the project" in tool_def.description
        assert "type" in tool_def.parameters
        assert "args" in tool_def.parameters["properties"]
        assert "timeout" in tool_def.parameters["properties"]
        assert "env" in tool_def.parameters["properties"]

    def test_to_tool_definition_default_description(self) -> None:
        """Verify default description when none provided."""
        info = ScriptInfo(
            path=Path("scripts/deploy.sh"),
            interpreter="/bin/bash",
        )

        tool_def = info.to_tool_definition()

        assert "scripts/deploy.sh" in tool_def.description


# =============================================================================
# TestScriptCache
# =============================================================================


class TestScriptCache:
    """Tests for ScriptCache."""

    def test_cache_hit_on_unchanged(self, tmp_path: Path) -> None:
        """Verify cache hit when scripts are unchanged."""
        script = tmp_path / "build.sh"
        script.write_text("#!/bin/bash\necho hello")

        cache = ScriptCache()
        config = ScriptsPluginConfig(
            patterns=["*.sh"],
            base_directory=str(tmp_path),
        )
        scripts = [ScriptInfo(path=Path("build.sh"), interpreter="/bin/bash")]

        cache.set(str(tmp_path), scripts, config)
        entry = cache.get(str(tmp_path), 300, config)

        assert entry is not None
        assert len(entry.scripts) == 1
        assert entry.scripts[0].path == Path("build.sh")

    def test_cache_miss_on_ttl_expiry(self, tmp_path: Path) -> None:
        """Verify cache miss when TTL expires."""
        script = tmp_path / "build.sh"
        script.write_text("#!/bin/bash\necho hello")

        cache = ScriptCache()
        config = ScriptsPluginConfig(
            patterns=["*.sh"],
            base_directory=str(tmp_path),
        )
        scripts = [ScriptInfo(path=Path("build.sh"), interpreter="/bin/bash")]

        cache.set(str(tmp_path), scripts, config)
        # TTL of 0 should always miss
        entry = cache.get(str(tmp_path), 0, config)

        assert entry is None

    def test_cache_miss_on_config_change(self, tmp_path: Path) -> None:
        """Verify cache miss when configuration changes."""
        script = tmp_path / "build.sh"
        script.write_text("#!/bin/bash\necho hello")

        cache = ScriptCache()
        config1 = ScriptsPluginConfig(
            patterns=["*.sh"],
            base_directory=str(tmp_path),
        )
        config2 = ScriptsPluginConfig(
            patterns=["*.py"],  # Different patterns
            base_directory=str(tmp_path),
        )
        scripts = [ScriptInfo(path=Path("build.sh"), interpreter="/bin/bash")]

        cache.set(str(tmp_path), scripts, config1)
        entry = cache.get(str(tmp_path), 300, config2)

        assert entry is None

    def test_invalidate_specific_entry(self, tmp_path: Path) -> None:
        """Verify invalidating a specific cache entry."""
        script = tmp_path / "build.sh"
        script.write_text("#!/bin/bash\necho hello")

        cache = ScriptCache()
        config = ScriptsPluginConfig(
            patterns=["*.sh"],
            base_directory=str(tmp_path),
        )
        scripts = [ScriptInfo(path=Path("build.sh"), interpreter="/bin/bash")]

        cache.set(str(tmp_path), scripts, config)
        cache.invalidate(str(tmp_path))
        entry = cache.get(str(tmp_path), 300, config)

        assert entry is None

    def test_invalidate_all_entries(self, tmp_path: Path) -> None:
        """Verify invalidating all cache entries."""
        script = tmp_path / "build.sh"
        script.write_text("#!/bin/bash\necho hello")

        cache = ScriptCache()
        config = ScriptsPluginConfig(
            patterns=["*.sh"],
            base_directory=str(tmp_path),
        )
        scripts = [ScriptInfo(path=Path("build.sh"), interpreter="/bin/bash")]

        cache.set(str(tmp_path), scripts, config)
        cache.invalidate()

        assert cache.get(str(tmp_path), 300, config) is None


# =============================================================================
# TestCacheEntry
# =============================================================================


class TestCacheEntry:
    """Tests for CacheEntry."""

    def test_is_valid_within_ttl(self) -> None:
        """Verify entry is valid within TTL."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time(),
            config_hash="abc123",
        )

        assert entry.is_valid(300, "abc123") is True

    def test_is_valid_ttl_expired(self) -> None:
        """Verify entry is invalid when TTL expired."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time() - 400,  # 400 seconds ago
            config_hash="abc123",
        )

        assert entry.is_valid(300, "abc123") is False

    def test_is_valid_config_changed(self) -> None:
        """Verify entry is invalid when config changed."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time(),
            config_hash="abc123",
        )

        assert entry.is_valid(300, "different") is False

    def test_is_valid_ttl_zero_always_false(self) -> None:
        """Verify TTL of 0 always returns False."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time(),
            config_hash="abc123",
        )

        assert entry.is_valid(0, "abc123") is False

    def test_is_valid_with_injectable_time(self) -> None:
        """Verify is_valid() respects injected current_time parameter."""
        base_time = 1000.0
        entry = CacheEntry(
            scripts=[],
            timestamp=base_time,
            config_hash="abc123",
        )

        # Within TTL
        assert entry.is_valid(300, "abc123", current_time=base_time + 299) is True

        # At TTL boundary
        assert entry.is_valid(300, "abc123", current_time=base_time + 300) is False


# =============================================================================
# TestScriptsPlugin
# =============================================================================


class TestScriptsPlugin:
    """Tests for Plugin class lifecycle."""

    @pytest.mark.asyncio
    async def test_initialize_discovers_scripts(self) -> None:
        """Verify initialization discovers scripts."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)

        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        # Should have list_scripts tool and script tools
        assert "script_list_scripts" in tool_names
        assert any("build" in name for name in tool_names)
        assert any("test" in name for name in tool_names)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_get_tools_returns_tool_definitions(self) -> None:
        """Verify get_tools returns proper ToolDefinitions."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()

        for tool in tools:
            assert tool.name
            assert tool.description
            assert "type" in tool.parameters

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Verify unknown tool returns error."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("nonexistent", {})

        assert result.success is False
        assert "Unknown tool" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_call_before_init_returns_error(self) -> None:
        """Verify calling tool before init returns error."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)

        result = await plugin.call_tool("script_simple_build", {})

        assert result.success is False
        assert "not initialized" in result.error.lower()

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self) -> None:
        """Verify shutdown clears plugin state."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()
        await plugin.shutdown()

        assert plugin._scripts == []
        assert plugin._tool_to_script == {}
        assert plugin._initialized is False

    @pytest.mark.asyncio
    async def test_config_reload(self) -> None:
        """Verify configuration reload works."""
        config = {
            "patterns": ["simple/*.sh", "nested/**/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        initial_count = len(plugin._scripts)

        # Reload with filter that excludes some scripts
        new_config = {
            "patterns": ["simple/*.sh"],  # Exclude nested
            "base_directory": str(FIXTURES_DIR),
        }
        await plugin.on_config_reload(new_config)

        # Should have fewer scripts now
        assert len(plugin._scripts) < initial_count

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_expose_list_scripts_false(self) -> None:
        """Verify list_scripts can be disabled."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
            "expose_list_scripts": False,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        assert "script_list_scripts" not in tool_names

        await plugin.shutdown()


# =============================================================================
# TestHealthCheck
# =============================================================================


class TestHealthCheck:
    """Tests for health check methods."""

    @pytest.mark.asyncio
    async def test_health_check_passes_when_healthy(self) -> None:
        """Verify health check returns True when plugin is healthy."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        is_healthy = await plugin.health_check()

        assert is_healthy is True

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_fails_before_init(self) -> None:
        """Verify health check returns False before initialization."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)

        is_healthy = await plugin.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_detailed_health_check_healthy(self) -> None:
        """Verify detailed_health_check returns correct data when healthy."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.detailed_health_check()

        assert result["healthy"] is True
        assert result["initialized"] is True
        assert result["script_count"] > 0
        assert "cache_ttl" in result

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_detailed_health_check_not_initialized(self) -> None:
        """Verify detailed_health_check shows not initialized."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)

        result = await plugin.detailed_health_check()

        assert result["healthy"] is False
        assert result["initialized"] is False


# =============================================================================
# TestScriptExecution
# =============================================================================


class TestScriptExecution:
    """Tests for script execution."""

    @pytest.mark.asyncio
    async def test_list_scripts_tool(self) -> None:
        """Verify list_scripts tool works."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("script_list_scripts", {})

        assert result.success is True
        assert isinstance(result.data, dict)
        assert "scripts" in result.data
        scripts = result.data["scripts"]
        assert any("build" in s["path"] for s in scripts)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_simple_script(self) -> None:
        """Verify simple script execution."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
            "working_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Find the build tool
        tools = plugin.get_tools()
        build_tool = next(
            t for t in tools if "build" in t.name and "list" not in t.name
        )

        result = await plugin.call_tool(build_tool.name, {})

        assert result.success is True
        assert "stdout" in result.data
        assert "exit_code" in result.data
        assert result.data["exit_code"] == 0
        assert "Building" in result.data["stdout"]

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_with_args(self, tmp_path: Path) -> None:
        """Verify script execution with arguments."""
        # Create a script that echoes its arguments
        script = tmp_path / "echo_args.sh"
        script.write_text('#!/bin/bash\necho "Args: $@"')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {
            "patterns": ["*.sh"],
            "base_directory": str(tmp_path),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool(
            "script_echo_args", {"args": ["hello", "world"]}
        )

        assert result.success is True
        assert "hello" in result.data["stdout"]
        assert "world" in result.data["stdout"]

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_with_dangerous_args_rejected(self) -> None:
        """Verify dangerous arguments are rejected."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        build_tool = next(
            t for t in tools if "build" in t.name and "list" not in t.name
        )

        result = await plugin.call_tool(build_tool.name, {"args": ["; rm -rf /"]})

        assert result.success is False
        assert "dangerous" in result.error.lower()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_with_blocked_env_rejected(self) -> None:
        """Verify blocked environment variables are rejected."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        build_tool = next(
            t for t in tools if "build" in t.name and "list" not in t.name
        )

        result = await plugin.call_tool(
            build_tool.name, {"env": {"PATH": "/malicious"}}
        )

        assert result.success is False
        assert "Blocked" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, tmp_path: Path) -> None:
        """Verify timeout is enforced."""
        # Create a slow script
        script = tmp_path / "slow.sh"
        script.write_text("#!/bin/bash\nsleep 10")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {
            "patterns": ["*.sh"],
            "base_directory": str(tmp_path),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("script_slow", {"timeout": 1})

        assert result.success is False
        assert "timed out" in result.error.lower()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_script_with_nonzero_exit(self, tmp_path: Path) -> None:
        """Verify non-zero exit codes are captured."""
        script = tmp_path / "fail.sh"
        script.write_text("#!/bin/bash\nexit 42")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {
            "patterns": ["*.sh"],
            "base_directory": str(tmp_path),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("script_fail", {})

        assert result.success is False
        assert result.data["exit_code"] == 42
        assert "failed" in result.error.lower()

        await plugin.shutdown()


# =============================================================================
# TestDiscovery
# =============================================================================


class TestDiscovery:
    """Tests for plugin discovery interface."""

    def test_discover_finds_scripts(self) -> None:
        """Verify discovery finds scripts in common locations."""
        result = Plugin.discover(FIXTURES_DIR)

        assert result.applicable is True
        assert result.confidence > 0
        assert "patterns" in result.suggested_config
        assert len(result.discovered_items) > 0

    def test_discover_empty_directory(self, tmp_path: Path) -> None:
        """Verify discovery returns not applicable for empty directory."""
        result = Plugin.discover(tmp_path)

        assert result.applicable is False
        assert result.confidence == 0

    def test_discover_generates_tool_names(self) -> None:
        """Verify discovery generates expected tool names."""
        result = Plugin.discover(FIXTURES_DIR)

        assert "script_list_scripts" in result.discovered_items
        # Should contain discovered script tools
        assert any("script_" in item for item in result.discovered_items)

    def test_discover_warns_about_sensitive_scripts(self) -> None:
        """Verify discovery warns about potentially sensitive scripts."""
        result = Plugin.discover(FIXTURES_DIR / "sensitive")

        # Should have warning about sensitive script
        assert any("sensitive" in w.lower() for w in result.warnings)

    def test_discover_warns_about_no_shebang(self) -> None:
        """Verify discovery warns about scripts without shebang."""
        result = Plugin.discover(FIXTURES_DIR / "no_shebang")

        assert any("shebang" in w.lower() for w in result.warnings)


# =============================================================================
# TestCLICommands
# =============================================================================


class TestCLICommands:
    """Tests for CLI commands."""

    def test_get_cli_commands_returns_list(self) -> None:
        """Verify get_cli_commands returns expected commands."""
        commands = Plugin.get_cli_commands()

        assert len(commands) >= 2
        command_names = [c.name for c in commands]
        assert "list" in command_names
        assert "run" in command_names

    def test_get_plugin_metadata(self) -> None:
        """Verify plugin metadata is returned."""
        metadata = Plugin.get_plugin_metadata()

        assert "name" in metadata
        assert "description" in metadata
        assert metadata["name"] == "Scripts"


# =============================================================================
# TestScriptsPluginIntegration
# =============================================================================


class TestScriptsPluginIntegration:
    """Integration tests with fixture files."""

    @pytest.mark.asyncio
    async def test_simple_scripts(self) -> None:
        """Test with simple fixture scripts."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_paths = [s.path for s in plugin._scripts]
        assert Path("simple/build.sh") in script_paths
        assert Path("simple/test.sh") in script_paths

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_nested_scripts(self) -> None:
        """Test with nested directory scripts."""
        config = {
            "patterns": ["nested/**/*.sh", "nested/**/*.py"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_paths = [str(s.path) for s in plugin._scripts]
        assert any("deploy.sh" in p for p in script_paths)
        assert any("db-migrate.py" in p for p in script_paths)

        # Verify tool naming for nested
        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]
        assert any("tools_deploy" in name for name in tool_names)
        assert any("tools_db_migrate" in name for name in tool_names)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_shebang_detection(self) -> None:
        """Test interpreter detection from shebang."""
        config = {
            "patterns": ["with_shebang/*"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_map = {str(s.path): s for s in plugin._scripts}

        # Check Python script has python3 interpreter
        python_script = next(
            (s for k, s in script_map.items() if "python_script.py" in k), None
        )
        assert python_script is not None
        assert "python" in python_script.interpreter

        # Check bash script has bash interpreter
        bash_script = next(
            (s for k, s in script_map.items() if "bash_script.bash" in k), None
        )
        assert bash_script is not None
        assert "bash" in bash_script.interpreter

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_description_extraction(self) -> None:
        """Test description extraction from script comments."""
        config = {
            "patterns": ["with_descriptions/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script = next((s for s in plugin._scripts if "documented" in str(s.path)), None)
        assert script is not None
        assert script.description is not None
        assert "test suite" in script.description.lower()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_exclude_patterns(self) -> None:
        """Test script exclusion patterns."""
        config = {
            "patterns": ["**/*.sh"],
            "exclude": ["**/deploy*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_paths = [str(s.path) for s in plugin._scripts]

        # Should not include deploy scripts
        assert not any("deploy" in p for p in script_paths)

        # Should include other scripts
        assert any("build" in p for p in script_paths)

        await plugin.shutdown()


# =============================================================================
# TestPluginCacheInjection
# =============================================================================


class TestPluginCacheInjection:
    """Tests for ScriptCache injection in Plugin.__init__."""

    @pytest.mark.asyncio
    async def test_plugin_uses_injected_cache(self) -> None:
        """Verify plugin uses injected cache instance."""
        custom_cache = ScriptCache()
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config, cache=custom_cache)

        assert plugin._cache is custom_cache

    @pytest.mark.asyncio
    async def test_plugin_creates_default_cache_when_not_provided(self) -> None:
        """Verify plugin creates its own cache when not injected."""
        config = {
            "patterns": ["simple/*.sh"],
            "base_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)

        assert plugin._cache is not None
        assert isinstance(plugin._cache, ScriptCache)
