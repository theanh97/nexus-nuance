"""Memory governance utilities for long-running learning."""

import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List


class MemoryGovernor:
    def __init__(self):
        self.recent_signatures: Dict[str, str] = {}
        self.ttl_days_by_category = {
            "learning_event": 30,
            "proposal": 60,
            "evidence": 180,
            "default": 90,
        }

    def signature(self, payload: Dict[str, Any]) -> str:
        base = "|".join([
            str(payload.get("source", "")),
            str(payload.get("event_type", payload.get("type", ""))),
            str(payload.get("content", payload.get("title", "")))[:180],
        ]).lower()
        return hashlib.md5(base.encode("utf-8")).hexdigest()[:16]

    def should_keep(self, payload: Dict[str, Any], category: str = "default") -> bool:
        sig = self.signature(payload)
        now = datetime.now().isoformat()
        prev = self.recent_signatures.get(sig)
        self.recent_signatures[sig] = now
        # Deduplicate immediate repeats
        if prev and payload.get("dedup_strict", True):
            return False
        return True

    def prune_by_ttl(self, rows: List[Dict[str, Any]], category: str = "default") -> List[Dict[str, Any]]:
        ttl_days = int(self.ttl_days_by_category.get(category, self.ttl_days_by_category["default"]))
        cutoff = datetime.now() - timedelta(days=ttl_days)
        out: List[Dict[str, Any]] = []
        for row in rows:
            ts = row.get("ts") or row.get("timestamp") or row.get("created_at")
            if not ts:
                out.append(row)
                continue
            try:
                dt = datetime.fromisoformat(str(ts))
            except (ValueError, TypeError):
                out.append(row)
                continue
            if dt >= cutoff:
                out.append(row)
        return out


_governor = MemoryGovernor()


def get_memory_governor() -> MemoryGovernor:
    return _governor
