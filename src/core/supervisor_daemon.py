"""
Supervisor Daemon - 24/7 Autonomous Orchestrator
=================================================

The central nervous system of NEXUS's 24/7 autonomous operation:
- Continuous screen monitoring via ScreenMonitor
- Project understanding via ProjectSupervisor
- Decision engine: Observe → Analyze → Decide → Act
- Computer control via ComputerController
- Action execution via ActionExecutor
- Health monitoring via ProactiveMonitor
- Self-healing and recovery

Operating Modes:
- OBSERVE: Watch only, no actions (logging + alerts)
- ADVISE: Watch + suggest actions (but don't execute)
- INTERVENE: Watch + auto-fix critical issues only
- FULL_AUTO: Watch + decide + act on everything

Usage:
    from src.core.supervisor_daemon import SupervisorDaemon

    daemon = SupervisorDaemon(
        project_path="/path/to/project",
        mode="FULL_AUTO",
    )
    daemon.start()   # Runs 24/7 in background
    daemon.stop()    # Graceful shutdown
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.nexus_logger import get_logger

logger = get_logger(__name__)


class SupervisorMode(Enum):
    OBSERVE = "observe"
    ADVISE = "advise"
    INTERVENE = "intervene"
    FULL_AUTO = "full_auto"


class DecisionType(Enum):
    NO_ACTION = "no_action"
    LOG_ONLY = "log_only"
    SUGGEST = "suggest"
    EXECUTE = "execute"
    ALERT = "alert"
    RECOVER = "recover"


@dataclass
class SupervisorDecision:
    """A decision made by the supervisor."""
    timestamp: str
    decision_type: DecisionType
    trigger: str          # What triggered the decision
    analysis: str         # What was analyzed
    action: str           # What action to take (or suggestion)
    executed: bool = False
    result: str = ""
    confidence: float = 0.0


@dataclass
class SupervisorState:
    """Current state of the supervisor daemon."""
    mode: str
    running: bool
    started_at: Optional[str]
    uptime_sec: float
    cycle_count: int
    decisions_made: int
    actions_executed: int
    errors_detected: int
    recoveries: int
    project_path: str
    current_focus: str  # What the supervisor is currently focused on
    screen_monitoring: bool
    terminal_monitoring: bool


class SupervisorDaemon:
    """
    24/7 Autonomous Supervisor Daemon.

    Orchestrates screen monitoring, project analysis, decision making,
    and action execution in a continuous loop.
    """

    def __init__(
        self,
        project_path: str = ".",
        mode: str = "FULL_AUTO",
        cycle_interval_sec: float = 5.0,
        screen_interval_sec: float = 3.0,
        project_scan_interval_sec: float = 60.0,
        terminal_check_interval_sec: float = 5.0,
        decision_log_path: str = "data/supervisor/decisions.jsonl",
    ):
        self.project_path = Path(project_path).resolve()
        self.mode = SupervisorMode(mode.lower())
        self.cycle_interval_sec = max(1.0, cycle_interval_sec)
        self.screen_interval_sec = screen_interval_sec
        self.project_scan_interval_sec = project_scan_interval_sec
        self.terminal_check_interval_sec = terminal_check_interval_sec
        self.decision_log_path = Path(decision_log_path)
        self.decision_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Components (lazy-initialized)
        self._screen_monitor = None
        self._project_supervisor = None
        self._computer_controller = None
        self._action_executor = None
        self._proactive_monitor = None
        self._openclaw_bridge = None

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._started_at: Optional[datetime] = None
        self._cycle_count = 0
        self._last_project_scan: Optional[datetime] = None
        self._last_terminal_check: Optional[datetime] = None
        self._current_focus = "initializing"

        # Metrics
        self._decisions: List[SupervisorDecision] = []
        self._decisions_made = 0
        self._actions_executed = 0
        self._errors_detected = 0
        self._recoveries = 0

        # Event callbacks
        self._on_decision_callbacks: List[Callable[[SupervisorDecision], None]] = []

        # Recent observations (for context)
        self._recent_terminal: str = ""
        self._recent_errors: List[str] = []
        self._active_app: str = ""

    # ── Component Initialization ──────────────────────────────────

    def _init_components(self) -> None:
        """Lazy-initialize all components."""
        # Screen Monitor
        try:
            from core.screen_monitor import ScreenMonitor
            self._screen_monitor = ScreenMonitor(
                interval_sec=self.screen_interval_sec,
                capture_screenshots=True,
                read_terminal=True,
                detect_active_app=True,
            )
            self._screen_monitor.on_change(self._on_screen_change)
            logger.info("ScreenMonitor initialized")
        except Exception as e:
            logger.warning(f"ScreenMonitor init failed: {e}")

        # Project Supervisor
        try:
            from brain.project_supervisor import ProjectSupervisor
            self._project_supervisor = ProjectSupervisor(str(self.project_path))
            logger.info(f"ProjectSupervisor initialized for {self.project_path}")
        except Exception as e:
            logger.warning(f"ProjectSupervisor init failed: {e}")

        # Computer Controller
        try:
            from core.computer_controller import get_computer_controller
            self._computer_controller = get_computer_controller()
            logger.info(f"ComputerController initialized (caps: {self._computer_controller.get_capabilities()})")
        except Exception as e:
            logger.warning(f"ComputerController init failed: {e}")

        # OpenClaw Bridge (primary control interface)
        try:
            from core.openclaw_bridge import OpenClawBridge
            self._openclaw_bridge = OpenClawBridge()
            logger.info(f"OpenClawBridge initialized (CLI: {self._openclaw_bridge.cli_available})")
        except Exception as e:
            logger.warning(f"OpenClawBridge init failed: {e}")

        # Action Executor
        try:
            from brain.action_executor import get_executor
            self._action_executor = get_executor()
            logger.info("ActionExecutor initialized")
        except Exception as e:
            logger.warning(f"ActionExecutor init failed: {e}")

        # Proactive Monitor
        try:
            from core.proactive_monitor import get_monitor
            self._proactive_monitor = get_monitor()
            logger.info("ProactiveMonitor initialized")
        except Exception as e:
            logger.warning(f"ProactiveMonitor init failed: {e}")

    # ── Screen Change Callback ────────────────────────────────────

    def _on_screen_change(self, event) -> None:
        """Called when screen state changes."""
        try:
            if event.event_type == "terminal_changed":
                state = event.screen_state
                if state and state.terminal_content:
                    self._recent_terminal = state.terminal_content

            if event.event_type == "app_switched":
                self._active_app = event.details.get("active_app", "")
                logger.debug(f"App switched to: {self._active_app}")

        except Exception as e:
            logger.debug(f"Screen change callback error: {e}")

    # ── Decision Engine ───────────────────────────────────────────

    def _make_decision(self, trigger: str, context: Dict[str, Any]) -> SupervisorDecision:
        """Core decision engine: analyze context and decide what to do."""
        now = datetime.now().isoformat()

        decision = SupervisorDecision(
            timestamp=now,
            decision_type=DecisionType.NO_ACTION,
            trigger=trigger,
            analysis="",
            action="",
        )

        # Analyze based on trigger type
        if trigger == "terminal_error":
            errors = context.get("errors", [])
            decision.analysis = f"Terminal errors detected: {len(errors)} error(s)"

            if self.mode == SupervisorMode.OBSERVE:
                decision.decision_type = DecisionType.LOG_ONLY
                decision.action = f"Logged {len(errors)} terminal errors"
            elif self.mode == SupervisorMode.ADVISE:
                decision.decision_type = DecisionType.SUGGEST
                decision.action = f"Suggest investigating: {errors[0][:100] if errors else 'unknown'}"
            elif self.mode in (SupervisorMode.INTERVENE, SupervisorMode.FULL_AUTO):
                decision.decision_type = DecisionType.EXECUTE
                decision.action = f"Auto-investigate terminal error: {errors[0][:100] if errors else 'unknown'}"
                decision.confidence = 0.7

            self._errors_detected += len(errors)

        elif trigger == "project_issue":
            issues = context.get("issues", [])
            critical = [i for i in issues if i.severity in ("critical", "high")]
            decision.analysis = f"Project issues: {len(issues)} total, {len(critical)} critical/high"

            if critical and self.mode == SupervisorMode.FULL_AUTO:
                decision.decision_type = DecisionType.EXECUTE
                decision.action = f"Fix critical issue: {critical[0].title}"
                decision.confidence = 0.6
            elif critical:
                decision.decision_type = DecisionType.ALERT
                decision.action = f"Alert: {len(critical)} critical issues found"
            else:
                decision.decision_type = DecisionType.LOG_ONLY
                decision.action = f"Logged {len(issues)} minor issues"

        elif trigger == "health_degraded":
            decision.analysis = f"System health degraded: {context.get('details', 'unknown')}"
            decision.decision_type = DecisionType.RECOVER
            decision.action = "Trigger recovery"
            self._recoveries += 1

        elif trigger == "scheduled_scan":
            decision.analysis = "Scheduled project scan completed"
            decision.decision_type = DecisionType.LOG_ONLY
            decision.action = "Scan complete, no action needed"

        else:
            decision.analysis = f"Unknown trigger: {trigger}"
            decision.decision_type = DecisionType.NO_ACTION

        self._decisions_made += 1
        return decision

    def _execute_decision(self, decision: SupervisorDecision) -> None:
        """Execute a supervisor decision."""
        if decision.decision_type == DecisionType.NO_ACTION:
            return

        if decision.decision_type == DecisionType.LOG_ONLY:
            logger.info(f"[Supervisor] {decision.action}")
            decision.executed = True
            decision.result = "logged"
            self._log_decision(decision)
            return

        if decision.decision_type == DecisionType.SUGGEST:
            logger.info(f"[Supervisor SUGGEST] {decision.action}")
            decision.executed = True
            decision.result = "suggestion_logged"
            self._log_decision(decision)
            return

        if decision.decision_type == DecisionType.ALERT:
            logger.warning(f"[Supervisor ALERT] {decision.action}")
            decision.executed = True
            decision.result = "alert_sent"
            self._log_decision(decision)
            return

        if decision.decision_type == DecisionType.EXECUTE:
            if self.mode not in (SupervisorMode.INTERVENE, SupervisorMode.FULL_AUTO):
                decision.result = "skipped_mode_restriction"
                return

            logger.info(f"[Supervisor EXECUTE] {decision.action}")

            # Execute via ActionExecutor
            if self._action_executor and decision.confidence >= 0.5:
                try:
                    # Parse action and execute
                    result = self._execute_action_from_decision(decision)
                    decision.executed = True
                    decision.result = result
                    self._actions_executed += 1
                except Exception as e:
                    decision.result = f"execution_failed: {e}"
                    logger.error(f"Decision execution failed: {e}")
            else:
                decision.result = "low_confidence_or_no_executor"

        if decision.decision_type == DecisionType.RECOVER:
            logger.warning(f"[Supervisor RECOVER] {decision.action}")
            if self._proactive_monitor:
                health = self._proactive_monitor.check_health()
                incident = self._proactive_monitor.detect_incident(health)
                if incident:
                    self._proactive_monitor.trigger_recovery(incident)
            decision.executed = True
            decision.result = "recovery_triggered"

        # Log decision
        self._log_decision(decision)

    def _execute_action_from_decision(self, decision: SupervisorDecision) -> str:
        """Convert a decision into an actionable execution via OpenClaw."""
        action = decision.action.lower()

        # 1. Terminal investigation - use OpenClaw bridge
        if "investigate" in action and "terminal" in action:
            if self._openclaw_bridge:
                content = self._openclaw_bridge.read_terminal()
                if content:
                    return f"Terminal content read via OpenClaw ({len(content)} chars)"
            # Fallback to ComputerController
            if self._computer_controller:
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    content = loop.run_until_complete(
                        self._computer_controller.get_terminal_content()
                    )
                    loop.close()
                    return f"Terminal content read ({len(content)} chars)"
                except Exception as e:
                    return f"Terminal read failed: {e}"

        # 2. Send commands to terminal via OpenClaw
        if "send" in action and "terminal" in action:
            if self._openclaw_bridge:
                # Extract command from decision
                result = self._openclaw_bridge.send_to_terminal(decision.action)
                return f"Sent to terminal: {result}"

        # 3. Browser operations via OpenClaw
        if "screenshot" in action or "capture" in action:
            if self._openclaw_bridge:
                result = self._openclaw_bridge.browser_screenshot()
                return f"Screenshot: {result.get('success', False)}"

        if "navigate" in action:
            if self._openclaw_bridge:
                result = self._openclaw_bridge.browser_navigate(decision.action)
                return f"Navigate: {result.get('success', False)}"

        # 4. Dashboard flow commands
        if "dashboard" in action or "flow" in action:
            if self._openclaw_bridge:
                result = self._openclaw_bridge.send_dashboard_command(decision.action)
                return f"Dashboard flow: {result.get('success', False)}"

        # 5. Action executor for code/file operations
        if "fix" in action and self._action_executor:
            return f"Fix identified: {decision.action}"

        # 6. Queue command for async execution
        if self._openclaw_bridge:
            result = self._openclaw_bridge.enqueue_command("supervisor_action", {
                "action": decision.action,
                "trigger": decision.trigger,
            })
            return f"Queued: {result.get('success', False)}"

        return "action_not_mapped"

    def _log_decision(self, decision: SupervisorDecision) -> None:
        """Log decision to JSONL file."""
        try:
            with open(self.decision_log_path, "a", encoding="utf-8") as f:
                data = {
                    "timestamp": decision.timestamp,
                    "type": decision.decision_type.value,
                    "trigger": decision.trigger,
                    "analysis": decision.analysis,
                    "action": decision.action,
                    "executed": decision.executed,
                    "result": decision.result,
                    "confidence": decision.confidence,
                }
                f.write(json.dumps(data) + "\n")
        except Exception:
            pass

        # Keep in memory
        with self._lock:
            self._decisions.append(decision)
            if len(self._decisions) > 200:
                self._decisions = self._decisions[-200:]

        # Notify callbacks
        for cb in self._on_decision_callbacks:
            try:
                cb(decision)
            except Exception:
                pass

    # ── Main Supervisor Loop ──────────────────────────────────────

    def _supervisor_loop(self) -> None:
        """Main 24/7 supervisor loop."""
        logger.info(
            f"SupervisorDaemon started | mode={self.mode.value} | "
            f"project={self.project_path} | cycle={self.cycle_interval_sec}s"
        )

        while self._running:
            try:
                self._cycle_count += 1
                self._current_focus = "observing"

                # ── Step 1: Read terminal via OpenClaw + check for errors ─
                now = datetime.now()
                if (
                    self._last_terminal_check is None
                    or (now - self._last_terminal_check).total_seconds() >= self.terminal_check_interval_sec
                ):
                    self._current_focus = "reading_terminal"
                    self._last_terminal_check = now

                    # Read terminal via OpenClaw bridge (primary) or ScreenMonitor (fallback)
                    if self._openclaw_bridge and not self._recent_terminal:
                        try:
                            self._recent_terminal = self._openclaw_bridge.read_terminal()
                        except Exception:
                            pass

                    if self._recent_terminal and self._project_supervisor:
                        self._current_focus = "analyzing_terminal"
                        analysis = self._project_supervisor.analyze_terminal_output(self._recent_terminal)
                        if analysis["has_errors"]:
                            decision = self._make_decision("terminal_error", {
                                "errors": analysis["error_lines"],
                                "terminal": self._recent_terminal[-500:],
                            })
                            self._execute_decision(decision)

                # ── Step 2: Periodic project scan ─────────────────
                if (
                    self._project_supervisor
                    and (
                        self._last_project_scan is None
                        or (now - self._last_project_scan).total_seconds() >= self.project_scan_interval_sec
                    )
                ):
                    self._current_focus = "scanning_project"
                    self._last_project_scan = now

                    snapshot = self._project_supervisor.scan()
                    issues = self._project_supervisor.analyze()

                    if issues:
                        decision = self._make_decision("project_issue", {
                            "issues": issues,
                            "snapshot": snapshot,
                        })
                        self._execute_decision(decision)
                    else:
                        decision = self._make_decision("scheduled_scan", {})
                        self._execute_decision(decision)

                # ── Step 3: Health check ──────────────────────────
                if self._proactive_monitor and self._cycle_count % 10 == 0:
                    self._current_focus = "health_check"
                    health = self._proactive_monitor.check_health()
                    if health["overall"] != "healthy":
                        decision = self._make_decision("health_degraded", {
                            "details": health["overall"],
                            "issues": health.get("issues", []),
                        })
                        self._execute_decision(decision)

                self._current_focus = "idle"

            except Exception as e:
                logger.error(f"Supervisor cycle error: {e}")
                self._errors_detected += 1

            time.sleep(self.cycle_interval_sec)

        logger.info("SupervisorDaemon stopped")

    # ── Start / Stop ──────────────────────────────────────────────

    def start(self) -> None:
        """Start the supervisor daemon."""
        if self._running:
            logger.warning("SupervisorDaemon already running")
            return

        # Initialize components
        self._init_components()

        # Start screen monitor
        if self._screen_monitor:
            self._screen_monitor.start()

        # Start proactive monitor
        if self._proactive_monitor:
            self._proactive_monitor.start()

        # Start main loop
        self._running = True
        self._started_at = datetime.now()
        self._thread = threading.Thread(
            target=self._supervisor_loop,
            daemon=True,
            name="supervisor-daemon",
        )
        self._thread.start()
        logger.info("SupervisorDaemon fully started")

    def stop(self) -> None:
        """Stop the supervisor daemon gracefully."""
        logger.info("SupervisorDaemon shutting down...")
        self._running = False

        if self._screen_monitor:
            self._screen_monitor.stop()

        if self._proactive_monitor:
            self._proactive_monitor.stop()

        if self._thread:
            self._thread.join(timeout=15)
            self._thread = None

        logger.info("SupervisorDaemon shut down complete")

    # ── Mode Control ──────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Change operating mode at runtime."""
        self.mode = SupervisorMode(mode.lower())
        logger.info(f"SupervisorDaemon mode changed to: {self.mode.value}")

    # ── Callbacks ─────────────────────────────────────────────────

    def on_decision(self, callback: Callable[[SupervisorDecision], None]) -> None:
        """Register a callback for supervisor decisions."""
        self._on_decision_callbacks.append(callback)

    # ── Status & Queries ──────────────────────────────────────────

    def get_state(self) -> SupervisorState:
        """Get current supervisor state."""
        uptime = 0.0
        if self._started_at:
            uptime = (datetime.now() - self._started_at).total_seconds()

        return SupervisorState(
            mode=self.mode.value,
            running=self._running,
            started_at=self._started_at.isoformat() if self._started_at else None,
            uptime_sec=uptime,
            cycle_count=self._cycle_count,
            decisions_made=self._decisions_made,
            actions_executed=self._actions_executed,
            errors_detected=self._errors_detected,
            recoveries=self._recoveries,
            project_path=str(self.project_path),
            current_focus=self._current_focus,
            screen_monitoring=self._screen_monitor.running if self._screen_monitor else False,
            terminal_monitoring=bool(self._recent_terminal),
        )

    def get_status(self) -> Dict[str, Any]:
        """Get status as dict (for API)."""
        state = self.get_state()
        result = {
            "mode": state.mode,
            "running": state.running,
            "started_at": state.started_at,
            "uptime_sec": round(state.uptime_sec, 1),
            "cycle_count": state.cycle_count,
            "decisions_made": state.decisions_made,
            "actions_executed": state.actions_executed,
            "errors_detected": state.errors_detected,
            "recoveries": state.recoveries,
            "project_path": state.project_path,
            "current_focus": state.current_focus,
            "screen_monitoring": state.screen_monitoring,
            "terminal_monitoring": state.terminal_monitoring,
        }
        # Add OpenClaw bridge status
        if self._openclaw_bridge:
            result["openclaw"] = self._openclaw_bridge.get_status()
        return result

    def get_recent_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent supervisor decisions."""
        with self._lock:
            return [
                {
                    "timestamp": d.timestamp,
                    "type": d.decision_type.value,
                    "trigger": d.trigger,
                    "analysis": d.analysis,
                    "action": d.action,
                    "executed": d.executed,
                    "result": d.result,
                    "confidence": d.confidence,
                }
                for d in self._decisions[-limit:]
            ]

    def get_terminal_content(self) -> str:
        """Get the latest terminal content observed."""
        return self._recent_terminal

    def get_project_summary(self) -> Dict[str, Any]:
        """Get project summary from supervisor."""
        if self._project_supervisor:
            return self._project_supervisor.get_summary()
        return {}

    # ── Manual Actions ────────────────────────────────────────────

    def force_scan(self) -> Dict[str, Any]:
        """Force an immediate project scan."""
        if self._project_supervisor:
            snapshot = self._project_supervisor.scan()
            return {
                "success": True,
                "files": snapshot.total_files,
                "lines": snapshot.total_lines,
                "languages": snapshot.languages,
            }
        return {"success": False, "error": "ProjectSupervisor not initialized"}

    def execute_command(self, command: str) -> Dict[str, Any]:
        """Execute a command via ComputerController."""
        if not self._computer_controller:
            return {"success": False, "error": "ComputerController not available"}

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._computer_controller.run_terminal_command(command)
            )
            return result
        finally:
            loop.close()

    def take_screenshot(self) -> Dict[str, Any]:
        """Take an immediate screenshot."""
        if not self._computer_controller:
            return {"success": False, "error": "ComputerController not available"}

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            path = loop.run_until_complete(
                self._computer_controller.take_screenshot()
            )
            return {"success": True, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            loop.close()

    def type_text(self, text: str) -> Dict[str, Any]:
        """Type text via ComputerController."""
        if not self._computer_controller:
            return {"success": False, "error": "ComputerController not available"}

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._computer_controller.type_text(text)
            )
            return {"success": result}
        finally:
            loop.close()

    def click_at(self, x: int, y: int) -> Dict[str, Any]:
        """Click at coordinates via ComputerController."""
        if not self._computer_controller:
            return {"success": False, "error": "ComputerController not available"}

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._computer_controller.click(x, y)
            )
            return {"success": result}
        finally:
            loop.close()

    def press_key(self, key: str) -> Dict[str, Any]:
        """Press a key via ComputerController."""
        if not self._computer_controller:
            return {"success": False, "error": "ComputerController not available"}

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._computer_controller.press_key(key)
            )
            return {"success": result}
        finally:
            loop.close()

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """Press hotkey combination via ComputerController."""
        if not self._computer_controller:
            return {"success": False, "error": "ComputerController not available"}

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._computer_controller.hotkey(*keys)
            )
            return {"success": result}
        finally:
            loop.close()


# ── Singleton ─────────────────────────────────────────────────────

_daemon: Optional[SupervisorDaemon] = None
_daemon_lock = threading.Lock()


def get_supervisor_daemon(**kwargs) -> SupervisorDaemon:
    """Get or create the singleton SupervisorDaemon."""
    global _daemon
    with _daemon_lock:
        if _daemon is None:
            project = kwargs.pop("project_path", os.getenv("SUPERVISOR_PROJECT_PATH", "."))
            mode = kwargs.pop("mode", os.getenv("SUPERVISOR_MODE", "FULL_AUTO"))
            _daemon = SupervisorDaemon(project_path=project, mode=mode, **kwargs)
        return _daemon


def reset_daemon() -> None:
    """Reset singleton (for testing)."""
    global _daemon
    with _daemon_lock:
        if _daemon and _daemon._running:
            _daemon.stop()
        _daemon = None


# ── CLI Entry Point ───────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    project = sys.argv[1] if len(sys.argv) > 1 else "."
    mode = sys.argv[2] if len(sys.argv) > 2 else "FULL_AUTO"

    daemon = SupervisorDaemon(project_path=project, mode=mode)

    def shutdown(sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    daemon.start()

    # Keep main thread alive
    try:
        while daemon._running:
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()
