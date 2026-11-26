"""Circuit breaker and retry behaviour."""

from __future__ import annotations

import anyio
import pytest

from kairos.errors import InvalidRequestError, SourceUnavailableError
from kairos.resilience import BreakerState, CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def run(coro_fn):  # type: ignore[no-untyped-def]
    return anyio.run(coro_fn)


def make(clock: Clock, retries: int = 3) -> CircuitBreaker:
    return CircuitBreaker("home", clock=clock, retry_attempts=retries, backoff_base=0.0)


def test_retries_then_succeeds() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise SourceUnavailableError("blip")
        return "ok"

    breaker = make(Clock())
    assert run(lambda: breaker.call(fn)) == "ok"
    assert calls == 3
    assert breaker.state is BreakerState.CLOSED


def test_no_retry_for_writes() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise SourceUnavailableError("timeout")

    breaker = make(Clock())
    with pytest.raises(SourceUnavailableError):
        run(lambda: breaker.call(fn, retry=False))
    assert calls == 1


def test_client_errors_are_not_failures() -> None:
    async def fn() -> None:
        raise InvalidRequestError("bad")

    breaker = make(Clock())
    for _ in range(10):
        with pytest.raises(InvalidRequestError):
            run(lambda: breaker.call(fn))
    assert breaker.state is BreakerState.CLOSED


def test_opens_then_half_opens_then_closes() -> None:
    clock = Clock()
    breaker = make(clock, retries=1)

    async def fail() -> None:
        raise SourceUnavailableError("down")

    async def ok() -> str:
        return "ok"

    for _ in range(5):
        with pytest.raises(SourceUnavailableError):
            run(lambda: breaker.call(fail))
    assert breaker.state is BreakerState.OPEN
    with pytest.raises(SourceUnavailableError, match="paused"):
        run(lambda: breaker.call(ok))

    clock.now += 301
    assert run(lambda: breaker.call(ok)) == "ok"
    assert breaker.state is BreakerState.CLOSED


def test_half_open_failure_reopens() -> None:
    clock = Clock()
    breaker = make(clock, retries=1)

    async def fail() -> None:
        raise SourceUnavailableError("down")

    for _ in range(5):
        with pytest.raises(SourceUnavailableError):
            run(lambda: breaker.call(fail))
    clock.now += 301
    with pytest.raises(SourceUnavailableError):
        run(lambda: breaker.call(fail))
    assert breaker.state is BreakerState.OPEN
    assert breaker.status()["last_error"] == "down"


def test_old_failures_age_out() -> None:
    clock = Clock()
    breaker = make(clock, retries=1)

    async def fail() -> None:
        raise SourceUnavailableError("down")

    for _ in range(4):
        with pytest.raises(SourceUnavailableError):
            run(lambda: breaker.call(fail))
    clock.now += 61
    with pytest.raises(SourceUnavailableError):
        run(lambda: breaker.call(fail))
    assert breaker.state is BreakerState.CLOSED
