"""Tests for SupervisorDaemon - 24/7 Autonomous Orchestrator."""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from core.supervisor_daemon import (
    SupervisorDaemon,
    SupervisorMode,
    SupervisorState,
    SupervisorDecision,
    DecisionType,
    get_supervisor_daemon,
    reset_daemon,
)


class TestSupervisorMode:
    def test_observe_mode(self):
        assert SupervisorMode("observe") == SupervisorMode.OBSERVE

    def test_advise_mode(self):
        assert SupervisorMode("advise") == SupervisorMode.ADVISE

    def test_intervene_mode(self):
        assert SupervisorMode("intervene") == SupervisorMode.INTERVENE

    def test_full_auto_mode(self):
        assert SupervisorMode("full_auto") == SupervisorMode.FULL_AUTO


class TestDecisionType:
    def test_all_types(self):
        assert DecisionType.NO_ACTION.value == "no_action"
        assert DecisionType.LOG_ONLY.value == "log_only"
        assert DecisionType.SUGGEST.value == "suggest"
        assert DecisionType.EXECUTE.value == "execute"
        assert DecisionType.ALERT.value == "alert"
        assert DecisionType.RECOVER.value == "recover"


class TestSupervisorDecision:
    def test_default_decision(self):
        d = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.NO_ACTION,
            trigger="test",
            analysis="test analysis",
            action="no action",
        )
        assert d.executed is False
        assert d.result == ""
        assert d.confidence == 0.0


class TestSupervisorDaemonInit:
    def test_default_init(self, tmp_path):
        daemon = SupervisorDaemon(
            project_path=str(tmp_path),
            mode="OBSERVE",
        )
        assert daemon.mode == SupervisorMode.OBSERVE
        assert daemon._running is False
        assert daemon._cycle_count == 0

    def test_full_auto_mode(self, tmp_path):
        daemon = SupervisorDaemon(
            project_path=str(tmp_path),
            mode="FULL_AUTO",
        )
        assert daemon.mode == SupervisorMode.FULL_AUTO

    def test_min_cycle_interval(self, tmp_path):
        daemon = SupervisorDaemon(
            project_path=str(tmp_path),
            cycle_interval_sec=0.1,
        )
        assert daemon.cycle_interval_sec == 1.0  # Min clamped


class TestSupervisorDaemonModeChange:
    def test_set_mode(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        daemon.set_mode("full_auto")
        assert daemon.mode == SupervisorMode.FULL_AUTO

    def test_set_mode_advise(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        daemon.set_mode("advise")
        assert daemon.mode == SupervisorMode.ADVISE


class TestSupervisorDaemonDecisionEngine:
    def test_terminal_error_observe_mode(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        decision = daemon._make_decision("terminal_error", {
            "errors": ["Traceback: NameError"],
        })
        assert decision.decision_type == DecisionType.LOG_ONLY
        assert daemon._errors_detected == 1

    def test_terminal_error_advise_mode(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="ADVISE")
        decision = daemon._make_decision("terminal_error", {
            "errors": ["SyntaxError: invalid syntax"],
        })
        assert decision.decision_type == DecisionType.SUGGEST

    def test_terminal_error_full_auto_mode(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="FULL_AUTO")
        decision = daemon._make_decision("terminal_error", {
            "errors": ["ImportError: no module named 'foo'"],
        })
        assert decision.decision_type == DecisionType.EXECUTE
        assert decision.confidence >= 0.5

    def test_project_issue_full_auto(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="FULL_AUTO")

        # Create mock issues
        from brain.project_supervisor import ProjectIssue
        issues = [
            ProjectIssue(severity="critical", category="security", title="API key exposed",
                         description="Secret found in code"),
        ]
        decision = daemon._make_decision("project_issue", {"issues": issues})
        assert decision.decision_type == DecisionType.EXECUTE

    def test_project_issue_observe_mode(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        from brain.project_supervisor import ProjectIssue
        issues = [
            ProjectIssue(severity="low", category="structure", title="Missing docs",
                         description="No docs"),
        ]
        decision = daemon._make_decision("project_issue", {"issues": issues})
        assert decision.decision_type == DecisionType.LOG_ONLY

    def test_health_degraded(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="FULL_AUTO")
        decision = daemon._make_decision("health_degraded", {"details": "unhealthy"})
        assert decision.decision_type == DecisionType.RECOVER

    def test_scheduled_scan(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="FULL_AUTO")
        decision = daemon._make_decision("scheduled_scan", {})
        assert decision.decision_type == DecisionType.LOG_ONLY

    def test_unknown_trigger(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="FULL_AUTO")
        decision = daemon._make_decision("unknown_xyz", {})
        assert decision.decision_type == DecisionType.NO_ACTION


class TestSupervisorDaemonExecuteDecision:
    def test_execute_no_action(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        decision = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.NO_ACTION,
            trigger="test",
            analysis="none",
            action="none",
        )
        daemon._execute_decision(decision)
        assert not decision.executed

    def test_execute_log_only(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        decision = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.LOG_ONLY,
            trigger="test",
            analysis="test",
            action="test action",
        )
        daemon._execute_decision(decision)
        assert decision.executed is True
        assert decision.result == "logged"

    def test_execute_suggest(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="ADVISE")
        decision = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.SUGGEST,
            trigger="test",
            analysis="test",
            action="suggest something",
        )
        daemon._execute_decision(decision)
        assert decision.executed is True
        assert decision.result == "suggestion_logged"

    def test_execute_blocked_by_mode(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        decision = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.EXECUTE,
            trigger="test",
            analysis="test",
            action="do something",
            confidence=0.9,
        )
        daemon._execute_decision(decision)
        assert decision.result == "skipped_mode_restriction"


class TestSupervisorDaemonCallbacks:
    def test_on_decision_callback(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        received = []
        daemon.on_decision(lambda d: received.append(d))

        decision = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.LOG_ONLY,
            trigger="test",
            analysis="test",
            action="test",
        )
        daemon._execute_decision(decision)
        assert len(received) == 1


class TestSupervisorDaemonStatus:
    def test_get_state(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="FULL_AUTO")
        state = daemon.get_state()
        assert state.mode == "full_auto"
        assert state.running is False
        assert state.cycle_count == 0

    def test_get_status_dict(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path), mode="OBSERVE")
        status = daemon.get_status()
        assert "mode" in status
        assert "running" in status
        assert "cycle_count" in status
        assert "decisions_made" in status
        assert status["mode"] == "observe"

    def test_get_recent_decisions_empty(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path))
        assert daemon.get_recent_decisions() == []

    def test_get_terminal_content_empty(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path))
        assert daemon.get_terminal_content() == ""

    def test_get_project_summary_no_supervisor(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path))
        summary = daemon.get_project_summary()
        assert summary == {}


class TestSupervisorDaemonForceActions:
    def test_force_scan_no_supervisor(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path))
        result = daemon.force_scan()
        assert result["success"] is False

    def test_execute_command_no_controller(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path))
        result = daemon.execute_command("ls")
        assert result["success"] is False

    def test_take_screenshot_no_controller(self, tmp_path):
        daemon = SupervisorDaemon(project_path=str(tmp_path))
        result = daemon.take_screenshot()
        assert result["success"] is False


class TestSupervisorDaemonSingleton:
    def test_get_daemon(self, tmp_path, monkeypatch):
        reset_daemon()
        monkeypatch.setenv("SUPERVISOR_PROJECT_PATH", str(tmp_path))
        d1 = get_supervisor_daemon()
        d2 = get_supervisor_daemon()
        assert d1 is d2
        reset_daemon()

    def test_reset_daemon(self, tmp_path, monkeypatch):
        reset_daemon()
        monkeypatch.setenv("SUPERVISOR_PROJECT_PATH", str(tmp_path))
        d1 = get_supervisor_daemon()
        reset_daemon()
        d2 = get_supervisor_daemon()
        assert d1 is not d2
        reset_daemon()


class TestSupervisorDaemonDecisionLog:
    def test_decision_logged_to_memory(self, tmp_path):
        daemon = SupervisorDaemon(
            project_path=str(tmp_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        )
        decision = SupervisorDecision(
            timestamp="now",
            decision_type=DecisionType.LOG_ONLY,
            trigger="test",
            analysis="test",
            action="test",
        )
        daemon._execute_decision(decision)

        assert len(daemon._decisions) == 1
        assert daemon._decisions[0].trigger == "test"

    def test_decisions_capped_at_200(self, tmp_path):
        daemon = SupervisorDaemon(
            project_path=str(tmp_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        )
        for i in range(250):
            decision = SupervisorDecision(
                timestamp=f"t{i}",
                decision_type=DecisionType.LOG_ONLY,
                trigger=f"test_{i}",
                analysis="a",
                action="a",
            )
            daemon._log_decision(decision)

        assert len(daemon._decisions) <= 200
