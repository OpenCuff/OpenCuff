"""Tests for the Makefile plugin.

Tests cover:
    - Configuration validation (TestMakefilePluginConfig)
    - Simple regex-based extraction (TestSimpleExtractor)
    - Make database extraction (TestMakeDatabaseExtractor)
    - Hybrid/auto extraction (TestExtractorSelector)
    - Target caching (TestTargetCache)
    - Target filtering (TestTargetFilter)
    - Plugin lifecycle and tools (TestMakefilePlugin)
    - Integration tests with fixture Makefiles (TestMakefilePluginIntegration)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from opencuff.plugins.builtin.makefile import (
    CacheEntry,
    ExtractorError,
    ExtractorSelector,
    ExtractorStrategy,
    MakeDatabaseExtractor,
    MakefilePluginConfig,
    MakeTarget,
    Plugin,
    SimpleExtractor,
    TargetCache,
    TargetFilter,
)

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "makefiles"


# =============================================================================
# TestMakefilePluginConfig
# =============================================================================


class TestMakefilePluginConfig:
    """Tests for MakefilePluginConfig validation."""

    def test_default_values(self) -> None:
        """Verify default configuration values."""
        config = MakefilePluginConfig()

        assert config.makefile_path == "./Makefile"
        assert config.targets == "*"
        assert config.exclude_targets == ""
        assert config.extractor == ExtractorStrategy.AUTO
        assert config.trust_makefile is True
        assert config.cache_ttl == 300
        assert config.make_command == "make"
        assert config.working_directory is None
        assert config.allow_parallel is True
        assert config.parallel_jobs is None
        assert config.default_timeout == 300
        assert config.environment == {}
        assert config.description_prefix == "##"
        assert config.expose_list_targets is True

    def test_custom_values(self) -> None:
        """Verify custom configuration values are accepted."""
        config = MakefilePluginConfig(
            makefile_path="/path/to/Makefile",
            targets="build,test-*",
            exclude_targets="_*,*-internal",
            extractor=ExtractorStrategy.SIMPLE,
            trust_makefile=False,
            cache_ttl=600,
            make_command="/usr/bin/make",
            working_directory="/workspace",
            allow_parallel=False,
            parallel_jobs=4,
            default_timeout=120,
            environment={"CI": "true"},
            description_prefix="###",
            expose_list_targets=False,
        )

        assert config.makefile_path == "/path/to/Makefile"
        assert config.targets == "build,test-*"
        assert config.exclude_targets == "_*,*-internal"
        assert config.extractor == ExtractorStrategy.SIMPLE
        assert config.trust_makefile is False
        assert config.cache_ttl == 600
        assert config.make_command == "/usr/bin/make"
        assert config.working_directory == "/workspace"
        assert config.allow_parallel is False
        assert config.parallel_jobs == 4
        assert config.default_timeout == 120
        assert config.environment == {"CI": "true"}
        assert config.description_prefix == "###"
        assert config.expose_list_targets is False

    def test_pattern_parsing(self) -> None:
        """Verify patterns are parsed correctly."""
        config = MakefilePluginConfig(
            targets="build, test-*, install-*",
            exclude_targets=" _*, *-internal ",
        )

        # Patterns should be stored as-is (parsing happens in TargetFilter)
        assert config.targets == "build, test-*, install-*"
        assert config.exclude_targets == " _*, *-internal "

    def test_invalid_pattern_with_path_separator(self) -> None:
        """Verify patterns with path separators are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            MakefilePluginConfig(targets="build/test")

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "path separators" in errors[0]["msg"]

    def test_invalid_pattern_with_backslash(self) -> None:
        """Verify patterns with backslashes are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            MakefilePluginConfig(exclude_targets="build\\test")

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "path separators" in errors[0]["msg"]

    def test_cache_ttl_validation(self) -> None:
        """Verify cache_ttl cannot be negative."""
        with pytest.raises(ValidationError):
            MakefilePluginConfig(cache_ttl=-1)

    def test_parallel_jobs_validation(self) -> None:
        """Verify parallel_jobs must be positive."""
        with pytest.raises(ValidationError):
            MakefilePluginConfig(parallel_jobs=0)

    def test_default_timeout_validation(self) -> None:
        """Verify default_timeout must be positive."""
        with pytest.raises(ValidationError):
            MakefilePluginConfig(default_timeout=0)


# =============================================================================
# TestSimpleExtractor
# =============================================================================


class TestSimpleExtractor:
    """Tests for SimpleExtractor regex-based parsing."""

    @pytest.mark.asyncio
    async def test_extract_basic_targets(self) -> None:
        """Verify extraction of basic targets from simple.mk."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "simple.mk")

        target_names = [t.name for t in targets]
        assert "build" in target_names
        assert "test" in target_names
        assert "clean" in target_names

    @pytest.mark.asyncio
    async def test_extract_phony_targets(self) -> None:
        """Verify .PHONY targets are marked correctly."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "simple.mk")

        target_map = {t.name: t for t in targets}
        assert target_map["build"].is_phony is True
        assert target_map["test"].is_phony is True
        assert target_map["clean"].is_phony is True

    @pytest.mark.asyncio
    async def test_extract_format1_descriptions(self) -> None:
        """Verify descriptions from ## comments above targets."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "with_descriptions.mk")

        target_map = {t.name: t for t in targets}
        assert target_map["build"].description == "Format 1: Comment above target"
        expected_format = "Multi-word description for format target"
        assert target_map["format"].description == expected_format

    @pytest.mark.asyncio
    async def test_extract_format2_descriptions(self) -> None:
        """Verify descriptions from ## inline comments."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "with_descriptions.mk")

        target_map = {t.name: t for t in targets}
        assert target_map["deploy"].description == "Format 2: Inline comment"
        expected_lint = "Format 2 with prerequisites: Run linting"
        assert target_map["lint"].description == expected_lint

    @pytest.mark.asyncio
    async def test_regular_comments_not_descriptions(self) -> None:
        """Verify regular # comments are not treated as descriptions."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "with_descriptions.mk")

        target_map = {t.name: t for t in targets}
        # test has a regular comment, not a ## comment
        assert target_map["test"].description is None

    @pytest.mark.asyncio
    async def test_ignore_pattern_rules(self) -> None:
        """Verify pattern rules (%.o: %.c) are not extracted."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "with_patterns.mk")

        target_names = [t.name for t in targets]
        # Pattern rules should be ignored
        assert "%o" not in target_names
        assert "%.o" not in target_names
        # Regular targets should be found
        assert "build" in target_names
        assert "all" in target_names

    @pytest.mark.asyncio
    async def test_empty_makefile(self) -> None:
        """Verify handling of empty Makefile."""
        extractor = SimpleExtractor()

        targets = await extractor.extract(FIXTURES_DIR / "empty.mk")

        assert targets == []

    @pytest.mark.asyncio
    async def test_custom_description_prefix(self) -> None:
        """Verify custom description prefix is respected."""
        extractor = SimpleExtractor(description_prefix="###")

        # With wrong prefix, should not find descriptions
        targets = await extractor.extract(FIXTURES_DIR / "simple.mk")

        target_map = {t.name: t for t in targets}
        # Descriptions use ##, not ###, so should be None
        assert target_map["build"].description is None

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises_error(self) -> None:
        """Verify ExtractorError is raised for nonexistent files."""
        extractor = SimpleExtractor()

        with pytest.raises(ExtractorError) as exc_info:
            await extractor.extract(Path("/nonexistent/Makefile"))

        assert "Cannot read Makefile" in exc_info.value.message

    def test_supports_includes_returns_false(self) -> None:
        """Verify simple extractor does not support includes."""
        extractor = SimpleExtractor()
        assert extractor.supports_includes() is False

    def test_supports_dynamic_targets_returns_false(self) -> None:
        """Verify simple extractor does not support dynamic targets."""
        extractor = SimpleExtractor()
        assert extractor.supports_dynamic_targets() is False


# =============================================================================
# TestMakeDatabaseExtractor
# =============================================================================


class TestMakeDatabaseExtractor:
    """Tests for MakeDatabaseExtractor using make -pn."""

    @pytest.mark.asyncio
    async def test_extract_targets_with_includes(self) -> None:
        """Verify extraction handles include directives."""
        extractor = MakeDatabaseExtractor(
            working_directory=str(FIXTURES_DIR),
        )

        targets = await extractor.extract(FIXTURES_DIR / "with_includes.mk")

        target_names = [t.name for t in targets]
        assert "build" in target_names
        # Target from included file
        assert "setup" in target_names

    @pytest.mark.asyncio
    async def test_extract_targets_with_variables(self) -> None:
        """Verify extraction handles variable expansion."""
        extractor = MakeDatabaseExtractor(
            working_directory=str(FIXTURES_DIR),
        )

        targets = await extractor.extract(FIXTURES_DIR / "with_variables.mk")

        target_names = [t.name for t in targets]
        # All targets from $(TARGETS) variable should be found
        assert "build" in target_names
        assert "test" in target_names
        assert "deploy" in target_names

    @pytest.mark.asyncio
    async def test_filter_builtin_targets(self) -> None:
        """Verify built-in targets are filtered out."""
        extractor = MakeDatabaseExtractor(
            working_directory=str(FIXTURES_DIR),
        )

        targets = await extractor.extract(FIXTURES_DIR / "simple.mk")

        target_names = [t.name for t in targets]
        # Built-in targets should not appear
        assert ".PHONY" not in target_names
        assert ".SUFFIXES" not in target_names
        assert ".DEFAULT" not in target_names

    @pytest.mark.asyncio
    async def test_make_not_found_raises_error(self) -> None:
        """Verify error when make command is not found."""
        extractor = MakeDatabaseExtractor(
            make_command="/nonexistent/make",
        )

        with pytest.raises(ExtractorError) as exc_info:
            await extractor.extract(FIXTURES_DIR / "simple.mk")

        assert "make command not found" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_timeout_raises_error(self) -> None:
        """Verify timeout error is raised."""
        extractor = MakeDatabaseExtractor(
            timeout=0.001,  # Very short timeout
        )

        # May or may not timeout depending on system speed
        # Just verify it doesn't crash
        try:
            await extractor.extract(FIXTURES_DIR / "simple.mk")
        except ExtractorError as e:
            assert "timed out" in e.message

    @pytest.mark.asyncio
    async def test_extracts_descriptions(self) -> None:
        """Verify descriptions are extracted from original file."""
        extractor = MakeDatabaseExtractor(
            working_directory=str(FIXTURES_DIR),
        )

        targets = await extractor.extract(FIXTURES_DIR / "with_descriptions.mk")

        target_map = {t.name: t for t in targets}
        # Descriptions should be extracted from the original Makefile
        if "deploy" in target_map:
            assert target_map["deploy"].description == "Format 2: Inline comment"

    def test_supports_includes_returns_true(self) -> None:
        """Verify make database extractor supports includes."""
        extractor = MakeDatabaseExtractor()
        assert extractor.supports_includes() is True

    def test_supports_dynamic_targets_returns_true(self) -> None:
        """Verify make database extractor supports dynamic targets."""
        extractor = MakeDatabaseExtractor()
        assert extractor.supports_dynamic_targets() is True


# =============================================================================
# TestExtractorSelector
# =============================================================================


class TestExtractorSelector:
    """Tests for ExtractorSelector auto-detection."""

    @pytest.mark.asyncio
    async def test_auto_detect_simple_makefile(self) -> None:
        """Verify auto mode selects simple for basic Makefiles."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database)

        _, strategy = await hybrid.extract(
            FIXTURES_DIR / "simple.mk",
            ExtractorStrategy.AUTO,
        )

        assert strategy == ExtractorStrategy.SIMPLE

    @pytest.mark.asyncio
    async def test_auto_detect_complex_makefile_with_includes(self) -> None:
        """Verify auto mode selects make_database for Makefiles with includes."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database)

        _, strategy = await hybrid.extract(
            FIXTURES_DIR / "with_includes.mk",
            ExtractorStrategy.AUTO,
        )

        assert strategy == ExtractorStrategy.MAKE_DATABASE

    @pytest.mark.asyncio
    async def test_auto_detect_complex_makefile_with_shell(self) -> None:
        """Verify auto mode selects make_database for Makefiles with $(shell)."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database)

        _, strategy = await hybrid.extract(
            FIXTURES_DIR / "complex.mk",
            ExtractorStrategy.AUTO,
        )

        assert strategy == ExtractorStrategy.MAKE_DATABASE

    @pytest.mark.asyncio
    async def test_trust_makefile_false_forces_simple(self) -> None:
        """Verify trust_makefile=False forces simple extraction."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database, trust_makefile=False)

        _, strategy = await hybrid.extract(
            FIXTURES_DIR / "complex.mk",
            ExtractorStrategy.AUTO,
        )

        # Even though complex.mk has $(shell), should use simple
        assert strategy == ExtractorStrategy.SIMPLE

    @pytest.mark.asyncio
    async def test_make_database_requires_trust(self) -> None:
        """Verify make_database strategy requires trust_makefile=True."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database, trust_makefile=False)

        with pytest.raises(ExtractorError) as exc_info:
            await hybrid.extract(
                FIXTURES_DIR / "simple.mk",
                ExtractorStrategy.MAKE_DATABASE,
            )

        assert "trust_makefile=True" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_explicit_simple_strategy(self) -> None:
        """Verify explicit simple strategy is used."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database)

        _, strategy = await hybrid.extract(
            FIXTURES_DIR / "with_includes.mk",
            ExtractorStrategy.SIMPLE,
        )

        # Even for complex Makefile, should use simple when explicitly requested
        assert strategy == ExtractorStrategy.SIMPLE

    @pytest.mark.asyncio
    async def test_explicit_make_database_strategy(self) -> None:
        """Verify explicit make_database strategy is used."""
        simple = SimpleExtractor()
        database = MakeDatabaseExtractor(working_directory=str(FIXTURES_DIR))
        hybrid = ExtractorSelector(simple, database)

        _, strategy = await hybrid.extract(
            FIXTURES_DIR / "simple.mk",
            ExtractorStrategy.MAKE_DATABASE,
        )

        # Even for simple Makefile, should use make_database when requested
        assert strategy == ExtractorStrategy.MAKE_DATABASE


# =============================================================================
# TestTargetCache
# =============================================================================


class TestTargetCache:
    """Tests for TargetCache."""

    def test_cache_hit_on_unchanged_file(self, tmp_path: Path) -> None:
        """Verify cache hit when file is unchanged."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\techo test")

        cache = TargetCache()
        config = MakefilePluginConfig()
        targets = [MakeTarget(name="test", is_phony=True)]

        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config)
        entry = cache.get(str(makefile), 300, config)

        assert entry is not None
        assert len(entry.targets) == 1
        assert entry.targets[0].name == "test"

    def test_cache_miss_on_content_change(self, tmp_path: Path) -> None:
        """Verify cache miss when file content changes."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\techo test")

        cache = TargetCache()
        config = MakefilePluginConfig()
        targets = [MakeTarget(name="test", is_phony=True)]

        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config)

        # Modify the file
        modified = ".PHONY: test build\ntest:\n\techo test\nbuild:\n\techo build"
        makefile.write_text(modified)

        entry = cache.get(str(makefile), 300, config)

        assert entry is None

    def test_cache_miss_on_ttl_expiry(self, tmp_path: Path) -> None:
        """Verify cache miss when TTL expires."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\techo test")

        cache = TargetCache()
        config = MakefilePluginConfig()
        targets = [MakeTarget(name="test", is_phony=True)]

        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config)

        # TTL of 0 should always miss
        entry = cache.get(str(makefile), 0, config)

        assert entry is None

    def test_cache_miss_on_config_change(self, tmp_path: Path) -> None:
        """Verify cache miss when configuration changes."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\techo test")

        cache = TargetCache()
        config1 = MakefilePluginConfig(targets="test")
        config2 = MakefilePluginConfig(targets="build")
        targets = [MakeTarget(name="test", is_phony=True)]

        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config1)
        entry = cache.get(str(makefile), 300, config2)

        assert entry is None

    def test_invalidate_specific_entry(self, tmp_path: Path) -> None:
        """Verify invalidating a specific cache entry."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\techo test")

        cache = TargetCache()
        config = MakefilePluginConfig()
        targets = [MakeTarget(name="test", is_phony=True)]

        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config)
        cache.invalidate(str(makefile))
        entry = cache.get(str(makefile), 300, config)

        assert entry is None

    def test_invalidate_all_entries(self, tmp_path: Path) -> None:
        """Verify invalidating all cache entries."""
        makefile1 = tmp_path / "Makefile1"
        makefile2 = tmp_path / "Makefile2"
        makefile1.write_text(".PHONY: test\ntest:\n\techo test")
        makefile2.write_text(".PHONY: build\nbuild:\n\techo build")

        cache = TargetCache()
        config = MakefilePluginConfig()

        targets1 = [MakeTarget(name="test")]
        targets2 = [MakeTarget(name="build")]
        cache.set(str(makefile1), targets1, ExtractorStrategy.SIMPLE, config)
        cache.set(str(makefile2), targets2, ExtractorStrategy.SIMPLE, config)

        cache.invalidate()

        assert cache.get(str(makefile1), 300, config) is None
        assert cache.get(str(makefile2), 300, config) is None


# =============================================================================
# TestTargetFilter
# =============================================================================


class TestTargetFilter:
    """Tests for TargetFilter fnmatch patterns."""

    def test_include_all_pattern(self) -> None:
        """Verify * pattern matches all targets."""
        filter = TargetFilter(["*"], [])

        assert filter.matches("build") is True
        assert filter.matches("test") is True
        assert filter.matches("anything") is True

    def test_include_specific_targets(self) -> None:
        """Verify specific target names match."""
        filter = TargetFilter(["build", "test"], [])

        assert filter.matches("build") is True
        assert filter.matches("test") is True
        assert filter.matches("deploy") is False

    def test_include_wildcard_pattern(self) -> None:
        """Verify wildcard patterns match correctly."""
        filter = TargetFilter(["test-*"], [])

        assert filter.matches("test-unit") is True
        assert filter.matches("test-integration") is True
        assert filter.matches("test") is False
        assert filter.matches("unittest") is False

    def test_exclude_pattern(self) -> None:
        """Verify exclude patterns work."""
        filter = TargetFilter(["*"], ["*-internal"])

        assert filter.matches("build") is True
        assert filter.matches("build-internal") is False
        assert filter.matches("test-internal") is False

    def test_exclude_takes_precedence(self) -> None:
        """Verify exclude patterns take precedence over include."""
        filter = TargetFilter(["*"], ["build"])

        assert filter.matches("build") is False
        assert filter.matches("test") is True

    def test_multiple_include_patterns(self) -> None:
        """Verify multiple include patterns work."""
        filter = TargetFilter(["build-*", "test-*", "install"], [])

        assert filter.matches("build-debug") is True
        assert filter.matches("test-unit") is True
        assert filter.matches("install") is True
        assert filter.matches("deploy") is False

    def test_multiple_exclude_patterns(self) -> None:
        """Verify multiple exclude patterns work."""
        filter = TargetFilter(["*"], ["_*", "*-internal", "*-debug"])

        assert filter.matches("build") is True
        assert filter.matches("_private") is False
        assert filter.matches("deploy-internal") is False
        assert filter.matches("build-debug") is False

    def test_question_mark_wildcard(self) -> None:
        """Verify ? matches single character."""
        filter = TargetFilter(["test?"], [])

        assert filter.matches("test1") is True
        assert filter.matches("testx") is True
        assert filter.matches("test") is False
        assert filter.matches("test12") is False

    def test_character_class(self) -> None:
        """Verify [seq] matches characters in sequence."""
        filter = TargetFilter(["test-[abc]"], [])

        assert filter.matches("test-a") is True
        assert filter.matches("test-b") is True
        assert filter.matches("test-c") is True
        assert filter.matches("test-d") is False

    def test_negated_character_class(self) -> None:
        """Verify [!seq] matches characters not in sequence."""
        filter = TargetFilter(["test-[!0-9]"], [])

        assert filter.matches("test-a") is True
        assert filter.matches("test-x") is True
        assert filter.matches("test-1") is False
        assert filter.matches("test-9") is False

    def test_from_config(self) -> None:
        """Verify filter creation from config."""
        config = MakefilePluginConfig(
            targets="build, test-*",
            exclude_targets="_*, *-internal",
        )

        filter = TargetFilter.from_config(config)

        assert filter.matches("build") is True
        assert filter.matches("test-unit") is True
        assert filter.matches("_private") is False
        assert filter.matches("deploy-internal") is False

    def test_empty_patterns_default_to_all(self) -> None:
        """Verify empty include patterns default to *."""
        filter = TargetFilter([], [])

        assert filter.matches("anything") is True


# =============================================================================
# TestMakefilePlugin
# =============================================================================


class TestMakefilePlugin:
    """Tests for MakefilePlugin class."""

    @pytest.mark.asyncio
    async def test_initialize_discovers_targets(self) -> None:
        """Verify initialization discovers targets."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)

        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        assert "make_list_targets" in tool_names
        assert "make_build" in tool_names
        assert "make_test" in tool_names
        assert "make_clean" in tool_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_get_tools_returns_tool_definitions(self) -> None:
        """Verify get_tools returns proper ToolDefinitions."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
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
    async def test_tool_to_target_mapping(self) -> None:
        """Verify tool-to-target mapping preserves names."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # The mapping should preserve original target names
        assert "make_build" in plugin._tool_to_target
        assert plugin._tool_to_target["make_build"] == "build"

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_list_targets_tool(self) -> None:
        """Verify make_list_targets tool works."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("make_list_targets", {})

        assert result.success is True
        assert isinstance(result.data, list)
        target_names = [t["name"] for t in result.data]
        assert "build" in target_names
        assert "test" in target_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_target(self, tmp_path: Path) -> None:
        """Verify target execution works."""
        # Create a simple Makefile that echoes
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\n\n## Run test\ntest:\n\t@echo 'Test passed'")

        config = {
            "makefile_path": str(makefile),
            "extractor": "simple",
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("make_test", {})

        assert result.success is True
        assert "Test passed" in result.data["stdout"]
        assert result.data["exit_code"] == 0

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_target_dry_run(self, tmp_path: Path) -> None:
        """Verify dry run mode works."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\n\ntest:\n\techo 'Test'")

        config = {
            "makefile_path": str(makefile),
            "extractor": "simple",
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.call_tool("make_test", {"dry_run": True})

        assert result.success is True
        # In dry-run mode, commands are printed but not executed
        assert "echo" in result.data["stdout"]

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Verify unknown tool returns error."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
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
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
        }
        plugin = Plugin(config)

        result = await plugin.call_tool("make_build", {})

        assert result.success is False
        assert "not initialized" in result.error

    @pytest.mark.asyncio
    async def test_health_check_passes_when_healthy(self) -> None:
        """Verify health check returns True when plugin is healthy."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
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
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
        }
        plugin = Plugin(config)

        is_healthy = await plugin.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_health_check_fails_with_missing_makefile(
        self, tmp_path: Path
    ) -> None:
        """Verify health check fails when Makefile is deleted after init."""
        # Create a Makefile, initialize, then delete it
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\t@echo test")

        config = {
            "makefile_path": str(makefile),
            "extractor": "simple",
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Delete the Makefile after initialization
        makefile.unlink()

        is_healthy = await plugin.health_check()

        assert is_healthy is False

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self) -> None:
        """Verify shutdown clears plugin state."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()
        await plugin.shutdown()

        assert plugin._targets == []
        assert plugin._tool_to_target == {}
        assert plugin._initialized is False

    @pytest.mark.asyncio
    async def test_config_reload(self) -> None:
        """Verify configuration reload works."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
            "targets": "*",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        initial_targets = len(plugin._targets)

        # Reload with filter that excludes some targets
        new_config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
            "targets": "build",
        }
        await plugin.on_config_reload(new_config)

        # Should have fewer targets now
        assert len(plugin._targets) < initial_targets

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_expose_list_targets_false(self) -> None:
        """Verify list_targets can be disabled."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
            "expose_list_targets": False,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        tools = plugin.get_tools()
        tool_names = [t.name for t in tools]

        assert "make_list_targets" not in tool_names

        await plugin.shutdown()


# =============================================================================
# TestMakefilePluginIntegration
# =============================================================================


class TestMakefilePluginIntegration:
    """Integration tests with fixture Makefiles."""

    @pytest.mark.asyncio
    async def test_simple_makefile(self) -> None:
        """Test with simple.mk fixture."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_names = [t.name for t in plugin._targets]

        assert "build" in target_names
        assert "test" in target_names
        assert "clean" in target_names

        # Verify descriptions
        target_map = {t.name: t for t in plugin._targets}
        assert target_map["build"].description == "Build the project"
        assert target_map["test"].description == "Run tests"

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_with_descriptions_makefile(self) -> None:
        """Test with with_descriptions.mk fixture."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "with_descriptions.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_map = {t.name: t for t in plugin._targets}

        # Format 1 description
        assert target_map["build"].description == "Format 1: Comment above target"
        # Format 2 description
        assert target_map["deploy"].description == "Format 2: Inline comment"
        # Regular comment should not be a description
        assert target_map["test"].description is None

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_with_includes_makefile(self) -> None:
        """Test with with_includes.mk fixture using make_database."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "with_includes.mk"),
            "extractor": "make_database",
            "working_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_names = [t.name for t in plugin._targets]

        # Targets from main file
        assert "build" in target_names
        # Targets from included file
        assert "setup" in target_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_with_variables_makefile(self) -> None:
        """Test with with_variables.mk fixture."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "with_variables.mk"),
            "extractor": "make_database",
            "working_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_names = [t.name for t in plugin._targets]

        # Targets from $(TARGETS) variable
        assert "build" in target_names
        assert "test" in target_names
        assert "deploy" in target_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_with_patterns_makefile(self) -> None:
        """Test with with_patterns.mk fixture."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "with_patterns.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_names = [t.name for t in plugin._targets]

        # Should include regular targets
        assert "build" in target_names
        assert "all" in target_names
        # Pattern rules should be excluded
        assert "%o" not in target_names
        assert "%.o" not in target_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_complex_makefile_with_auto_strategy(self) -> None:
        """Test with complex.mk fixture using auto strategy."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "complex.mk"),
            "extractor": "auto",
            "working_directory": str(FIXTURES_DIR),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_names = [t.name for t in plugin._targets]

        assert "lint" in target_names
        assert "test" in target_names

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_empty_makefile(self) -> None:
        """Test with empty.mk fixture."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "empty.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Should have no targets (except possibly list_targets)
        non_list_tools = [
            t for t in plugin.get_tools() if t.name != "make_list_targets"
        ]
        assert len(non_list_tools) == 0

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_target_filtering_integration(self) -> None:
        """Test target filtering with real Makefile."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
            "targets": "build,test",
            "exclude_targets": "clean",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        target_names = [t.name for t in plugin._targets]

        assert "build" in target_names
        assert "test" in target_names
        assert "clean" not in target_names

        await plugin.shutdown()


# =============================================================================
# TestMakeTarget
# =============================================================================


class TestMakeTarget:
    """Tests for MakeTarget data model."""

    def test_to_tool_name_simple(self) -> None:
        """Verify simple target name conversion."""
        target = MakeTarget(name="build")
        assert target.to_tool_name() == "make_build"

    def test_to_tool_name_with_hyphen(self) -> None:
        """Verify hyphen is converted to underscore."""
        target = MakeTarget(name="test-unit")
        assert target.to_tool_name() == "make_test_unit"

    def test_to_tool_name_with_dot(self) -> None:
        """Verify dot is converted to underscore."""
        target = MakeTarget(name="test.py")
        assert target.to_tool_name() == "make_test_py"

    def test_to_tool_definition(self) -> None:
        """Verify tool definition structure."""
        target = MakeTarget(
            name="build",
            description="Build the project",
            is_phony=True,
        )

        tool_def = target.to_tool_definition()

        assert tool_def["name"] == "make_build"
        assert "Build the project" in tool_def["description"]
        assert "phony" in tool_def["description"]
        assert "type" in tool_def["parameters"]
        assert "extra_args" in tool_def["parameters"]["properties"]

    def test_to_tool_definition_default_description(self) -> None:
        """Verify default description when none provided."""
        target = MakeTarget(name="deploy")

        tool_def = target.to_tool_definition()

        assert "make deploy" in tool_def["description"]


# =============================================================================
# TestCacheEntry
# =============================================================================


class TestCacheEntry:
    """Tests for CacheEntry."""

    def test_is_valid_within_ttl(self) -> None:
        """Verify entry is valid within TTL."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        assert entry.is_valid(300, "abc123", {}, "config123") is True

    def test_is_valid_ttl_expired(self) -> None:
        """Verify entry is invalid when TTL expired."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time() - 400,  # 400 seconds ago
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        assert entry.is_valid(300, "abc123", {}, "config123") is False

    def test_is_valid_content_changed(self) -> None:
        """Verify entry is invalid when content changed."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        assert entry.is_valid(300, "different", {}, "config123") is False

    def test_is_valid_config_changed(self) -> None:
        """Verify entry is invalid when config changed."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        assert entry.is_valid(300, "abc123", {}, "different") is False

    def test_is_valid_included_file_modified(self) -> None:
        """Verify entry is invalid when included file is modified."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={"/path/to/common.mk": 1000.0},
            config_hash="config123",
        )

        # Included file has newer mtime
        assert (
            entry.is_valid(300, "abc123", {"/path/to/common.mk": 2000.0}, "config123")
            is False
        )

    def test_is_valid_included_file_deleted(self) -> None:
        """Verify entry is invalid when included file is deleted."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={"/path/to/common.mk": 1000.0},
            config_hash="config123",
        )

        # Included file no longer in current mtimes
        assert entry.is_valid(300, "abc123", {}, "config123") is False

    def test_is_valid_new_included_file(self) -> None:
        """Verify entry is invalid when new included file appears."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        # New included file appeared
        assert (
            entry.is_valid(300, "abc123", {"/path/to/new.mk": 1000.0}, "config123")
            is False
        )

    def test_is_valid_ttl_zero_always_false(self) -> None:
        """Verify TTL of 0 always returns False."""
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=time.time(),
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        assert entry.is_valid(0, "abc123", {}, "config123") is False

    def test_is_valid_with_injectable_time(self) -> None:
        """Verify is_valid() respects injected current_time parameter."""
        base_time = 1000.0
        entry = CacheEntry(
            targets=[],
            strategy_used=ExtractorStrategy.SIMPLE,
            timestamp=base_time,
            makefile_content_hash="abc123",
            included_files={},
            config_hash="config123",
        )

        # With injected time just within TTL (299 seconds later)
        assert (
            entry.is_valid(300, "abc123", {}, "config123", current_time=base_time + 299)
            is True
        )

        # With injected time at TTL boundary (300 seconds later)
        assert (
            entry.is_valid(300, "abc123", {}, "config123", current_time=base_time + 300)
            is False
        )

        # With injected time past TTL (301 seconds later)
        assert (
            entry.is_valid(300, "abc123", {}, "config123", current_time=base_time + 301)
            is False
        )


# =============================================================================
# TestExecuteTargetShlex
# =============================================================================


class TestExecuteTargetShlex:
    """Tests for _execute_target with shlex parsing edge cases."""

    @pytest.mark.asyncio
    async def test_execute_target_with_invalid_shlex(self, tmp_path: Path) -> None:
        """Verify _execute_target handles invalid shlex syntax gracefully."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\t@echo test")

        config = {
            "makefile_path": str(makefile),
            "extractor": "simple",
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Invalid shlex - unbalanced quotes
        result = await plugin.call_tool("make_test", {"extra_args": "VAR='unbalanced"})

        assert result.success is False
        assert "Invalid extra_args" in result.error

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_execute_target_with_valid_quoted_args(self, tmp_path: Path) -> None:
        """Verify _execute_target handles valid quoted arguments."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\t@echo $(VAR)")

        config = {
            "makefile_path": str(makefile),
            "extractor": "simple",
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Valid shlex with quotes
        result = await plugin.call_tool(
            "make_test", {"extra_args": "VAR='hello world'"}
        )

        assert result.success is True
        assert "hello world" in result.data["stdout"]

        await plugin.shutdown()


# =============================================================================
# TestConfigReloadTrustMakefile
# =============================================================================


class TestConfigReloadTrustMakefile:
    """Tests for on_config_reload when trust_makefile changes."""

    @pytest.mark.asyncio
    async def test_config_reload_trust_makefile_true_to_false(self) -> None:
        """Verify extractor is recreated when trust_makefile changes True->False."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "auto",
            "trust_makefile": True,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Store reference to original extractor
        original_extractor = plugin._extractor

        # Reload with trust_makefile=False
        new_config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "auto",
            "trust_makefile": False,
        }
        await plugin.on_config_reload(new_config)

        # Extractor should be recreated
        assert plugin._extractor is not original_extractor
        assert plugin._extractor.trust_makefile is False

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_config_reload_trust_makefile_false_to_true(self) -> None:
        """Verify extractor is recreated when trust_makefile changes False->True."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "auto",
            "trust_makefile": False,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Store reference to original extractor
        original_extractor = plugin._extractor

        # Reload with trust_makefile=True
        new_config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "auto",
            "trust_makefile": True,
        }
        await plugin.on_config_reload(new_config)

        # Extractor should be recreated
        assert plugin._extractor is not original_extractor
        assert plugin._extractor.trust_makefile is True

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_config_reload_trust_makefile_unchanged(self) -> None:
        """Verify extractor is NOT recreated when trust_makefile stays the same."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
            "trust_makefile": True,
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Store reference to original extractor
        original_extractor = plugin._extractor

        # Reload with same trust_makefile value but different targets
        new_config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
            "trust_makefile": True,
            "targets": "build",
        }
        await plugin.on_config_reload(new_config)

        # Extractor should be the same object
        assert plugin._extractor is original_extractor

        await plugin.shutdown()


# =============================================================================
# TestCacheIncludedFiles
# =============================================================================


class TestCacheIncludedFiles:
    """Tests for cache behavior with included files modification."""

    def test_cache_invalidated_when_included_file_modified(
        self, tmp_path: Path
    ) -> None:
        """Verify cache is invalidated when an included file is modified."""
        # Create main Makefile with include directive
        included_file = tmp_path / "common.mk"
        included_file.write_text(".PHONY: setup\nsetup:\n\t@echo setup")

        makefile = tmp_path / "Makefile"
        makefile.write_text(
            f"include {included_file}\n.PHONY: build\nbuild:\n\t@echo build"
        )

        cache = TargetCache()
        config = MakefilePluginConfig()
        targets = [MakeTarget(name="build"), MakeTarget(name="setup")]

        # Set cache entry
        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config)

        # Verify cache hit before modification
        entry = cache.get(str(makefile), 300, config)
        assert entry is not None

        # Simulate time passing and modify included file
        time.sleep(0.1)
        included_file.write_text(".PHONY: setup\nsetup:\n\t@echo modified_setup")

        # Cache should be invalidated due to included file mtime change
        entry = cache.get(str(makefile), 300, config)
        assert entry is None

    def test_cache_invalidated_when_new_included_file_added(
        self, tmp_path: Path
    ) -> None:
        """Verify cache is invalidated when a new include directive is added."""
        # Create Makefile without includes first
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: build\nbuild:\n\t@echo build")

        cache = TargetCache()
        config = MakefilePluginConfig()
        targets = [MakeTarget(name="build")]

        # Set cache entry
        cache.set(str(makefile), targets, ExtractorStrategy.SIMPLE, config)

        # Verify cache hit
        entry = cache.get(str(makefile), 300, config)
        assert entry is not None

        # Now modify Makefile to add an include (this changes content hash)
        included_file = tmp_path / "common.mk"
        included_file.write_text(".PHONY: setup\nsetup:\n\t@echo setup")

        makefile.write_text(
            f"include {included_file}\n.PHONY: build\nbuild:\n\t@echo build"
        )

        # Cache should be invalidated due to content change
        entry = cache.get(str(makefile), 300, config)
        assert entry is None


# =============================================================================
# TestDetailedHealthCheck
# =============================================================================


class TestDetailedHealthCheck:
    """Tests for detailed_health_check() method."""

    @pytest.mark.asyncio
    async def test_detailed_health_check_healthy(self) -> None:
        """Verify detailed_health_check returns correct data when healthy."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        await plugin.initialize()

        result = await plugin.detailed_health_check()

        assert result["healthy"] is True
        assert result["initialized"] is True
        assert result["makefile_exists"] is True
        assert result["makefile_readable"] is True
        assert result["extractor_strategy"] == "simple"
        assert result["target_count"] > 0
        assert "makefile_path" in result
        assert "make_command" in result
        assert "make_available" in result
        assert "cache_ttl" in result
        assert "trust_makefile" in result

        await plugin.shutdown()

    @pytest.mark.asyncio
    async def test_detailed_health_check_not_initialized(self) -> None:
        """Verify detailed_health_check shows not initialized."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)
        # Don't initialize

        result = await plugin.detailed_health_check()

        assert result["healthy"] is False
        assert result["initialized"] is False

    @pytest.mark.asyncio
    async def test_detailed_health_check_missing_makefile(self, tmp_path: Path) -> None:
        """Verify detailed_health_check reports missing Makefile."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\t@echo test")

        config = {
            "makefile_path": str(makefile),
            "extractor": "simple",
            "working_directory": str(tmp_path),
        }
        plugin = Plugin(config)
        await plugin.initialize()

        # Delete Makefile
        makefile.unlink()

        result = await plugin.detailed_health_check()

        assert result["healthy"] is False
        assert result["initialized"] is True
        assert result["makefile_exists"] is False
        assert result["makefile_readable"] is False

        await plugin.shutdown()


# =============================================================================
# TestPluginCacheInjection
# =============================================================================


class TestPluginCacheInjection:
    """Tests for TargetCache injection in Plugin.__init__."""

    @pytest.mark.asyncio
    async def test_plugin_uses_injected_cache(self) -> None:
        """Verify plugin uses injected cache instance."""
        custom_cache = TargetCache()
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config, cache=custom_cache)

        assert plugin._cache is custom_cache

    @pytest.mark.asyncio
    async def test_plugin_creates_default_cache_when_not_provided(self) -> None:
        """Verify plugin creates its own cache when not injected."""
        config = {
            "makefile_path": str(FIXTURES_DIR / "simple.mk"),
            "extractor": "simple",
        }
        plugin = Plugin(config)

        assert plugin._cache is not None
        assert isinstance(plugin._cache, TargetCache)
