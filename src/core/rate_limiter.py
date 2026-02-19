"""
Simple in-memory sliding-window rate limiter for NEXUS API.

Usage:
    from core.rate_limiter import check_rate_limit

    allowed, info = check_rate_limit("client_ip", max_requests=60, window_seconds=60)
    if not allowed:
        raise HTTPException(429, detail=info)

Environment variables:
    NEXUS_RATE_LIMIT_RPM  – requests per minute (default: 120, 0 = disabled)
"""

import os
import time
import threading
from typing import Dict, List, Tuple


_lock = threading.Lock()
_buckets: Dict[str, List[float]] = {}

_DEFAULT_RPM = int(os.getenv("NEXUS_RATE_LIMIT_RPM", "120"))
_PRUNE_INTERVAL = 300  # seconds between pruning stale buckets
_last_prune: float = 0.0


def check_rate_limit(
    client_key: str,
    max_requests: int = 0,
    window_seconds: float = 60.0,
) -> Tuple[bool, Dict]:
    """Check whether a request from *client_key* should be allowed.

    Returns:
        (allowed, info_dict)
    """
    if max_requests <= 0:
        max_requests = _DEFAULT_RPM
    if max_requests <= 0:
        return True, {"limit": 0, "remaining": 0, "window": window_seconds}

    now = time.monotonic()
    cutoff = now - window_seconds

    with _lock:
        _maybe_prune(now)
        timestamps = _buckets.setdefault(client_key, [])
        # Remove expired entries
        timestamps[:] = [t for t in timestamps if t > cutoff]
        count = len(timestamps)

        if count >= max_requests:
            retry_after = timestamps[0] - cutoff if timestamps else window_seconds
            return False, {
                "error": "rate_limit_exceeded",
                "limit": max_requests,
                "remaining": 0,
                "retry_after_seconds": round(retry_after, 1),
                "window": window_seconds,
            }

        timestamps.append(now)
        return True, {
            "limit": max_requests,
            "remaining": max_requests - count - 1,
            "window": window_seconds,
        }


def _maybe_prune(now: float) -> None:
    """Remove buckets that haven't been touched in a while."""
    global _last_prune
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    _last_prune = now
    stale_keys = [k for k, v in _buckets.items() if not v or v[-1] < now - 120]
    for k in stale_keys:
        del _buckets[k]


def reset() -> None:
    """Clear all state (for testing)."""
    with _lock:
        _buckets.clear()
