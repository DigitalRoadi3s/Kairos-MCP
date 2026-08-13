"""
Circuit breaker + retry/backoff (task 16, Opus tier). NFR-2 / Known Risks.

Wraps CalDAV calls with:
- exponential backoff retry (2s-30s, 3 attempts) on transient failures
- a circuit breaker: after 5 consecutive failures within 60s, trip OPEN
  and return 503 immediately (no retry attempts) for 5 minutes, then
  allow one trial call (HALF_OPEN) before fully closing again.

Concurrency: state is per-source (one breaker per CalDAVSourceClient),
protected by a lock since multiple requests can hit the same source
concurrently. State transitions are the only thing under the lock -
the actual CalDAV call happens outside it, so a slow upstream call
never blocks other requests from reading/updating breaker state.

Interface for Sonnet task 17: wrap every CalDAV client call site with
CircuitBreaker.call(fn). The breaker raises CalDAVUnavailableError
itself when OPEN - callers don't need a separate check.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar

from caldav_client import CalDAVUnavailableError

logger = logging.getLogger("caldav_gateway.circuit_breaker")

T = TypeVar("T")

FAILURE_THRESHOLD = 5
FAILURE_WINDOW_SECONDS = 60
OPEN_DURATION_SECONDS = 5 * 60
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 2
RETRY_BACKOFF_MAX_SECONDS = 30


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _BreakerState:
    state: _State = _State.CLOSED
    failure_times: list[float] = field(default_factory=list)  # monotonic timestamps
    opened_at: float = 0.0


class CircuitBreaker:
    """One instance per calendar source."""

    def __init__(self, source_id: str):
        self.source_id = source_id
        self._lock = threading.Lock()
        self._state = _BreakerState()

    def call(self, fn: Callable[[], T]) -> T:
        """
        Runs fn() with retry+backoff, tracked by this breaker. Raises
        CalDAVUnavailableError if the breaker is OPEN (fn is never
        called in that case) or if all retries are exhausted.
        """
        self._check_open()

        last_exc: Exception | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                result = fn()
                self._record_success()
                return result
            except CalDAVUnavailableError as exc:
                last_exc = exc
                self._record_failure()
                if self._is_open():
                    # Breaker just tripped (or was already open) - stop
                    # retrying against a service we've now given up on for
                    # this cooldown window, rather than burning the rest
                    # of this call's attempts against it.
                    break
                if attempt < RETRY_ATTEMPTS - 1:
                    delay = min(RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt),
                                RETRY_BACKOFF_MAX_SECONDS)
                    logger.warning("caldav_retry", extra={
                        "source_id": self.source_id, "attempt": attempt + 1,
                        "delay_seconds": delay,
                    })
                    time.sleep(delay)
        raise last_exc

    def _is_open(self) -> bool:
        with self._lock:
            return self._state.state == _State.OPEN

    def _check_open(self) -> None:
        with self._lock:
            if self._state.state == _State.OPEN:
                if time.monotonic() - self._state.opened_at >= OPEN_DURATION_SECONDS:
                    # Cooldown elapsed: allow exactly one trial call through.
                    self._state.state = _State.HALF_OPEN
                    logger.info("circuit_half_open", extra={"source_id": self.source_id})
                else:
                    raise CalDAVUnavailableError(
                        f"Circuit open for '{self.source_id}' after repeated "
                        f"failures - retry after cooldown."
                    )

    def _record_success(self) -> None:
        with self._lock:
            # Any success (including the HALF_OPEN trial) fully resets.
            self._state = _BreakerState()

    def _record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state.state == _State.HALF_OPEN:
                # Trial call failed - reopen immediately, don't wait for
                # another 5-failure window.
                self._state.state = _State.OPEN
                self._state.opened_at = now
                self._state.failure_times = []
                logger.warning("circuit_reopened", extra={"source_id": self.source_id})
                return

            self._state.failure_times = [
                t for t in self._state.failure_times if now - t < FAILURE_WINDOW_SECONDS
            ]
            self._state.failure_times.append(now)
            if self._state.state != _State.OPEN and len(self._state.failure_times) >= FAILURE_THRESHOLD:
                self._state.state = _State.OPEN
                self._state.opened_at = now
                logger.error("circuit_opened", extra={
                    "source_id": self.source_id,
                    "failures_in_window": len(self._state.failure_times),
                })

    def status(self) -> dict:
        """For /health (task 17 wiring)."""
        with self._lock:
            return {
                "state": self._state.state.value,
                "recent_failures": len(self._state.failure_times),
            }
