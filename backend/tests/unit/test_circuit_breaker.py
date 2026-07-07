"""
Unit tests for CircuitBreaker.

Pure in-memory logic — no mocking, no DB.
"""
import time

import pytest

from app.core.circuit_breakers import CircuitBreaker, CircuitBreakerOpen


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(name="test", fail_max=3, reset_timeout=1.0)


class TestInitialState:
    def test_starts_closed(self, breaker):
        assert not breaker.is_open()

    def test_failure_count_starts_at_zero(self, breaker):
        assert breaker._failures == 0


class TestOpenTransition:
    def test_opens_after_fail_max_failures(self, breaker):
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_open()

    def test_does_not_open_before_fail_max(self, breaker):
        for _ in range(2):
            breaker.record_failure()
        assert not breaker.is_open()

    def test_single_failure_does_not_open(self, breaker):
        breaker.record_failure()
        assert not breaker.is_open()

    def test_opened_at_is_set_when_circuit_opens(self, breaker):
        for _ in range(3):
            breaker.record_failure()
        assert breaker._opened_at > 0.0


class TestSuccessReset:
    def test_success_resets_failure_count(self, breaker):
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        # Two more failures needed to reach fail_max=3; one so far since reset
        breaker.record_failure()
        assert not breaker.is_open()

    def test_success_after_open_closes_circuit(self, breaker):
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_open()
        breaker.record_success()
        assert not breaker.is_open()

    def test_success_on_clean_breaker_is_no_op(self, breaker):
        breaker.record_success()
        assert not breaker.is_open()
        assert breaker._failures == 0


class TestAutoReset:
    def test_auto_resets_after_timeout(self, breaker):
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_open()
        time.sleep(1.1)  # past reset_timeout=1.0s
        assert not breaker.is_open()

    def test_failure_count_cleared_after_auto_reset(self, breaker):
        for _ in range(3):
            breaker.record_failure()
        time.sleep(1.1)
        breaker.is_open()  # trigger auto-reset
        assert breaker._failures == 0

    def test_stays_open_before_timeout_elapses(self, breaker):
        for _ in range(3):
            breaker.record_failure()
        time.sleep(0.1)  # well before reset_timeout=1.0s
        assert breaker.is_open()


class TestCustomFailMax:
    def test_higher_fail_max_requires_more_failures(self):
        strict = CircuitBreaker(name="strict", fail_max=1, reset_timeout=60.0)
        strict.record_failure()
        assert strict.is_open()

    def test_tolerant_breaker_stays_closed_longer(self):
        tolerant = CircuitBreaker(name="tolerant", fail_max=10, reset_timeout=60.0)
        for _ in range(9):
            tolerant.record_failure()
        assert not tolerant.is_open()
        tolerant.record_failure()
        assert tolerant.is_open()
