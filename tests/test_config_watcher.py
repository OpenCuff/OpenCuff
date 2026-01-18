"""Tests for the configuration file watcher.

Tests cover:
    - File watching initialization
    - Polling fallback behavior
    - Hash computation
    - Change detection
    - Callback invocation
    - Stop/start lifecycle
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from opencuff.plugins.config import OpenCuffSettings
from opencuff.plugins.watcher import WATCHFILES_AVAILABLE, ConfigWatcher


class TestConfigWatcherInitialization:
    """Tests for ConfigWatcher initialization."""

    def test_init_with_string_path(self) -> None:
        """Verify initialization accepts string path."""
        callback = AsyncMock()

        watcher = ConfigWatcher(
            settings_path="/tmp/settings.yml",
            on_change=callback,
        )

        assert watcher.settings_path == Path("/tmp/settings.yml")
        assert watcher.on_change is callback
        assert watcher.poll_interval == 5.0

    def test_init_with_path_object(self) -> None:
        """Verify initialization accepts Path object."""
        callback = AsyncMock()
        path = Path("/tmp/settings.yml")

        watcher = ConfigWatcher(
            settings_path=path,
            on_change=callback,
        )

        assert watcher.settings_path == path

    def test_init_with_custom_poll_interval(self) -> None:
        """Verify custom poll interval is accepted."""
        callback = AsyncMock()

        watcher = ConfigWatcher(
            settings_path="/tmp/settings.yml",
            on_change=callback,
            poll_interval=10.0,
        )

        assert watcher.poll_interval == 10.0

    def test_initial_state_is_not_running(self) -> None:
        """Verify watcher is not running initially."""
        callback = AsyncMock()

        watcher = ConfigWatcher(
            settings_path="/tmp/settings.yml",
            on_change=callback,
        )

        assert watcher.is_running is False


class TestConfigWatcherHashComputation:
    """Tests for hash computation functionality."""

    def test_compute_hash_returns_sha256(self) -> None:
        """Verify hash computation returns SHA256 hex digest."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("test content")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
            )

            file_hash = watcher._compute_hash()

            # SHA256 produces 64 character hex string
            assert len(file_hash) == 64
            assert all(c in "0123456789abcdef" for c in file_hash)
        finally:
            Path(settings_path).unlink()

    def test_compute_hash_returns_empty_for_missing_file(self) -> None:
        """Verify hash returns empty string for non-existent file."""
        watcher = ConfigWatcher(
            settings_path="/nonexistent/path/settings.yml",
            on_change=AsyncMock(),
        )

        file_hash = watcher._compute_hash()

        assert file_hash == ""

    def test_compute_hash_changes_with_content(self) -> None:
        """Verify hash changes when file content changes."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("initial content")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
            )

            hash1 = watcher._compute_hash()

            # Modify file content
            Path(settings_path).write_text("modified content")

            hash2 = watcher._compute_hash()

            assert hash1 != hash2
        finally:
            Path(settings_path).unlink()


class TestConfigWatcherLifecycle:
    """Tests for watcher start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running_state(self) -> None:
        """Verify start() sets running state to True."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\n")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
                poll_interval=1.0,
            )

            await watcher.start()

            assert watcher.is_running is True

            await watcher.stop()
        finally:
            Path(settings_path).unlink()

    @pytest.mark.asyncio
    async def test_stop_clears_running_state(self) -> None:
        """Verify stop() clears running state."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\n")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
                poll_interval=1.0,
            )
            await watcher.start()

            await watcher.stop()

            assert watcher.is_running is False
        finally:
            Path(settings_path).unlink()

    @pytest.mark.asyncio
    async def test_start_when_already_running_logs_warning(self) -> None:
        """Verify starting an already running watcher is safe."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\n")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
                poll_interval=1.0,
            )
            await watcher.start()

            # Start again should not raise
            await watcher.start()

            assert watcher.is_running is True

            await watcher.stop()
        finally:
            Path(settings_path).unlink()

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_safe(self) -> None:
        """Verify stopping a non-running watcher is safe."""
        watcher = ConfigWatcher(
            settings_path="/tmp/settings.yml",
            on_change=AsyncMock(),
        )

        # Should not raise
        await watcher.stop()

        assert watcher.is_running is False


class TestConfigWatcherPollingFallback:
    """Tests for polling fallback behavior."""

    @pytest.mark.asyncio
    async def test_polling_detects_changes(self) -> None:
        """Verify polling mode detects file changes."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\nplugins: {}\n")
            settings_path = f.name

        try:
            callback = AsyncMock()

            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=callback,
                poll_interval=0.1,
            )

            # Force polling mode by patching
            with patch.object(
                watcher, "_watch_with_watchfiles", watcher._watch_with_polling
            ):
                await watcher.start()

                # Wait for initial poll
                await asyncio.sleep(0.05)

                # Modify file
                new_content = (
                    "version: '1'\nplugins:\n  test: {type: in_source, module: foo}\n"
                )
                Path(settings_path).write_text(new_content)

                # Wait for change detection
                await asyncio.sleep(0.2)

                await watcher.stop()

            # Callback should have been called
            assert callback.called
        finally:
            Path(settings_path).unlink()


class TestConfigWatcherChangeDetection:
    """Tests for change detection and callback invocation."""

    @pytest.mark.asyncio
    async def test_handle_change_calls_callback(self) -> None:
        """Verify _handle_change calls the on_change callback."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\nplugins: {}\n")
            settings_path = f.name

        try:
            callback = AsyncMock()

            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=callback,
            )

            await watcher._handle_change()

            assert callback.called
            # Verify callback received OpenCuffSettings
            call_args = callback.call_args[0]
            assert isinstance(call_args[0], OpenCuffSettings)
        finally:
            Path(settings_path).unlink()

    @pytest.mark.asyncio
    async def test_handle_change_logs_error_on_invalid_yaml(self) -> None:
        """Verify _handle_change handles invalid YAML gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("invalid: yaml: content: :")
            settings_path = f.name

        try:
            callback = AsyncMock()

            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=callback,
            )

            # Should not raise
            await watcher._handle_change()

            # Callback should not be called due to error
            assert not callback.called
        finally:
            Path(settings_path).unlink()

    @pytest.mark.asyncio
    async def test_hash_updated_on_change(self) -> None:
        """Verify last_hash is updated when change is detected."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\nplugins: {}\n")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
                poll_interval=0.1,
            )

            # Force polling mode
            with patch.object(
                watcher, "_watch_with_watchfiles", watcher._watch_with_polling
            ):
                await watcher.start()

                initial_hash = watcher._last_hash

                # Modify file
                new_content = (
                    "version: '1'\nplugins:\n  new: {type: in_source, module: bar}\n"
                )
                Path(settings_path).write_text(new_content)

                # Wait for detection
                await asyncio.sleep(0.2)

                # Hash should have changed
                assert watcher._last_hash != initial_hash

                await watcher.stop()
        finally:
            Path(settings_path).unlink()


class TestWatchfilesAvailability:
    """Tests for watchfiles availability detection."""

    def test_watchfiles_available_constant_exists(self) -> None:
        """Verify WATCHFILES_AVAILABLE constant is defined."""
        # Just verify it's a boolean
        assert isinstance(WATCHFILES_AVAILABLE, bool)

    @pytest.mark.asyncio
    async def test_falls_back_to_polling_when_watchfiles_unavailable(self) -> None:
        """Verify fallback to polling when watchfiles is not available."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("version: '1'\nplugins: {}\n")
            settings_path = f.name

        try:
            watcher = ConfigWatcher(
                settings_path=settings_path,
                on_change=AsyncMock(),
                poll_interval=0.1,
            )

            # Simulate watchfiles being unavailable
            with patch("opencuff.plugins.watcher.WATCHFILES_AVAILABLE", False):
                await watcher.start()

                # Should be running (in polling mode)
                assert watcher.is_running is True

                await watcher.stop()
        finally:
            Path(settings_path).unlink()
