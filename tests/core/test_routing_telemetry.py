import json
from datetime import datetime

from src.core import routing_telemetry


def _event_line(index: int, agent: str) -> str:
    payload = {
        "timestamp": datetime.now().isoformat(),
        "agent": agent,
        "seq": index,
        "success": True,
    }
    return json.dumps(payload, ensure_ascii=True)


def test_read_recent_routing_events_tail_and_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_STATE_DIR", str(tmp_path))
    path = routing_telemetry._events_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        for i in range(120):
            agent = "Orion" if i % 2 == 0 else "Echo"
            handle.write(_event_line(i, agent) + "\n")
        handle.write("{invalid json}\n")

    recent = routing_telemetry.read_recent_routing_events(limit=7)
    assert len(recent) == 7
    assert [item["seq"] for item in recent] == [113, 114, 115, 116, 117, 118, 119]

    filtered = routing_telemetry.read_recent_routing_events(limit=4, agent="echo")
    assert len(filtered) == 4
    assert [item["seq"] for item in filtered] == [113, 115, 117, 119]
