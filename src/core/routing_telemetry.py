"""
Routing telemetry helpers.
Stores model-routing events as newline-delimited JSON for lightweight append/read.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _state_dir() -> Path:
    base = Path(os.getenv("ROUTER_STATE_DIR", "data/state"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _events_path() -> Path:
    return _state_dir() / f"routing_events_{datetime.now().strftime('%Y%m%d')}.jsonl"


def record_routing_event(event: Dict[str, Any]) -> None:
    """Append one routing event. Fail-open to avoid blocking agent execution."""
    payload = dict(event or {})
    payload.setdefault("timestamp", datetime.now().isoformat())
    try:
        with open(_events_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        # Telemetry must never break runtime calls.
        return


def read_recent_routing_events(limit: int = 40, agent: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read recent routing events from today's JSONL file."""
    path = _events_path()
    if not path.exists():
        return []

    lines: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    events: List[Dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue

        if agent and str(item.get("agent", "")).lower() != agent.lower():
            continue

        events.append(item)
        if len(events) >= max(1, int(limit)):
            break

    return list(reversed(events))
