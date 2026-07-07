"""
Simple in-process circuit breakers for Serper and Anthropic APIs.

State machine (per worker process):
  CLOSED  → normal; call is allowed through
  OPEN    → fail_max consecutive failures recorded; calls fast-fail for reset_timeout seconds
  (auto)  → after reset_timeout, OPEN → CLOSED on next call attempt (half-open probe)

Only 5xx and network-level errors count as failures.
4xx errors (auth, bad request) are the caller's fault, not the service's.

State is NOT shared across worker processes or Celery tasks — each process has
its own counters.  For fleet-wide coordination a Redis-backed breaker would be
needed; per-process is a significant improvement for free.
"""
import time
from dataclasses import dataclass, field


class CircuitBreakerOpen(RuntimeError):
    """Raised when a call is fast-failed because the circuit is OPEN."""


@dataclass
class CircuitBreaker:
    """
    Lightweight half-open circuit breaker.

    Usage:
        if breaker.is_open():
            raise CircuitBreakerOpen("...")
        try:
            result = await external_call()
            breaker.record_success()
            return result
        except RecoverableError:
            breaker.record_failure()
            raise
        except ClientError:
            raise  # do NOT record_failure for 4xx
    """
    name: str
    fail_max: int
    reset_timeout: float   # seconds to stay OPEN before auto-reset

    _failures: int = field(default=0, init=False, repr=False)
    _opened_at: float = field(default=0.0, init=False, repr=False)
    _open: bool = field(default=False, init=False, repr=False)

    def is_open(self) -> bool:
        """Return True if the circuit is OPEN and the call should be fast-failed."""
        if not self._open:
            return False
        if time.monotonic() - self._opened_at >= self.reset_timeout:
            # Auto-reset: allow the next call through as a half-open probe
            self._open = False
            self._failures = 0
            return False
        return True

    def record_failure(self) -> None:
        """Call after a retriable failure (5xx, network error, timeout)."""
        self._failures += 1
        if self._failures >= self.fail_max:
            self._open = True
            self._opened_at = time.monotonic()

    def record_success(self) -> None:
        """Call after a successful response — resets the failure counter."""
        self._failures = 0
        self._open = False


# ── Module-level singletons (one per worker process) ──────────────────────────

serper_breaker = CircuitBreaker(
    name="serper",
    fail_max=5,         # open after 5 consecutive 5xx/network errors
    reset_timeout=60.0, # stay open for 60 s, then half-open probe
)

anthropic_breaker = CircuitBreaker(
    name="anthropic",
    fail_max=3,          # LLM is the most critical stage; open sooner
    reset_timeout=120.0, # Anthropic outages tend to be longer
)
