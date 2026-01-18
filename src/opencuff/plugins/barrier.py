"""Request barrier for managing requests during plugin reload.

This module provides the RequestBarrier class which ensures safe plugin
reloading by:
    1. Allowing in-flight requests to complete with the OLD plugin
    2. Queuing new requests during reload
    3. Releasing queued requests after reload completes

Classes:
    - RequestBarrier: Manages request flow during plugin reload

The barrier implements a two-phase protocol:
    1. Block new requests (but allow in-flight to continue)
    2. Wait for all in-flight requests to drain
    3. Perform reload
    4. Unblock requests
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from opencuff.plugins.errors import PluginError, PluginErrorCode


class RequestBarrier:
    """Manages request flow during plugin reload.

    Ensures in-flight requests complete before reload and queues new
    requests until the reload is complete.

    The barrier provides two context managers:
        - request_scope(): Wrap tool invocations to track request lifecycle
        - reload_scope(): Wrap reload operations to block/queue requests

    Thread Safety:
        This class is designed for use with asyncio and is safe to use
        from multiple concurrent coroutines. It uses the following
        synchronization primitives:
            - asyncio.Lock: Protects the active request counter
            - asyncio.Event: Signals when requests have drained
            - asyncio.Event: Signals when reload is complete
            - asyncio.Lock: Serializes concurrent reload operations

        Note that this class is NOT thread-safe for use across multiple
        threads. It should only be used within a single asyncio event loop.

    Attributes:
        queue_timeout: Maximum time (seconds) for queued requests to wait.

    Example:
        barrier = RequestBarrier(queue_timeout=5.0)

        # In request handler:
        async with barrier.request_scope():
            result = await plugin.call_tool(...)

        # In reload handler:
        async with barrier.reload_scope():
            await plugin.reload(new_config)
    """

    def __init__(self, queue_timeout: float = 5.0) -> None:
        """Initialize the request barrier.

        Args:
            queue_timeout: Maximum time (seconds) for requests to wait
                when queued during a reload. Default is 5 seconds.
        """
        self._active_requests: int = 0
        self._lock = asyncio.Lock()
        self._drain_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._reloading = False
        self._queue_timeout = queue_timeout
        self._reload_lock = asyncio.Lock()

        # Initially ready (not reloading)
        self._ready_event.set()
        self._drain_event.set()

    @property
    def active_requests(self) -> int:
        """Return the number of currently active requests."""
        return self._active_requests

    @property
    def is_reloading(self) -> bool:
        """Return True if a reload is currently in progress."""
        return self._reloading

    @asynccontextmanager
    async def request_scope(self) -> AsyncIterator[None]:
        """Context manager for tracking request lifecycle.

        Tracks the request as active and ensures it is counted toward
        in-flight requests during reload.

        If a reload is in progress when this is called, the request will
        wait for the reload to complete (up to queue_timeout seconds).

        Raises:
            PluginError: If the reload takes longer than queue_timeout.

        Example:
            async with barrier.request_scope():
                result = await plugin.call_tool(tool_name, arguments)
        """
        # Wait if a reload is in progress
        try:
            await asyncio.wait_for(
                self._ready_event.wait(),
                timeout=self._queue_timeout,
            )
        except TimeoutError as e:
            raise PluginError(
                code=PluginErrorCode.TIMEOUT,
                message="Plugin reload in progress, request timed out",
            ) from e

        # Register this request as active
        async with self._lock:
            self._active_requests += 1
            self._drain_event.clear()

        try:
            yield
        finally:
            # Unregister this request
            async with self._lock:
                self._active_requests -= 1
                if self._active_requests == 0:
                    self._drain_event.set()

    @asynccontextmanager
    async def reload_scope(self) -> AsyncIterator[None]:
        """Context manager for plugin reload operations.

        Blocks new requests, waits for in-flight requests to complete,
        then allows the reload to proceed. After the reload, requests
        are unblocked.

        Multiple concurrent reload attempts are serialized.

        Example:
            async with barrier.reload_scope():
                await plugin.shutdown()
                plugin = await create_new_plugin(new_config)
                await plugin.initialize()
        """
        # Serialize reload operations
        async with self._reload_lock:
            # Signal that reload is starting - block new requests
            self._ready_event.clear()
            self._reloading = True

            try:
                # Wait for all in-flight requests to complete
                await self._drain_event.wait()

                yield  # Perform the actual reload

            finally:
                # Reload complete - allow new requests
                self._reloading = False
                self._ready_event.set()
