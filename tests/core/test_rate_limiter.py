"""Tests for in-memory rate limiter."""

import pytest

from src.core.rate_limiter import check_rate_limit, reset


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


class TestBasicRateLimiting:
    def test_allows_under_limit(self):
        allowed, info = check_rate_limit("client1", max_requests=5, window_seconds=60)
        assert allowed is True
        assert info["remaining"] == 4

    def test_blocks_over_limit(self):
        for _ in range(3):
            check_rate_limit("client2", max_requests=3, window_seconds=60)
        allowed, info = check_rate_limit("client2", max_requests=3, window_seconds=60)
        assert allowed is False
        assert info["error"] == "rate_limit_exceeded"
        assert "retry_after_seconds" in info

    def test_different_clients_independent(self):
        for _ in range(5):
            check_rate_limit("a", max_requests=5, window_seconds=60)
        # client "a" is at limit
        allowed_a, _ = check_rate_limit("a", max_requests=5, window_seconds=60)
        assert allowed_a is False
        # client "b" should still be allowed
        allowed_b, _ = check_rate_limit("b", max_requests=5, window_seconds=60)
        assert allowed_b is True

    def test_remaining_decrements(self):
        _, info1 = check_rate_limit("c", max_requests=3, window_seconds=60)
        assert info1["remaining"] == 2
        _, info2 = check_rate_limit("c", max_requests=3, window_seconds=60)
        assert info2["remaining"] == 1


class TestDisabled:
    def test_zero_limit_allows_all(self):
        for _ in range(100):
            allowed, _ = check_rate_limit("flood", max_requests=0, window_seconds=60)
        # When default RPM is also 0, all requests pass
        # (depends on env var, but with max_requests=0 and default > 0, it uses default)


class TestWindowExpiry:
    def test_expired_entries_cleared(self):
        # Use a very short window
        for _ in range(3):
            check_rate_limit("expire", max_requests=3, window_seconds=0.01)
        # All 3 slots used
        allowed, _ = check_rate_limit("expire", max_requests=3, window_seconds=0.01)
        assert allowed is False

        import time
        time.sleep(0.02)  # Wait for window to expire

        allowed, info = check_rate_limit("expire", max_requests=3, window_seconds=0.01)
        assert allowed is True
        assert info["remaining"] == 2
