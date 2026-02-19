"""Tests for LLM circuit breaker."""

import time

import pytest

from src.core.llm_caller import _CircuitBreaker


class TestCircuitBreakerStates:
    def test_starts_closed(self):
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        assert cb.state == _CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        cb = _CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        cb.record_failure()
        assert cb.state == _CircuitBreaker.CLOSED
        cb.record_failure()
        assert cb.state == _CircuitBreaker.OPEN
        assert cb.allow_request() is False

    def test_success_resets_count(self):
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Only 1 failure after reset, not 3
        assert cb.state == _CircuitBreaker.CLOSED

    def test_half_open_after_timeout(self):
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        assert cb.state == _CircuitBreaker.OPEN
        time.sleep(0.06)
        assert cb.state == _CircuitBreaker.HALF
        assert cb.allow_request() is True

    def test_success_in_half_open_closes(self):
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == _CircuitBreaker.HALF
        cb.record_success()
        assert cb.state == _CircuitBreaker.CLOSED

    def test_failure_in_half_open_reopens(self):
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == _CircuitBreaker.HALF
        cb.record_failure()
        assert cb.state == _CircuitBreaker.OPEN


class TestCircuitBreakerDefaults:
    def test_default_threshold(self):
        cb = _CircuitBreaker()
        assert cb.failure_threshold == 3

    def test_default_timeout(self):
        cb = _CircuitBreaker()
        assert cb.recovery_timeout == 60
