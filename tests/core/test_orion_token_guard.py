import asyncio

import src.agents.orion as orion_module
from src.agents.orion import Orion
from src.core.message import ProjectContext, TaskResult


def _orion_with_context() -> Orion:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    orion = Orion(agents={})
    orion.context = ProjectContext(project_name="TokenGuard", project_goal="Reduce waste")
    return orion


def test_token_budget_snapshot_levels(monkeypatch):
    orion = _orion_with_context()
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 9.0, "daily_budget_usd": 10.0, "budget_ratio": 0.9},
    )
    soft = orion._get_token_budget_snapshot()
    assert soft["level"] == "soft"

    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 10.5, "daily_budget_usd": 10.0, "budget_ratio": 1.05},
    )
    hard = orion._get_token_budget_snapshot()
    assert hard["level"] == "hard"


def test_should_run_pixel_under_hard_pressure(monkeypatch):
    orion = _orion_with_context()
    orion.iteration = 7
    orion.last_pixel_iteration = 5

    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 10.2, "daily_budget_usd": 10.0, "budget_ratio": 1.02},
    )
    should_run, reason = orion._should_run_pixel()
    assert should_run is False
    assert "hard token pressure" in reason

    orion.last_pixel_iteration = 2
    should_run, reason = orion._should_run_pixel()
    assert should_run is True
    assert "hard-pressure periodic interval" in reason


def test_should_run_nova_uses_hard_pressure_interval(monkeypatch):
    orion = _orion_with_context()
    orion.iteration = 8
    orion.last_nova_iteration = 5

    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 10.2, "daily_budget_usd": 10.0, "budget_ratio": 1.02},
    )
    should_run, reason = orion._should_run_nova()
    assert should_run is False
    assert "hard token pressure cadence" in reason

    orion.last_nova_iteration = 2
    should_run, reason = orion._should_run_nova()
    assert should_run is True
    assert "nova cadence interval reached" in reason


def test_runtime_token_hints_prefer_cost_for_non_critical(monkeypatch):
    orion = _orion_with_context()
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 9.2, "daily_budget_usd": 10.0, "budget_ratio": 0.92},
    )
    non_critical = orion._runtime_token_hints(critical=False)
    critical = orion._runtime_token_hints(critical=True)
    assert non_critical["prefer_cost"] is True
    assert critical["prefer_cost"] is False


def test_echo_skip_reports_required_interval_under_hard_pressure(monkeypatch):
    orion = _orion_with_context()
    orion.agents["echo"] = object()
    orion.iteration = 7
    orion.last_echo_iteration = 3
    orion.context.files["app.js"] = "console.log('stable');"
    orion.last_tested_fingerprint = orion._files_fingerprint()

    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 10.3, "daily_budget_usd": 10.0, "budget_ratio": 1.03},
    )

    result = asyncio.run(orion._dispatch_echo_or_skip())
    assert result.success is True
    assert result.output.get("skipped") is True
    assert result.output.get("token_pressure_level") == "hard"
    assert int(result.output.get("required_interval", 0)) >= orion.echo_hard_interval


def test_security_audit_respects_token_guard_when_fingerprint_unchanged(monkeypatch):
    orion = _orion_with_context()
    orion.agents["cipher"] = object()
    orion.iteration = 12
    orion.last_full_security_audit_iteration = 10
    orion.context.files["app.js"] = "console.log('stable');"
    fingerprint = orion._files_fingerprint()
    orion.last_security_fingerprint = fingerprint

    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 11.0, "daily_budget_usd": 10.0, "budget_ratio": 1.1},
    )

    nova_result = TaskResult(success=True, agent="Nova", output={"generation_flags": {}}, score=8.0)
    should_run, reason = orion._should_run_security_audit(nova_result)
    assert should_run is False
    assert reason.startswith("token_guard_hard")


def test_token_economics_snapshot_aggregates_cost_and_efficiency(monkeypatch):
    orion = _orion_with_context()
    orion.history = [
        {"deployed": True, "score": 8.1},
        {"deployed": False, "score": 7.2},
        {"deployed": True, "score": 8.8},
    ]
    monkeypatch.setattr(
        orion_module,
        "read_recent_routing_events",
        lambda limit=120: [
            {
                "agent": "Nova",
                "success": True,
                "estimated_cost_usd": 0.12,
                "usage": {"total_tokens": 4000},
            },
            {
                "agent": "Echo",
                "success": True,
                "estimated_cost_usd": 0.03,
                "usage": {"total_tokens": 1100},
            },
            {
                "agent": "Pixel",
                "success": False,
                "estimated_cost_usd": 0.04,
                "usage": {"total_tokens": 900},
            },
        ],
    )
    snapshot = orion._token_economics_snapshot(event_limit=50, cycle_window=10)
    assert snapshot["calls"]["total"] == 3
    assert snapshot["calls"]["success"] == 2
    assert snapshot["tokens"]["total"] == 6000
    assert abs(float(snapshot["cost"]["total_usd"]) - 0.19) < 1e-9
    assert snapshot["productivity"]["deploy_count"] == 2
    assert snapshot["cost"]["per_deploy_usd"] is not None


def test_token_economics_falls_back_to_router_usage_when_events_missing_usage(monkeypatch):
    orion = _orion_with_context()
    orion.history = [{"deployed": True, "score": 8.0}]
    monkeypatch.setattr(
        orion_module,
        "read_recent_routing_events",
        lambda limit=120: [{"agent": "Nova", "success": True, "usage": {}, "estimated_cost_usd": 0.0}],
    )
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {
            "total_cost_usd": 12.5,
            "daily_budget_usd": 15.0,
            "budget_ratio": 0.8333,
            "models": {
                "glm-5": {"tokens": 550000, "cost_usd": 11.0, "calls": 320},
                "glm-4.7": {"tokens": 150000, "cost_usd": 1.5, "calls": 180},
            },
        },
    )
    snapshot = orion._token_economics_snapshot(event_limit=20, cycle_window=5)
    assert snapshot["metric_source"] == "router_usage"
    assert snapshot["tokens"]["total"] == 700000
    assert abs(float(snapshot["cost"]["total_usd"]) - 12.5) < 1e-9
    assert "glm-5" in snapshot["by_model"]


def test_token_policy_tune_switches_to_economy_then_balanced(monkeypatch):
    orion = _orion_with_context()
    orion.iteration = 10
    orion.history = [{"deployed": False, "score": 6.8}, {"deployed": False, "score": 6.6}]
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 9.7, "daily_budget_usd": 10.0, "budget_ratio": 0.97},
    )
    monkeypatch.setattr(orion_module, "read_recent_routing_events", lambda limit=120: [])
    tuned = orion._maybe_tune_token_policy()
    assert tuned["mode"] == "economy"
    assert orion.nova_stable_interval >= orion.nova_soft_interval
    assert orion.echo_interval >= orion.echo_soft_interval

    orion.iteration += orion.token_policy_tune_interval + 1
    orion.history = [
        {"deployed": True, "score": 8.1},
        {"deployed": True, "score": 8.2},
        {"deployed": False, "score": 7.9},
    ]
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 5.2, "daily_budget_usd": 10.0, "budget_ratio": 0.52},
    )
    retuned = orion._maybe_tune_token_policy()
    assert retuned["mode"] == "balanced"
    assert orion.nova_stable_interval == orion._base_nova_stable_interval
    assert orion.echo_interval == orion._base_echo_interval


def test_hard_cap_state_and_nova_cadence(monkeypatch):
    orion = _orion_with_context()
    orion.iteration = 10
    orion.last_nova_iteration = 5
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 13.0, "daily_budget_usd": 10.0, "budget_ratio": 1.3},
    )
    cap = orion._hard_cap_state()
    assert cap["active"] is True
    should_run, reason = orion._should_run_nova()
    assert should_run is False
    assert "hard cap cadence" in reason


def test_echo_skip_contains_hard_cap_flag(monkeypatch):
    orion = _orion_with_context()
    orion.agents["echo"] = object()
    orion.iteration = 8
    orion.last_echo_iteration = 6
    orion.context.files["app.js"] = "console.log('stable');"
    orion.last_tested_fingerprint = orion._files_fingerprint()
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 13.0, "daily_budget_usd": 10.0, "budget_ratio": 1.3},
    )
    result = asyncio.run(orion._dispatch_echo_or_skip())
    assert result.output.get("hard_cap_active") is True


def test_token_policy_tune_uses_hard_cap_multiplier(monkeypatch):
    orion = _orion_with_context()
    orion.iteration = 12
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 13.0, "daily_budget_usd": 10.0, "budget_ratio": 1.3},
    )
    monkeypatch.setattr(orion_module, "read_recent_routing_events", lambda limit=120: [])
    tuned = orion._maybe_tune_token_policy()
    assert tuned["mode"] == "economy"
    assert tuned["hard_cap_active"] is True
    assert orion.nova_stable_interval >= orion.nova_hard_interval * orion.token_hard_cap_interval_multiplier
    assert orion.echo_interval >= orion.echo_hard_interval * orion.token_hard_cap_interval_multiplier


def test_next_iteration_delay_increases_under_hard_cap(monkeypatch):
    orion = _orion_with_context()
    monkeypatch.setattr(
        orion.router,
        "get_usage_summary",
        lambda: {"total_cost_usd": 13.0, "daily_budget_usd": 10.0, "budget_ratio": 1.3},
    )
    delay = orion._next_iteration_delay_sec()
    assert delay >= 14
