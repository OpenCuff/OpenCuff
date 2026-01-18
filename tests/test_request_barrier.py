"""Tests for the request barrier module.

Tests cover:
    - Request tracking with request_scope
    - Reload blocks new requests
    - In-flight requests complete before reload
    - Timeout handling for queued requests
    - Concurrent request handling
"""

import asyncio

import pytest

from opencuff.plugins.barrier import RequestBarrier
from opencuff.plugins.errors import PluginError, PluginErrorCode


class TestRequestScope:
    """Tests for request_scope context manager."""

    @pytest.mark.asyncio
    async def test_request_scope_allows_normal_requests(self) -> None:
        """Verify requests can proceed normally when not reloading."""
        barrier = RequestBarrier()

        async with barrier.request_scope():
            # Should be able to do work here
            await asyncio.sleep(0.01)

        # Should complete without error

    @pytest.mark.asyncio
    async def test_request_scope_tracks_active_requests(self) -> None:
        """Verify active request count is tracked."""
        barrier = RequestBarrier()

        assert barrier.active_requests == 0

        async def check_inside():
            async with barrier.request_scope():
                # Inside the scope, count should be 1
                assert barrier.active_requests >= 1
                await asyncio.sleep(0.05)

        await check_inside()

        # After scope exits, count should be 0
        assert barrier.active_requests == 0

    @pytest.mark.asyncio
    async def test_multiple_concurrent_requests_tracked(self) -> None:
        """Verify multiple concurrent requests are all tracked."""
        barrier = RequestBarrier()
        max_concurrent = 0
        lock = asyncio.Lock()

        async def request():
            nonlocal max_concurrent
            async with barrier.request_scope():
                async with lock:
                    if barrier.active_requests > max_concurrent:
                        max_concurrent = barrier.active_requests
                await asyncio.sleep(0.05)

        # Start 5 concurrent requests
        await asyncio.gather(*[request() for _ in range(5)])

        assert max_concurrent == 5
        assert barrier.active_requests == 0


class TestReloadScope:
    """Tests for reload_scope context manager."""

    @pytest.mark.asyncio
    async def test_reload_scope_blocks_new_requests(self) -> None:
        """Verify new requests are blocked during reload."""
        barrier = RequestBarrier(queue_timeout=0.1)
        request_started = asyncio.Event()
        request_completed = asyncio.Event()

        async def blocked_request():
            request_started.set()
            try:
                async with barrier.request_scope():
                    request_completed.set()
            except PluginError:
                pass  # Expected timeout

        # Start reload
        async with barrier.reload_scope():
            # Start a request that should be blocked
            request_task = asyncio.create_task(blocked_request())

            # Wait a bit for the request to start
            await asyncio.sleep(0.05)

            # Request should have started but not completed
            assert request_started.is_set()
            assert not request_completed.is_set()

            # Let the request timeout
            await asyncio.sleep(0.1)

        # Wait for the task to finish
        await request_task

    @pytest.mark.asyncio
    async def test_reload_waits_for_in_flight_requests(self) -> None:
        """Verify reload waits for in-flight requests to complete."""
        barrier = RequestBarrier()
        request_started = asyncio.Event()
        request_can_complete = asyncio.Event()
        reload_started = asyncio.Event()
        reload_completed = asyncio.Event()

        async def slow_request():
            async with barrier.request_scope():
                request_started.set()
                await request_can_complete.wait()

        async def do_reload():
            reload_started.set()
            async with barrier.reload_scope():
                # This should only execute after slow_request completes
                reload_completed.set()

        # Start a slow request
        request_task = asyncio.create_task(slow_request())
        await request_started.wait()

        # Start reload
        reload_task = asyncio.create_task(do_reload())
        await reload_started.wait()

        # Give reload time to wait
        await asyncio.sleep(0.05)

        # Reload should not have completed yet
        assert not reload_completed.is_set()

        # Allow the request to complete
        request_can_complete.set()

        # Wait for both to finish
        await asyncio.gather(request_task, reload_task)

        # Now reload should have completed
        assert reload_completed.is_set()

    @pytest.mark.asyncio
    async def test_requests_resume_after_reload(self) -> None:
        """Verify requests can proceed after reload completes."""
        barrier = RequestBarrier()

        # Do a reload
        async with barrier.reload_scope():
            pass

        # Requests should work again
        async with barrier.request_scope():
            await asyncio.sleep(0.01)


class TestTimeoutHandling:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_queued_request_times_out(self) -> None:
        """Verify queued requests timeout after configured duration."""
        barrier = RequestBarrier(queue_timeout=0.1)
        reload_started = asyncio.Event()

        async def hold_reload():
            reload_started.set()
            async with barrier.reload_scope():
                # Hold the reload for longer than timeout
                await asyncio.sleep(0.3)

        # Start and hold the reload
        reload_task = asyncio.create_task(hold_reload())
        await reload_started.wait()

        # Allow reload to acquire lock
        await asyncio.sleep(0.02)

        # Try to make a request - should timeout
        with pytest.raises(PluginError) as exc_info:
            async with barrier.request_scope():
                pass

        assert exc_info.value.code == PluginErrorCode.TIMEOUT
        assert "reload in progress" in exc_info.value.message.lower()

        # Cancel the long-running reload
        import contextlib

        reload_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reload_task

    @pytest.mark.asyncio
    async def test_custom_timeout_value(self) -> None:
        """Verify custom timeout value is respected."""
        import contextlib

        barrier = RequestBarrier(queue_timeout=0.05)
        reload_started = asyncio.Event()

        async def hold_reload():
            reload_started.set()
            async with barrier.reload_scope():
                await asyncio.sleep(0.2)

        reload_task = asyncio.create_task(hold_reload())
        await reload_started.wait()
        await asyncio.sleep(0.02)

        start = asyncio.get_event_loop().time()
        with pytest.raises(PluginError):
            async with barrier.request_scope():
                pass
        elapsed = asyncio.get_event_loop().time() - start

        # Should timeout around 0.05 seconds (with some margin)
        assert 0.03 < elapsed < 0.15

        reload_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reload_task


class TestReloadingState:
    """Tests for the reloading state property."""

    @pytest.mark.asyncio
    async def test_is_reloading_false_initially(self) -> None:
        """Verify is_reloading is False initially."""
        barrier = RequestBarrier()
        assert barrier.is_reloading is False

    @pytest.mark.asyncio
    async def test_is_reloading_true_during_reload(self) -> None:
        """Verify is_reloading is True during reload."""
        barrier = RequestBarrier()
        inside_reload = asyncio.Event()

        async def check_reload():
            async with barrier.reload_scope():
                inside_reload.set()
                assert barrier.is_reloading is True
                await asyncio.sleep(0.05)

        task = asyncio.create_task(check_reload())
        await inside_reload.wait()
        assert barrier.is_reloading is True

        await task
        assert barrier.is_reloading is False


class TestConcurrentReloads:
    """Tests for handling concurrent reload attempts."""

    @pytest.mark.asyncio
    async def test_concurrent_reloads_serialized(self) -> None:
        """Verify concurrent reload attempts are serialized."""
        barrier = RequestBarrier()
        order: list[str] = []

        async def reload(name: str):
            async with barrier.reload_scope():
                order.append(f"{name}_start")
                await asyncio.sleep(0.05)
                order.append(f"{name}_end")

        # Start two concurrent reloads
        await asyncio.gather(reload("first"), reload("second"))

        # One should complete before the other starts
        # Either: first_start, first_end, second_start, second_end
        # Or: second_start, second_end, first_start, first_end
        assert len(order) == 4
        first_start_idx = order.index("first_start")
        first_end_idx = order.index("first_end")
        second_start_idx = order.index("second_start")
        second_end_idx = order.index("second_end")

        # The first one to start should finish before the other starts
        if first_start_idx < second_start_idx:
            assert first_end_idx < second_start_idx
        else:
            assert second_end_idx < first_start_idx


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_request_scope_exception_still_releases(self) -> None:
        """Verify request count is decremented even if exception is raised."""
        barrier = RequestBarrier()

        with pytest.raises(ValueError):
            async with barrier.request_scope():
                raise ValueError("test error")

        assert barrier.active_requests == 0

    @pytest.mark.asyncio
    async def test_reload_scope_exception_still_releases(self) -> None:
        """Verify reload state is reset even if exception is raised."""
        barrier = RequestBarrier()

        with pytest.raises(ValueError):
            async with barrier.reload_scope():
                raise ValueError("test error")

        assert barrier.is_reloading is False

        # Requests should work after failed reload
        async with barrier.request_scope():
            pass
