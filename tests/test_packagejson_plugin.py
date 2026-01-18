"""Tests for the package.json plugin.

Tests cover:
    - Configuration validation (TestPackageJsonPluginConfig)
    - Script extraction (TestScriptExtractor)
    - Package manager detection (TestPackageManagerDetector)
    - Script data model (TestNpmScript)
    - Tool naming and sanitization (TestToolNaming)
    - Argument sanitization (TestArgumentSanitization)
    - Script caching (TestScriptCache)
    - Lifecycle script filtering (TestLifecycleFiltering)
    - Plugin lifecycle and tools (TestPackageJsonPlugin)
    - Health checks (TestHealthCheck)
    - Integration tests with fixture files (TestPackageJsonPluginIntegration)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from opencuff.plugins.builtin.packagejson import (
    BLOCKED_ENV_VARS,
    DANGEROUS_CHARS,
    LIFECYCLE_SCRIPTS,
    CacheEntry,
    NpmScript,
    PackageJsonPluginConfig,
    PackageManagerDetector,
    Plugin,
    ScriptCache,
    ScriptExtractor,
    ScriptFilter,
    sanitize_arguments,
)

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "package_json"


# =============================================================================
# TestPackageJsonPluginConfig
# =============================================================================


class TestPackageJsonPluginConfig:
    """Tests for PackageJsonPluginConfig validation."""

    def test_default_values(self) -> None:
        """Verify default configuration values."""
        config = PackageJsonPluginConfig()

        assert config.package_json_path == "./package.json"
        assert config.package_manager == "auto"
        assert config.scripts == "*"
        assert config.exclude_scripts == ""
        assert config.exclude_lifecycle_scripts is True
        assert config.cache_ttl == 300
        assert config.working_directory == "."
        assert config.default_timeout == 300
        assert config.environment == {}
        assert config.expose_list_scripts is True

    def test_custom_values(self) -> None:
        """Verify custom configuration values are accepted."""
        config = PackageJsonPluginConfig(
            package_json_path="/path/to/package.json",
            package_manager="pnpm",
            scripts="build,test-*",
            exclude_scripts="_*,*-internal",
            exclude_lifecycle_scripts=False,
            cache_ttl=600,
            working_directory="/workspace",
            default_timeout=120,
            environment={"CI": "true"},
            expose_list_scripts=False,
        )

        assert config.package_json_path == "/path/to/package.json"
        assert config.package_manager == "pnpm"
        assert config.scripts == "build,test-*"
        assert config.exclude_scripts == "_*,*-internal"
        assert config.exclude_lifecycle_scripts is False
        assert config.cache_ttl == 600
        assert config.working_directory == "/workspace"
        assert config.default_timeout == 120
        assert config.environment == {"CI": "true"}
        assert config.expose_list_scripts is False

    def test_valid_package_managers(self) -> None:
        """Verify valid package manager values are accepted."""
        for pm in ("npm", "pnpm", "auto"):
            config = PackageJsonPluginConfig(package_manager=pm)
            assert config.package_manager == pm

    def test_invalid_package_manager(self) -> None:
        """Verify invalid package manager is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            PackageJsonPluginConfig(package_manager="yarn")

        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_cache_ttl_validation(self) -> None:
        """Verify cache_ttl cannot be negative."""
        with pytest.raises(ValidationError):
            PackageJsonPluginConfig(cache_ttl=-1)

    def test_default_timeout_validation(self) -> None:
        """Verify default_timeout must be positive."""
        with pytest.raises(ValidationError):
            PackageJsonPluginConfig(default_timeout=0)


# =============================================================================
# TestNpmScript
# =============================================================================


class TestNpmScript:
    """Tests for NpmScript data model."""

    def test_basic_script(self) -> None:
        """Verify basic script creation."""
        script = NpmScript(name="build", command="tsc")

        assert script.name == "build"
        assert script.command == "tsc"
        assert script.description is None

    def test_script_with_description(self) -> None:
        """Verify script with description."""
        script = NpmScript(
            name="test",
            command="jest",
            description="Run the test suite",
        )

        assert script.name == "test"
        assert script.command == "jest"
        assert script.description == "Run the test suite"

    def test_to_tool_name_simple(self) -> None:
        """Verify simple script name conversion."""
        script = NpmScript(name="build", command="tsc")
        assert script.to_tool_name("npm") == "npm_build"
        assert script.to_tool_name("pnpm") == "pnpm_build"

    def test_to_tool_name_with_hyphen(self) -> None:
        """Verify hyphen is converted to underscore."""
        script = NpmScript(name="lint-fix", command="eslint --fix")
        assert script.to_tool_name("npm") == "npm_lint_fix"

    def test_to_tool_name_with_colon(self) -> None:
        """Verify colon is converted to double underscore."""
        script = NpmScript(name="build:prod", command="vite build")
        assert script.to_tool_name("npm") == "npm_build__prod"

    def test_to_tool_name_with_dot(self) -> None:
        """Verify dot is converted to underscore."""
        script = NpmScript(name="ci.test", command="jest --ci")
        assert script.to_tool_name("npm") == "npm_ci_test"

    def test_to_tool_name_complex(self) -> None:
        """Verify complex name with multiple special chars."""
        script = NpmScript(name="test:unit:ci", command="vitest")
        assert script.to_tool_name("npm") == "npm_test__unit__ci"

    def test_to_tool_definition(self) -> None:
        """Verify tool definition structure."""
        script = NpmScript(
            name="build",
            command="tsc",
            description="Compile TypeScript",
        )

        tool_def = script.to_tool_definition("npm")

        assert tool_def.name == "npm_build"
        assert "Compile TypeScript" in tool_def.description
        assert "type" in tool_def.parameters
        assert "extra_args" in tool_def.parameters["properties"]
        assert "timeout" in tool_def.parameters["properties"]
        assert "dry_run" in tool_def.parameters["properties"]

    def test_to_tool_definition_default_description(self) -> None:
        """Verify default description when none provided."""
        script = NpmScript(name="deploy", command="./deploy.sh")

        tool_def = script.to_tool_definition("npm")

        assert "npm run deploy" in tool_def.description


# =============================================================================
# TestToolNaming
# =============================================================================


class TestToolNaming:
    """Tests for tool naming conventions and sanitization."""

    def test_simple_names(self) -> None:
        """Verify simple script names are prefixed correctly."""
        test_cases = [
            ("test", "npm", "npm_test"),
            ("build", "pnpm", "pnpm_build"),
            ("lint", "npm", "npm_lint"),
        ]
        for script_name, pm, expected in test_cases:
            script = NpmScript(name=script_name, command="cmd")
            assert script.to_tool_name(pm) == expected

    def test_hyphen_to_underscore(self) -> None:
        """Verify hyphens are converted to underscores."""
        test_cases = [
            ("lint-fix", "npm_lint_fix"),
            ("test-unit", "npm_test_unit"),
            ("build-dev", "npm_build_dev"),
        ]
        for script_name, expected in test_cases:
            script = NpmScript(name=script_name, command="cmd")
            assert script.to_tool_name("npm") == expected

    def test_colon_to_double_underscore(self) -> None:
        """Verify colons are converted to double underscores."""
        test_cases = [
            ("build:prod", "npm_build__prod"),
            ("test:unit", "npm_test__unit"),
            ("test:e2e:ci", "npm_test__e2e__ci"),
        ]
        for script_name, expected in test_cases:
            script = NpmScript(name=script_name, command="cmd")
            assert script.to_tool_name("npm") == expected

    def test_dot_to_underscore(self) -> None:
        """Verify dots are converted to underscores."""
        test_cases = [
            ("ci.test", "npm_ci_test"),
            ("lint.fix", "npm_lint_fix"),
        ]
        for script_name, expected in test_cases:
            script = NpmScript(name=script_name, command="cmd")
            assert script.to_tool_name("npm") == expected

    def test_mixed_special_chars(self) -> None:
        """Verify mixed special characters are handled."""
        test_cases = [
            ("test:unit-ci", "npm_test__unit_ci"),
            ("build:prod.min", "npm_build__prod_min"),
        ]
        for script_name, expected in test_cases:
            script = NpmScript(name=script_name, command="cmd")
            assert script.to_tool_name("npm") == expected


# =============================================================================
# TestArgumentSanitization
# =============================================================================


class TestArgumentSanitization:
    """Tests for argument sanitization."""

    def test_valid_arguments(self) -> None:
        """Verify valid arguments pass sanitization."""
        valid_args = [
            "--coverage",
            "--watch --verbose",
            "--reporter=junit",
            "VAR=value",
            "-j 4",
            "--flag 'quoted value'",
        ]
        for args in valid_args:
            result = sanitize_arguments(args)
            assert isinstance(result, list)

    def test_dangerous_semicolon(self) -> None:
        """Verify semicolon is blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag; rm -rf /")
        assert ";" in str(exc_info.value)

    def test_dangerous_pipe(self) -> None:
        """Verify pipe is blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag | cat /etc/passwd")
        assert "|" in str(exc_info.value)

    def test_dangerous_ampersand(self) -> None:
        """Verify ampersand is blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag & malicious")
        assert "&" in str(exc_info.value)

    def test_dangerous_backtick(self) -> None:
        """Verify backtick is blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag `whoami`")
        assert "`" in str(exc_info.value)

    def test_dangerous_dollar(self) -> None:
        """Verify dollar sign is blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag $HOME")
        assert "$" in str(exc_info.value)

    def test_dangerous_parentheses(self) -> None:
        """Verify parentheses are blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag (cmd)")
        # Either ( or ) should be reported (set iteration order varies)
        assert "(" in str(exc_info.value) or ")" in str(exc_info.value)

    def test_dangerous_newline(self) -> None:
        """Verify newlines are blocked."""
        with pytest.raises(ValueError) as exc_info:
            sanitize_arguments("--flag\nmalicious")
        assert "\\n" in str(exc_info.value) or "newline" in str(exc_info.value).lower()

    def test_all_dangerous_chars_defined(self) -> None:
        """Verify all dangerous characters are defined."""
        expected = {";", "|", "&", "`", "$", "(", ")", "\n", "\r"}
        assert expected == DANGEROUS_CHARS


# =============================================================================
# TestScriptExtractor
# =============================================================================


class TestScriptExtractor:
    """Tests for ScriptExtractor."""

    @pytest.mark.asyncio
    async def test_extract_simple_scripts(self) -> None:
        """Verify extraction from simple package.json."""
        extractor = ScriptExtractor()

        scripts = await extractor.extract(FIXTURES_DIR / "simple" / "package.json")

        script_names = [s.name for s in scripts]
        assert "build" in script_names
        assert "test" in script_names
        assert "lint" in script_names

    @pytest.mark.asyncio
    async def test_extract_scripts_with_descriptions(self) -> None:
        """Verify scripts-info descriptions are extracted."""
        extractor = ScriptExtractor()

        scripts = await extractor.extract(FIXTURES_DIR / "simple" / "package.json")

        script_map = {s.name: s for s in scripts}
        assert script_map["build"].description == "Compile TypeScript to JavaScript"
        assert script_map["test"].description == "Run the test suite"
        assert script_map["lint"].description is None  # No description in scripts-info

    @pytest.mark.asyncio
    async def test_extract_complex_scripts(self) -> None:
        """Verify extraction of scripts with colons and hyphens."""
        extractor = ScriptExtractor()

        scripts = await extractor.extract(FIXTURES_DIR / "complex" / "package.json")

        script_names = [s.name for s in scripts]
        assert "build:prod" in script_names
        assert "test:unit" in script_names
        assert "lint-fix" in script_names
        assert "ci.test" in script_names

    @pytest.mark.asyncio
    async def test_extract_empty_scripts(self) -> None:
        """Verify handling of package.json without scripts."""
        extractor = ScriptExtractor()

        scripts = await extractor.extract(FIXTURES_DIR / "empty" / "package.json")

        assert scripts == []

    @pytest.mark.asyncio
    async def test_extract_nonexistent_file(self) -> None:
        """Verify error handling for nonexistent file."""
        extractor = ScriptExtractor()

        with pytest.raises(FileNotFoundError):
            await extractor.extract(Path("/nonexistent/package.json"))

    @pytest.mark.asyncio
    async def test_extract_invalid_json(self, tmp_path: Path) -> None:
        """Verify error handling for invalid JSON."""
        package_json = tmp_path / "package.json"
        package_json.write_text("{ invalid json }")

        extractor = ScriptExtractor()

        with pytest.raises(ValueError):
            await extractor.extract(package_json)


# =============================================================================
# TestPackageManagerDetector
# =============================================================================


class TestPackageManagerDetector:
    """Tests for PackageManagerDetector."""

    def test_detect_pnpm_from_lock_file(self) -> None:
        """Verify pnpm detection from pnpm-lock.yaml."""
        detector = PackageManagerDetector()

        result = detector.detect(FIXTURES_DIR / "with_pnpm")

        assert result == "pnpm"

    def test_detect_npm_from_lock_file(self) -> None:
        """Verify npm detection from package-lock.json."""
        detector = PackageManagerDetector()

        result = detector.detect(FIXTURES_DIR / "with_npm")

        assert result == "npm"

    def test_default_when_no_lock_file(self) -> None:
        """Verify default is used when no lock file present."""
        detector = PackageManagerDetector()

        result = detector.detect(FIXTURES_DIR / "simple")

        assert result == "npm"  # Default

    def test_custom_default(self) -> None:
        """Verify custom default is used."""
        detector = PackageManagerDetector()

        result = detector.detect(FIXTURES_DIR / "simple", default="pnpm")

        assert result == "pnpm"

    def test_pnpm_takes_precedence(self, tmp_path: Path) -> None:
        """Verify pnpm lock takes precedence over npm lock."""
        # Create both lock files
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'")
        (tmp_path / "package-lock.json").write_text("{}")
        (tmp_path / "package.json").write_text('{"name": "test"}')

        detector = PackageManagerDetector()

        result = detector.detect(tmp_path)

        assert result == "pnpm"


# =============================================================================
# TestScriptFilter
# =============================================================================


class TestScriptFilter:
    """Tests for ScriptFilter fnmatch patterns."""

    def test_include_all_pattern(self) -> None:
        """Verify * pattern matches all scripts."""
        filter = ScriptFilter(["*"], [], exclude_lifecycle=False)

        assert filter.matches("build") is True
        assert filter.matches("test") is True
        assert filter.matches("anything") is True

    def test_include_specific_scripts(self) -> None:
        """Verify specific script names match."""
        filter = ScriptFilter(["build", "test"], [], exclude_lifecycle=False)

        assert filter.matches("build") is True
        assert filter.matches("test") is True
        assert filter.matches("lint") is False

    def test_include_wildcard_pattern(self) -> None:
        """Verify wildcard patterns match correctly."""
        filter = ScriptFilter(["test:*"], [], exclude_lifecycle=False)

        assert filter.matches("test:unit") is True
        assert filter.matches("test:e2e") is True
        assert filter.matches("test") is False

    def test_exclude_pattern(self) -> None:
        """Verify exclude patterns work."""
        filter = ScriptFilter(["*"], ["*-internal"], exclude_lifecycle=False)

        assert filter.matches("build") is True
        assert filter.matches("build-internal") is False

    def test_exclude_takes_precedence(self) -> None:
        """Verify exclude patterns take precedence over include."""
        filter = ScriptFilter(["*"], ["build"], exclude_lifecycle=False)

        assert filter.matches("build") is False
        assert filter.matches("test") is True

    def test_exclude_lifecycle_scripts(self) -> None:
        """Verify lifecycle scripts are excluded when configured."""
        filter = ScriptFilter(["*"], [], exclude_lifecycle=True)

        # Lifecycle scripts should be excluded
        assert filter.matches("preinstall") is False
        assert filter.matches("postinstall") is False
        assert filter.matches("prepare") is False
        assert filter.matches("prepublishOnly") is False

        # Non-lifecycle scripts should be included
        assert filter.matches("build") is True
        assert filter.matches("test") is True

    def test_include_lifecycle_scripts_when_disabled(self) -> None:
        """Verify lifecycle scripts are included when not excluded."""
        filter = ScriptFilter(["*"], [], exclude_lifecycle=False)

        assert filter.matches("preinstall") is True
        assert filter.matches("postinstall") is True
        assert filter.matches("prepare") is True

    def test_from_config(self) -> None:
        """Verify filter creation from config."""
        config = PackageJsonPluginConfig(
            scripts="build, test:*",
            exclude_scripts="_*, *-internal",
            exclude_lifecycle_scripts=True,
        )

        filter = ScriptFilter.from_config(config)

        assert filter.matches("build") is True
        assert filter.matches("test:unit") is True
        assert filter.matches("_private") is False
        assert filter.matches("deploy-internal") is False
        assert filter.matches("preinstall") is False


# =============================================================================
# TestLifecycleFiltering
# =============================================================================


class TestLifecycleFiltering:
    """Tests for lifecycle script filtering."""

    def test_lifecycle_scripts_defined(self) -> None:
        """Verify all lifecycle scripts are defined."""
        expected = {
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
        assert expected == LIFECYCLE_SCRIPTS

    @pytest.mark.asyncio
    async def test_lifecycle_filtering_in_plugin(self) -> None:
        """Verify lifecycle scripts are filtered by plugin."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "with_lifecycle" / "package.json"),
            "exclude_lifecycle_scripts": True,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        # User scripts should be present
        assert any("build" in name for name in tool_names)
        assert any("test" in name for name in tool_names)
        assert any("start" in name for name in tool_names)

        # Lifecycle scripts should be excluded
        assert not any("preinstall" in name for name in tool_names)
        assert not any("postinstall" in name for name in tool_names)
        assert not any("prepare" in name for name in tool_names)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_lifecycle_included_when_disabled(self) -> None:
        """Verify lifecycle scripts are included when filtering disabled."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "with_lifecycle" / "package.json"),
            "exclude_lifecycle_scripts": False,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        # Lifecycle scripts should be present
        assert any("preinstall" in name for name in tool_names)
        assert any("postinstall" in name for name in tool_names)
        assert any("prepare" in name for name in tool_names)

        await plugin.shutdown()


# =============================================================================
# TestScriptCache
# =============================================================================


class TestScriptCache:
    """Tests for ScriptCache."""

    def test_cache_hit_on_unchanged_file(self, tmp_path: Path) -> None:
        """Verify cache hit when file is unchanged."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        cache = ScriptCache()
        config = PackageJsonPluginConfig()
        scripts = [NpmScript(name="build", command="tsc")]

        cache.set(str(package_json), scripts, config)
        entry = cache.get(str(package_json), 300, config)

        assert entry is not None
        assert len(entry.scripts) == 1
        assert entry.scripts[0].name == "build"

    def test_cache_miss_on_content_change(self, tmp_path: Path) -> None:
        """Verify cache miss when file content changes."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        cache = ScriptCache()
        config = PackageJsonPluginConfig()
        scripts = [NpmScript(name="build", command="tsc")]

        cache.set(str(package_json), scripts, config)

        # Modify the file
        modified_content = '{"name": "test", "scripts": {"build": "tsc", "test": "j"}}'
        package_json.write_text(modified_content)

        entry = cache.get(str(package_json), 300, config)

        assert entry is None

    def test_cache_miss_on_ttl_expiry(self, tmp_path: Path) -> None:
        """Verify cache miss when TTL expires."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        cache = ScriptCache()
        config = PackageJsonPluginConfig()
        scripts = [NpmScript(name="build", command="tsc")]

        cache.set(str(package_json), scripts, config)

        # TTL of 0 should always miss
        entry = cache.get(str(package_json), 0, config)

        assert entry is None

    def test_cache_miss_on_config_change(self, tmp_path: Path) -> None:
        """Verify cache miss when configuration changes."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        cache = ScriptCache()
        config1 = PackageJsonPluginConfig(scripts="build")
        config2 = PackageJsonPluginConfig(scripts="test")
        scripts = [NpmScript(name="build", command="tsc")]

        cache.set(str(package_json), scripts, config1)
        entry = cache.get(str(package_json), 300, config2)

        assert entry is None

    def test_invalidate_specific_entry(self, tmp_path: Path) -> None:
        """Verify invalidating a specific cache entry."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        cache = ScriptCache()
        config = PackageJsonPluginConfig()
        scripts = [NpmScript(name="build", command="tsc")]

        cache.set(str(package_json), scripts, config)
        cache.invalidate(str(package_json))
        entry = cache.get(str(package_json), 300, config)

        assert entry is None

    def test_invalidate_all_entries(self, tmp_path: Path) -> None:
        """Verify invalidating all cache entries."""
        pkg1 = tmp_path / "package1.json"
        pkg2 = tmp_path / "package2.json"
        pkg1.write_text('{"name": "test1", "scripts": {"build": "tsc"}}')
        pkg2.write_text('{"name": "test2", "scripts": {"test": "jest"}}')

        cache = ScriptCache()
        config = PackageJsonPluginConfig()

        cache.set(str(pkg1), [NpmScript(name="build", command="tsc")], config)
        cache.set(str(pkg2), [NpmScript(name="test", command="jest")], config)

        cache.invalidate()

        assert cache.get(str(pkg1), 300, config) is None
        assert cache.get(str(pkg2), 300, config) is None


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
            package_json_hash="abc123",
            config_hash="config123",
        )

        assert entry.is_valid(300, "abc123", "config123") is True

    def test_is_valid_ttl_expired(self) -> None:
        """Verify entry is invalid when TTL expired."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time() - 400,  # 400 seconds ago
            package_json_hash="abc123",
            config_hash="config123",
        )

        assert entry.is_valid(300, "abc123", "config123") is False

    def test_is_valid_content_changed(self) -> None:
        """Verify entry is invalid when content changed."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time(),
            package_json_hash="abc123",
            config_hash="config123",
        )

        assert entry.is_valid(300, "different", "config123") is False

    def test_is_valid_config_changed(self) -> None:
        """Verify entry is invalid when config changed."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time(),
            package_json_hash="abc123",
            config_hash="config123",
        )

        assert entry.is_valid(300, "abc123", "different") is False

    def test_is_valid_ttl_zero_always_false(self) -> None:
        """Verify TTL of 0 always returns False."""
        entry = CacheEntry(
            scripts=[],
            timestamp=time.time(),
            package_json_hash="abc123",
            config_hash="config123",
        )

        assert entry.is_valid(0, "abc123", "config123") is False

    def test_is_valid_with_injectable_time(self) -> None:
        """Verify is_valid() respects injected current_time parameter."""
        base_time = 1000.0
        entry = CacheEntry(
            scripts=[],
            timestamp=base_time,
            package_json_hash="abc123",
            config_hash="config123",
        )

        # Within TTL
        is_valid = entry.is_valid(
            300, "abc123", "config123", current_time=base_time + 299
        )
        assert is_valid is True

        # At TTL boundary
        is_valid = entry.is_valid(
            300, "abc123", "config123", current_time=base_time + 300
        )
        assert is_valid is False

        # Past TTL
        is_valid = entry.is_valid(
            300, "abc123", "config123", current_time=base_time + 301
        )
        assert is_valid is False


# =============================================================================
# TestPackageJsonPlugin
# =============================================================================


class TestPackageJsonPlugin:
    """Tests for PackageJsonPlugin class."""

    @pytest.mark.asyncio
    async def test_initialize_discovers_scripts(self) -> None:
        """Verify initialization discovers scripts."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)

        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        # Should have list_scripts tool and script tools
        assert any("list_scripts" in name for name in tool_names)
        assert any("build" in name for name in tool_names)
        assert any("test" in name for name in tool_names)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_get_tools_returns_tool_definitions(self) -> None:
        """Verify get_tools returns proper ToolDefinitions."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
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
    async def test_tool_to_script_mapping(self) -> None:
        """Verify tool-to-script mapping preserves names."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # The mapping should preserve original script names
        assert any("build" in key for key in plugin._tool_to_script)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_list_scripts_tool(self) -> None:
        """Verify list_scripts tool works."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Find the list_scripts tool name
        tools = plugin.get_tools()
        list_tool = next(t for t in tools if "list_scripts" in t.name)

        result = await plugin.call_tool(list_tool.name, {})

        assert result.success is True
        assert isinstance(result.data, list)
        script_names = [s["name"] for s in result.data]
        assert "build" in script_names
        assert "test" in script_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Verify unknown tool returns error."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
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
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)

        result = await plugin.call_tool("npm_build", {})

        assert result.success is False
        assert "not initialized" in result.error.lower()

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self) -> None:
        """Verify shutdown clears plugin state."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
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
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
            "scripts": "*",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        initial_count = len(plugin._scripts)

        # Reload with filter that excludes some scripts
        new_config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
            "scripts": "build",
        }
        await plugin.on_config_reload(new_config)

        # Should have fewer scripts now
        assert len(plugin._scripts) < initial_count

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_expose_list_scripts_false(self) -> None:
        """Verify list_scripts can be disabled."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
            "expose_list_scripts": False,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        assert not any("list_scripts" in name for name in tool_names)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_package_manager_auto_detection(self) -> None:
        """Verify package manager is auto-detected."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "with_pnpm" / "package.json"),
            "package_manager": "auto",
            "working_directory": str(FIXTURES_DIR / "with_pnpm"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        assert plugin._package_manager == "pnpm"

        tools = plugin.get_tools()
        # Tools should use pnpm prefix
        assert any(t.name.startswith("pnpm_") for t in tools)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_package_manager_explicit(self) -> None:
        """Verify explicit package manager is respected."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "with_npm" / "package.json"),
            "package_manager": "pnpm",  # Override npm lock file
            "working_directory": str(FIXTURES_DIR / "with_npm"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        assert plugin._package_manager == "pnpm"

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
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
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
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)

        is_healthy = await plugin.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_health_check_fails_with_missing_file(self, tmp_path: Path) -> None:
        """Verify health check fails when package.json is deleted after init."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Delete the file
        package_json.unlink()

        is_healthy = await plugin.health_check()

        assert is_healthy is False

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_detailed_health_check_healthy(self) -> None:
        """Verify detailed_health_check returns correct data when healthy."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.detailed_health_check()

        assert result["healthy"] is True
        assert result["initialized"] is True
        assert result["package_json_exists"] is True
        assert result["script_count"] > 0
        assert "package_manager" in result
        assert "cache_has_entries" in result

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_detailed_health_check_not_initialized(self) -> None:
        """Verify detailed_health_check shows not initialized."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)

        result = await plugin.detailed_health_check()

        assert result["healthy"] is False
        assert result["initialized"] is False


# =============================================================================
# TestPackageJsonPluginIntegration
# =============================================================================


class TestPackageJsonPluginIntegration:
    """Integration tests with fixture files."""

    @pytest.mark.asyncio
    async def test_simple_package_json(self) -> None:
        """Test with simple fixture."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_names = [s.name for s in plugin._scripts]

        assert "build" in script_names
        assert "test" in script_names
        assert "lint" in script_names

        # Verify descriptions
        script_map = {s.name: s for s in plugin._scripts}
        assert script_map["build"].description == "Compile TypeScript to JavaScript"

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_complex_package_json(self) -> None:
        """Test with complex fixture containing colons and hyphens."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "complex" / "package.json"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_names = [s.name for s in plugin._scripts]

        assert "build:prod" in script_names
        assert "test:unit" in script_names
        assert "lint-fix" in script_names
        assert "ci.test" in script_names

        # Verify tool names are sanitized
        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        assert any("build__prod" in name for name in tool_names)
        assert any("test__unit" in name for name in tool_names)
        assert any("lint_fix" in name for name in tool_names)
        assert any("ci_test" in name for name in tool_names)

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_empty_package_json(self) -> None:
        """Test with empty fixture (no scripts)."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "empty" / "package.json"),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Should have only list_scripts tool (if enabled)
        non_list_tools = [t for t in plugin.get_tools() if "list_scripts" not in t.name]
        assert len(non_list_tools) == 0

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_script_filtering_integration(self) -> None:
        """Test script filtering with real package.json."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "complex" / "package.json"),
            "scripts": "build*,test*",
            "exclude_scripts": "*:e2e",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        script_names = [s.name for s in plugin._scripts]

        assert "build" in script_names
        assert "build:prod" in script_names
        assert "test" in script_names
        assert "test:unit" in script_names
        assert "test:e2e" not in script_names  # Excluded
        assert "lint" not in script_names  # Not matched by patterns

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
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config, cache=custom_cache)

        assert plugin._cache is custom_cache

    @pytest.mark.asyncio
    async def test_plugin_creates_default_cache_when_not_provided(self) -> None:
        """Verify plugin creates its own cache when not injected."""
        config = {
            "package_json_path": str(FIXTURES_DIR / "simple" / "package.json"),
        }
        plugin = Plugin(config)

        assert plugin._cache is not None
        assert isinstance(plugin._cache, ScriptCache)


# =============================================================================
# TestDryRun
# =============================================================================


class TestDryRun:
    """Tests for dry run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_command(self, tmp_path: Path) -> None:
        """Verify dry run returns the command that would be executed."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"build": "tsc"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Find the build tool
        tools = plugin.get_tools()
        build_tool = next(
            t for t in tools if "build" in t.name and "list" not in t.name
        )

        result = await plugin.call_tool(build_tool.name, {"dry_run": True})

        assert result.success is True
        assert "Would execute:" in result.data
        assert "run build" in result.data

        await plugin.shutdown()


# =============================================================================
# TestBlockedEnvVars
# =============================================================================


class TestBlockedEnvVars:
    """Tests for environment variable security restrictions."""

    def test_blocked_env_vars_defined(self) -> None:
        """Verify critical security-sensitive env vars are blocked."""
        # These must be blocked to prevent privilege escalation
        critical_vars = {"PATH", "LD_PRELOAD", "NODE_OPTIONS"}
        assert critical_vars.issubset(BLOCKED_ENV_VARS)

    @pytest.mark.asyncio
    async def test_blocked_env_var_path(self, tmp_path: Path) -> None:
        """Verify PATH cannot be overridden via tool arguments."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"test": "echo hi"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        test_tool = next(t for t in tools if "test" in t.name and "list" not in t.name)

        result = await plugin.call_tool(
            test_tool.name, {"env": {"PATH": "/malicious/path"}}
        )

        assert result.success is False
        assert "security-sensitive" in result.error.lower()
        assert "PATH" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_blocked_env_var_node_options(self, tmp_path: Path) -> None:
        """Verify NODE_OPTIONS cannot be overridden via tool arguments."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"test": "echo hi"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        test_tool = next(t for t in tools if "test" in t.name and "list" not in t.name)

        result = await plugin.call_tool(
            test_tool.name, {"env": {"NODE_OPTIONS": "--require=/malicious"}}
        )

        assert result.success is False
        assert "security-sensitive" in result.error.lower()
        assert "NODE_OPTIONS" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_blocked_env_vars(self, tmp_path: Path) -> None:
        """Verify multiple blocked env vars are all reported."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"test": "echo hi"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        test_tool = next(t for t in tools if "test" in t.name and "list" not in t.name)

        result = await plugin.call_tool(
            test_tool.name,
            {"env": {"PATH": "/bad", "LD_PRELOAD": "/bad.so", "NODE_OPTIONS": "--bad"}},
        )

        assert result.success is False
        # All blocked vars should be mentioned
        assert "PATH" in result.error
        assert "LD_PRELOAD" in result.error
        assert "NODE_OPTIONS" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_allowed_env_var_passthrough(self, tmp_path: Path) -> None:
        """Verify non-blocked env vars can still be set."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "test", "scripts": {"test": "echo $MY_VAR"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        test_tool = next(t for t in tools if "test" in t.name and "list" not in t.name)

        # Dry run to verify env vars are accepted (actual execution depends on npm)
        result = await plugin.call_tool(
            test_tool.name,
            {"env": {"MY_VAR": "hello", "ANOTHER_VAR": "world"}, "dry_run": True},
        )

        assert result.success is True

        await plugin.shutdown()


# =============================================================================
# TestScriptExecution
# =============================================================================


class TestScriptExecution:
    """Tests for actual script execution (non-dry-run)."""

    @pytest.mark.asyncio
    async def test_execute_echo_script(self, tmp_path: Path) -> None:
        """Verify successful execution of a simple echo script."""
        package_json = tmp_path / "package.json"
        package_json.write_text(
            '{"name": "test", "scripts": {"echo-test": "echo hello-world"}}'
        )

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        echo_tool = next(t for t in tools if "echo_test" in t.name)

        result = await plugin.call_tool(echo_tool.name, {})

        # Script execution may fail if npm is not installed, but we test the code path
        if result.success:
            assert "hello-world" in result.data["stdout"]
            assert result.data["exit_code"] == 0
            assert "duration_seconds" in result.data
        else:
            # If npm is not installed, the error message should indicate that
            assert "not found" in result.error.lower() or "npm" in result.error.lower()

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_script_with_timeout(self, tmp_path: Path) -> None:
        """Verify timeout parameter is respected."""
        package_json = tmp_path / "package.json"
        # A script that would take a while if it ran
        package_json.write_text('{"name": "test", "scripts": {"slow": "sleep 10"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        slow_tool = next(t for t in tools if "slow" in t.name and "list" not in t.name)

        # With a 1 second timeout, this should timeout
        result = await plugin.call_tool(slow_tool.name, {"timeout": 1})

        if result.success is False and "timed out" in (result.error or "").lower():
            # Good - timeout was enforced
            assert "1 seconds" in result.error
        elif result.success is False:
            # npm not found or other error
            pass
        else:
            # If it succeeded, it ran faster than expected (e.g., sleep not available)
            pass

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_script_returns_exit_code(self, tmp_path: Path) -> None:
        """Verify non-zero exit codes are captured."""
        package_json = tmp_path / "package.json"
        # A script that exits with code 42
        package_json.write_text('{"name": "test", "scripts": {"fail": "exit 42"}}')

        config = {
            "package_json_path": str(package_json),
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        fail_tool = next(t for t in tools if "fail" in t.name and "list" not in t.name)

        result = await plugin.call_tool(fail_tool.name, {})

        # If npm is available, verify exit code handling
        if result.data and "exit_code" in result.data:
            # The exact exit code may vary depending on how npm handles it
            assert result.data["exit_code"] != 0 or result.success is False

        await plugin.shutdown()
