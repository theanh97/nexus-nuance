"""Stress tests: Rate limiter under concurrent load.

Verifies the sliding-window rate limiter behaves correctly when
hammered by multiple simulated clients in parallel.
"""

import asyncio
import time
import threading
import pytest

from src.core.rate_limiter import check_rate_limit, reset, _buckets
import src.api.brain_api as brain_api


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    reset()
    yield
    reset()


# ── Basic rate limiting behaviour ────────────────────────────────

class TestRateLimiterBasic:
    def test_allows_within_limit(self):
        """Requests within the limit should all be allowed."""
        for i in range(5):
            allowed, info = check_rate_limit("client_a", max_requests=10, window_seconds=60)
            assert allowed, f"Request {i} should be allowed"
            assert info["remaining"] == 10 - i - 1

    def test_blocks_over_limit(self):
        """Request beyond the limit should be blocked."""
        for _ in range(3):
            check_rate_limit("client_b", max_requests=3, window_seconds=60)

        allowed, info = check_rate_limit("client_b", max_requests=3, window_seconds=60)
        assert not allowed
        assert info["error"] == "rate_limit_exceeded"
        assert info["remaining"] == 0
        assert "retry_after_seconds" in info

    def test_different_clients_independent(self):
        """Each client has its own bucket."""
        # Fill client_x bucket
        for _ in range(5):
            check_rate_limit("client_x", max_requests=5, window_seconds=60)

        # client_x is blocked
        allowed_x, _ = check_rate_limit("client_x", max_requests=5, window_seconds=60)
        assert not allowed_x

        # client_y is still fine
        allowed_y, info_y = check_rate_limit("client_y", max_requests=5, window_seconds=60)
        assert allowed_y
        assert info_y["remaining"] == 4

    def test_window_expiry(self):
        """After window expires, client can make requests again."""
        # Use a very short window
        for _ in range(2):
            check_rate_limit("client_c", max_requests=2, window_seconds=0.1)

        # Should be blocked now
        allowed, _ = check_rate_limit("client_c", max_requests=2, window_seconds=0.1)
        assert not allowed

        # Wait for window to expire
        time.sleep(0.15)

        # Should be allowed again
        allowed, info = check_rate_limit("client_c", max_requests=2, window_seconds=0.1)
        assert allowed
        assert info["remaining"] == 1


# ── Concurrent load stress ───────────────────────────────────────

class TestConcurrentStress:
    def test_thread_safety_under_load(self):
        """Multiple threads hammering the same client should not corrupt state."""
        results = {"allowed": 0, "blocked": 0}
        lock = threading.Lock()
        max_req = 20

        def hammer():
            allowed, _ = check_rate_limit("stress_client", max_requests=max_req, window_seconds=60)
            with lock:
                if allowed:
                    results["allowed"] += 1
                else:
                    results["blocked"] += 1

        threads = [threading.Thread(target=hammer) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly max_req should be allowed, rest blocked
        assert results["allowed"] == max_req
        assert results["blocked"] == 50 - max_req

    def test_multi_client_concurrent(self):
        """Different clients concurrent access should be isolated."""
        per_client_results = {}
        lock = threading.Lock()
        max_req = 5

        def client_requests(client_id):
            allowed_count = 0
            for _ in range(10):
                allowed, _ = check_rate_limit(client_id, max_requests=max_req, window_seconds=60)
                if allowed:
                    allowed_count += 1
            with lock:
                per_client_results[client_id] = allowed_count

        threads = [
            threading.Thread(target=client_requests, args=(f"c_{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each client should get exactly max_req allowed
        for client_id, allowed in per_client_results.items():
            assert allowed == max_req, f"{client_id} got {allowed} allowed, expected {max_req}"

    def test_rapid_burst_then_recovery(self):
        """Burst to limit → wait → burst again should work."""
        max_req = 5
        window = 0.2  # 200ms window

        # Burst 1: exhaust
        for _ in range(max_req):
            check_rate_limit("burst_client", max_requests=max_req, window_seconds=window)

        allowed, _ = check_rate_limit("burst_client", max_requests=max_req, window_seconds=window)
        assert not allowed

        # Wait for recovery
        time.sleep(window + 0.05)

        # Burst 2: should work again
        count = 0
        for _ in range(max_req):
            allowed, _ = check_rate_limit("burst_client", max_requests=max_req, window_seconds=window)
            if allowed:
                count += 1

        assert count == max_req


# ── API integration with rate limiting ───────────────────────────

class _FakeRequest:
    class _Client:
        host = "10.0.0.1"
    client = _Client()


def _make_low_limit_checker(max_req):
    """Create a rate limit checker with a low limit for testing."""
    def checker(client_key, max_requests=0, window_seconds=60.0):
        return check_rate_limit(client_key, max_requests=max_req, window_seconds=window_seconds)
    return checker


class TestAPIRateLimitIntegration:
    def test_api_learn_rate_limited(self, monkeypatch):
        """POST /learn returns 429 when rate limit exceeded."""
        monkeypatch.setattr(brain_api, "_check_rate_limit", _make_low_limit_checker(2))

        def mock_learn(**kw):
            item = type("Item", (), {"id": "dummy"})()
            return item

        monkeypatch.setattr(brain_api, "learn", lambda **kw: mock_learn(**kw))

        req = brain_api.LearnRequest(
            source="s", type="t", title="T", content="content",
        )

        # First 2 should succeed
        asyncio.run(brain_api.learn_knowledge(req, _FakeRequest()))
        asyncio.run(brain_api.learn_knowledge(req, _FakeRequest()))

        # 3rd should get 429
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(brain_api.learn_knowledge(req, _FakeRequest()))
        assert exc_info.value.status_code == 429

    def test_api_search_rate_limited(self, monkeypatch):
        """POST /search returns 429 when rate limit exceeded."""
        monkeypatch.setattr(brain_api, "_check_rate_limit", _make_low_limit_checker(1))

        class _FakeBrain:
            def search(self, q):
                return []

        monkeypatch.setattr(brain_api, "get_brain", lambda: _FakeBrain())

        req = brain_api.SearchRequest(query="test", limit=10)
        asyncio.run(brain_api.search_knowledge(req, _FakeRequest()))

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(brain_api.search_knowledge(req, _FakeRequest()))
        assert exc_info.value.status_code == 429

    def test_api_execute_rate_limited(self, monkeypatch):
        """POST /execute returns 429 when rate limit exceeded."""
        monkeypatch.setattr(brain_api, "_check_rate_limit", _make_low_limit_checker(1))

        class _Agent:
            def execute_autonomously(self, task, max_cycles=10):
                return {"success": True}

        monkeypatch.setattr(brain_api, "get_autonomous_agent", lambda: _Agent())

        req = brain_api.ExecuteRequest(task="x", max_cycles=1, verification_required=False)
        asyncio.run(brain_api.execute_task(req, _FakeRequest()))

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(brain_api.execute_task(req, _FakeRequest()))
        assert exc_info.value.status_code == 429


# ── Reset behaviour ──────────────────────────────────────────────

class TestRateLimiterReset:
    def test_reset_clears_all_buckets(self):
        """reset() should clear all client state."""
        for i in range(5):
            check_rate_limit(f"r_client_{i}", max_requests=1, window_seconds=60)

        assert len(_buckets) >= 5
        reset()
        assert len(_buckets) == 0

    def test_disabled_rate_limiter(self, monkeypatch):
        """Setting RPM to 0 disables rate limiting."""
        monkeypatch.setattr("src.core.rate_limiter._DEFAULT_RPM", 0)

        for _ in range(1000):
            allowed, _ = check_rate_limit("unlimited_client")
            assert allowed
