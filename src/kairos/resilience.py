"""Retry with backoff plus a per-source circuit breaker.

After FAILURE_THRESHOLD failures inside FAILURE_WINDOW_SECONDS the breaker
opens and fails fast for OPEN_DURATION_SECONDS, then lets one trial call
through (half-open). Only SourceUnavailableError counts as a failure:
a 404 or a validation error says nothing about the upstream's health.

Backoff sleeps are async, so a retrying call never blocks the event loop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

import anyio

from kairos.errors import SourceUnavailableError

logger = logging.getLogger("kairos.breaker")

T = TypeVar("T")

FAILURE_THRESHOLD = 5
FAILURE_WINDOW_SECONDS = 60.0
OPEN_DURATION_SECONDS = 300.0
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 2.0
RETRY_BACKOFF_MAX_SECONDS = 30.0


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _State:
    state: BreakerState = BreakerState.CLOSED
    failure_times: list[float] = field(default_factory=list)
    opened_at: float = 0.0
    last_error: str | None = None
    last_success: float | None = None


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        retry_attempts: int = RETRY_ATTEMPTS,
        backoff_base: float = RETRY_BACKOFF_BASE_SECONDS,
    ) -> None:
        self.name = name
        self._clock = clock
        self._retry_attempts = retry_attempts
        self._backoff_base = backoff_base
        self._lock = threading.Lock()
        self._s = _State()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._s.state

    async def call(self, fn: Callable[[], Awaitable[T]], *, retry: bool = True) -> T:
        """Run fn with retries. Writes pass retry=False: a timed-out PUT may have landed."""
        self._admit()
        attempts = self._retry_attempts if retry else 1
        last_exc: SourceUnavailableError | None = None
        for attempt in range(attempts):
            try:
                result = await fn()
            except SourceUnavailableError as exc:
                last_exc = exc
                self._record_failure(str(exc))
                if self.state is BreakerState.OPEN or attempt == attempts - 1:
                    break
                delay = min(self._backoff_base * (2**attempt), RETRY_BACKOFF_MAX_SECONDS)
                logger.warning("caldav_retry", extra={"source_id": self.name, "attempt": attempt + 1, "delay": delay})
                await anyio.sleep(delay)
            else:
                self._record_success()
                return result
        assert last_exc is not None
        raise last_exc

    def _admit(self) -> None:
        with self._lock:
            if self._s.state is BreakerState.OPEN:
                if self._clock() - self._s.opened_at >= OPEN_DURATION_SECONDS:
                    self._s.state = BreakerState.HALF_OPEN
                    logger.info("circuit_half_open", extra={"source_id": self.name})
                else:
                    raise SourceUnavailableError(
                        f"Calendar source {self.name!r} is failing; requests are paused for a few minutes."
                    )

    def _record_success(self) -> None:
        with self._lock:
            self._s = _State(last_success=self._clock())

    def _record_failure(self, message: str) -> None:
        with self._lock:
            now = self._clock()
            self._s.last_error = message
            if self._s.state is BreakerState.HALF_OPEN:
                self._s.state = BreakerState.OPEN
                self._s.opened_at = now
                self._s.failure_times = []
                logger.warning("circuit_reopened", extra={"source_id": self.name})
                return
            self._s.failure_times = [t for t in self._s.failure_times if now - t < FAILURE_WINDOW_SECONDS]
            self._s.failure_times.append(now)
            if self._s.state is BreakerState.CLOSED and len(self._s.failure_times) >= FAILURE_THRESHOLD:
                self._s.state = BreakerState.OPEN
                self._s.opened_at = now
                logger.error("circuit_opened", extra={"source_id": self.name})

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._s.state.value,
                "recent_failures": len(self._s.failure_times),
                "last_error": self._s.last_error,
            }
