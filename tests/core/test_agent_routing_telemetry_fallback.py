from src.core.agent import AsyncAgent
from src.core.model_router import TaskType


class _DummyAgent(AsyncAgent):
    async def process(self, message):
        raise NotImplementedError


def test_emit_routing_telemetry_estimates_usage_when_missing(monkeypatch):
    agent = _DummyAgent(
        name="Echo",
        role="QA",
        model="glm-5",
        api_key="dummy",
    )
    captured = {}

    def _capture(event):
        captured.update(event or {})

    monkeypatch.setattr("src.core.agent.record_routing_event", _capture)

    agent._emit_routing_telemetry(
        task_type=TaskType.TEST_GENERATION,
        complexity=None,
        importance="normal",
        prefer_speed=False,
        prefer_cost=True,
        estimated_tokens=1200,
        chain=["glm-5"],
        attempts=[{"model": "glm-5", "success": True, "latency_ms": 100}],
        result={"content": "ok"},
        duration_ms=100,
        selected_model="glm-5",
    )

    usage = captured.get("usage", {})
    assert int(usage.get("total_tokens", 0) or 0) == 1200
    assert usage.get("source") == "estimated"
    assert float(captured.get("estimated_cost_usd", 0.0) or 0.0) > 0.0
