"""RealtimeState: the per-process cap on long-lived connections, and the shutdown signal."""
import asyncio

import pytest

from app.core.exceptions import AppError
from app.realtime.state import RealtimeState


async def test_a_slot_is_taken_and_released() -> None:
    state = RealtimeState(2)
    async with state.slot():
        assert state.active == 1
        async with state.slot():
            assert state.active == 2
        assert state.active == 1
    assert state.active == 0


async def test_a_full_limiter_rejects_with_503_and_retry_after() -> None:
    state = RealtimeState(1)
    async with state.slot():
        with pytest.raises(AppError) as rejected:
            async with state.slot():
                raise AssertionError("must not get a slot")
    assert rejected.value.status_code == 503
    assert rejected.value.code == "too_many_connections"
    assert rejected.value.headers == {"Retry-After": "5"}
    # The rejection took nothing: the count is unchanged and a slot is free again.
    assert state.active == 0
    async with state.slot():
        assert state.active == 1


async def test_a_slot_is_released_when_the_body_raises() -> None:
    state = RealtimeState(1)
    with pytest.raises(RuntimeError):
        async with state.slot():
            raise RuntimeError("boom")
    assert state.active == 0


async def test_a_slot_is_released_on_cancellation() -> None:
    state = RealtimeState(1)
    entered = asyncio.Event()

    async def hold() -> None:
        async with state.slot():
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), 1)
    assert state.active == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.active == 0


async def test_the_count_never_goes_negative_under_concurrency() -> None:
    state = RealtimeState(3)
    seen: list[int] = []

    async def use() -> None:
        try:
            async with state.slot():
                seen.append(state.active)
                await asyncio.sleep(0.01)
        except AppError:
            pass

    await asyncio.gather(*(use() for _ in range(10)))

    assert state.active == 0
    assert 0 < max(seen) <= 3 and min(seen) >= 1


async def test_closing_starts_unset() -> None:
    assert not RealtimeState(1).closing.is_set()


def test_max_connections_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RealtimeState(0)
