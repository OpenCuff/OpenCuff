"""Configuration file watcher.

This module provides the ConfigWatcher class which monitors the settings.yml
file for changes and triggers reload callbacks when modifications are detected.

The watcher uses a two-tier approach:
    1. Primary: watchfiles (inotify/FSEvents) for immediate notifications
    2. Fallback: Polling for environments where watchfiles doesn't work

Classes:
    - ConfigWatcher: Watches configuration file for changes
"""

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog

from opencuff.plugins.config import OpenCuffSettings, load_settings

# Try to import watchfiles for efficient file watching
try:
    from watchfiles import awatch

    WATCHFILES_AVAILABLE = True
except ImportError:
    WATCHFILES_AVAILABLE = False
    awatch = None  # type: ignore[assignment, misc]

logger = structlog.get_logger()


class ConfigWatcher:
    """Watches configuration file for changes.

    Uses watchfiles for immediate OS-level notifications where available,
    with automatic fallback to polling for environments where inotify
    is not supported (e.g., network filesystems).

    Attributes:
        settings_path: Path to the settings.yml file.
        on_change: Callback function called when settings change.
        poll_interval: Interval (seconds) for fallback polling.

    Example:
        async def handle_config_change(settings: OpenCuffSettings) -> None:
            print(f"Config changed: {settings}")

        watcher = ConfigWatcher(
            settings_path="./settings.yml",
            on_change=handle_config_change,
        )
        await watcher.start()

        # ... later ...
        await watcher.stop()
    """

    def __init__(
        self,
        settings_path: str | Path,
        on_change: Callable[[OpenCuffSettings], Awaitable[None]],
        poll_interval: float = 5.0,
    ) -> None:
        """Initialize the config watcher.

        Args:
            settings_path: Path to the settings.yml file.
            on_change: Async callback function called when settings change.
            poll_interval: Polling interval in seconds for fallback mode.
        """
        self.settings_path = Path(settings_path)
        self.on_change = on_change
        self.poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_hash: str | None = None

    async def start(self) -> None:
        """Start watching for configuration changes.

        Attempts to use watchfiles first, falling back to polling if
        watchfiles is unavailable or fails.
        """
        if self._running:
            logger.warning("config_watcher_already_running")
            return

        self._running = True
        self._last_hash = self._compute_hash()

        # Try watchfiles first, fall back to polling if it fails
        try:
            self._task = asyncio.create_task(self._watch_with_watchfiles())
            logger.info(
                "config_watcher_started",
                method="watchfiles",
                path=str(self.settings_path),
            )
        except Exception as e:
            logger.warning(
                "watchfiles_unavailable",
                error=str(e),
                poll_interval=self.poll_interval,
            )
            self._task = asyncio.create_task(self._watch_with_polling())
            logger.info(
                "config_watcher_started",
                method="polling",
                interval=self.poll_interval,
                path=str(self.settings_path),
            )

    async def stop(self) -> None:
        """Stop watching for configuration changes."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("config_watcher_stopped")

    def _compute_hash(self) -> str:
        """Compute SHA-256 hash of settings file for change detection.

        Returns:
            Hex digest of the file's SHA-256 hash.
        """
        try:
            content = self.settings_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except FileNotFoundError:
            return ""

    async def _watch_with_watchfiles(self) -> None:
        """Watch using watchfiles (inotify/FSEvents).

        This provides immediate notifications on file changes.
        """
        if not WATCHFILES_AVAILABLE or awatch is None:
            # watchfiles not installed, fall back to polling
            logger.warning("watchfiles_not_installed")
            await self._watch_with_polling()
            return

        try:
            async for _changes in awatch(self.settings_path):
                if not self._running:
                    break

                # Verify the change with hash comparison
                # (watchfiles can report spurious changes)
                current_hash = self._compute_hash()
                if current_hash != self._last_hash:
                    self._last_hash = current_hash
                    await self._handle_change()

        except Exception as e:
            if self._running:
                logger.error("watchfiles_error", error=str(e))
                # Fall back to polling on error
                await self._watch_with_polling()

    async def _watch_with_polling(self) -> None:
        """Fallback: watch using periodic polling.

        This is used when watchfiles is unavailable or fails.
        """
        logger.info(
            "config_watcher_polling",
            interval=self.poll_interval,
            path=str(self.settings_path),
        )

        while self._running:
            await asyncio.sleep(self.poll_interval)

            if not self._running:
                break

            current_hash = self._compute_hash()
            if current_hash != self._last_hash:
                self._last_hash = current_hash
                await self._handle_change()

    async def _handle_change(self) -> None:
        """Process a detected configuration change.

        Loads the new settings and calls the on_change callback.
        """
        try:
            logger.info("config_change_detected", path=str(self.settings_path))
            new_settings = load_settings(self.settings_path)
            await self.on_change(new_settings)
            logger.info("config_change_processed")
        except Exception as e:
            logger.error("config_change_error", error=str(e))

    @property
    def is_running(self) -> bool:
        """Return True if the watcher is currently running."""
        return self._running
