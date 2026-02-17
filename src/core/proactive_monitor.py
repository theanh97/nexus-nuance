"""
Proactive Monitoring & Self-Healing System
===========================================

Automatically monitors system health, detects issues, and triggers recovery:
- Health checks for all agents
- Automatic recovery from common failures
- Escalation to humans when needed
- Learning from past incidents

Usage:
    from src.core.proactive_monitor import get_monitor, start_monitoring

    # Start monitoring (runs in background)
    start_monitoring()

    # Check health
    health = get_monitor().check_health()

    # Trigger recovery
    get_monitor().trigger_recovery("agent_stuck", {"agent": "nova"})
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import json


@dataclass
class HealthCheck:
    component: str
    status: str  # healthy, degraded, unhealthy
    last_check: str
    message: str
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Incident:
    id: str
    type: str
    component: str
    severity: str  # low, medium, high, critical
    detected_at: str
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None
    auto_recovered: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class ProactiveMonitor:
    """Monitors system health and triggers auto-recovery."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

    SEVERITY_LEVELS = ["low", "medium", "high", "critical"]

    RECOVERY_ACTIONS = {
        "agent_stuck": "restart_agent",
        "high_error_rate": "reduce_load",
        "memory_pressure": "cleanup_cache",
        "task_timeout": "kill_and_restart",
        "orion_offline": "notify_humans",
        "queue_backed_up": "scale_workers",
    }

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = Path(state_path or "data/proactive_monitor.json")
        self._lock = threading.RLock()

        # Configuration
        self.check_interval_sec = max(10, int(os.getenv("MONITOR_CHECK_INTERVAL_SEC", "30")))
        self.auto_recovery_enabled = os.getenv("MONITOR_AUTO_RECOVERY", "true").lower() == "true"
        self.max_recovery_attempts = int(os.getenv("MONITOR_MAX_RECOVERY_ATTEMPTS", "3"))

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._health_checks: Dict[str, HealthCheck] = {}
        self._incidents: List[Incident] = []
        self._recovery_attempts: Dict[str, int] = {}
        self._last_check_at: Optional[datetime] = None

        # Callbacks
        self._recovery_handlers: Dict[str, Callable] = {}
        self._health_checkers: Dict[str, Callable] = {}

        # Metrics
        self.total_checks = 0
        self.total_incidents = 0
        self.auto_recoveries = 0
        self.human_escalations = 0

    def _now(self) -> datetime:
        return datetime.now()

    def _now_iso(self) -> str:
        return datetime.now().isoformat()

    def register_health_checker(self, component: str, checker: Callable[[], HealthCheck]) -> None:
        """Register a health check function for a component."""
        self._health_checkers[component] = checker

    def register_recovery_handler(self, incident_type: str, handler: Callable[[Incident], bool]) -> None:
        """Register a recovery handler for an incident type."""
        self._recovery_handlers[incident_type] = handler

    def check_health(self) -> Dict[str, Any]:
        """Run all health checks and return status."""
        results: Dict[str, Any] = {
            "timestamp": self._now_iso(),
            "overall": self.HEALTHY,
            "components": {},
            "issues": [],
        }

        for component, checker in self._health_checkers.items():
            try:
                check = checker()
                self._health_checks[component] = check
                results["components"][component] = {
                    "status": check.status,
                    "message": check.message,
                    "metrics": check.metrics,
                }

                if check.status == self.UNHEALTHY:
                    results["overall"] = self.UNHEALTHY
                    results["issues"].append({
                        "component": component,
                        "severity": "high",
                        "message": check.message,
                    })
                elif check.status == self.DEGRADED and results["overall"] == self.HEALTHY:
                    results["overall"] = self.DEGRADED
                    results["issues"].append({
                        "component": component,
                        "severity": "medium",
                        "message": check.message,
                    })
            except Exception as e:
                results["components"][component] = {
                    "status": self.UNHEALTHY,
                    "message": f"Health check failed: {str(e)}",
                }
                results["overall"] = self.UNHEALTHY
                results["issues"].append({
                    "component": component,
                    "severity": "critical",
                    "message": str(e),
                })

        self.total_checks += 1
        self._last_check_at = self._now()
        return results

    def detect_incident(self, health: Dict[str, Any]) -> Optional[Incident]:
        """Detect if there's an incident based on health check."""
        if health["overall"] == self.HEALTHY:
            return None

        # Find the most severe issue
        issues = health.get("issues", [])
        if not issues:
            return None

        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 3))

        top_issue = issues[0]
        incident = Incident(
            id=f"inc_{int(time.time())}_{top_issue['component']}",
            type=self._classify_incident(top_issue),
            component=top_issue["component"],
            severity=top_issue["severity"],
            detected_at=self._now_iso(),
            details=top_issue,
        )

        return incident

    def _classify_incident(self, issue: Dict[str, Any]) -> str:
        """Classify incident type from issue."""
        component = issue.get("component", "").lower()
        message = issue.get("message", "").lower()

        if "stuck" in message or "hang" in message:
            return "agent_stuck"
        elif "error" in message or "fail" in message:
            return "high_error_rate"
        elif "memory" in message or "oom" in message:
            return "memory_pressure"
        elif "timeout" in message:
            return "task_timeout"
        elif "offline" in message or "not responding" in message:
            return "orion_offline"
        elif "queue" in message or "backed up" in message:
            return "queue_backed_up"
        else:
            return "unknown"

    def trigger_recovery(self, incident: Incident) -> bool:
        """Attempt automatic recovery from incident."""
        if not self.auto_recovery_enabled:
            return False

        # Check recovery attempts
        key = f"{incident.type}_{incident.component}"
        attempts = self._recovery_attempts.get(key, 0)
        if attempts >= self.max_recovery_attempts:
            self._log(f"Max recovery attempts reached for {key}")
            return False

        self._recovery_attempts[key] = attempts + 1

        # Get recovery action
        action = self.RECOVERY_ACTIONS.get(incident.type)
        if not action:
            self._log(f"No recovery action for incident type: {incident.type}")
            return False

        # Try recovery handler
        handler = self._recovery_handlers.get(incident.type)
        if handler:
            try:
                success = handler(incident)
                if success:
                    incident.auto_recovered = True
                    incident.resolved_at = self._now_iso()
                    incident.resolution = f"Auto-recovered via {action}"
                    self.auto_recoveries += 1
                    self._log(f"✅ Auto-recovered: {incident.type} on {incident.component}")
                    return True
            except Exception as e:
                self._log(f"Recovery handler failed: {e}")

        return False

    def escalate_to_humans(self, incident: Incident) -> None:
        """Escalate incident to humans."""
        from src.core.human_notifier import get_notifier

        notifier = get_notifier()
        notifier.notify(
            title=f"🚨 Incident: {incident.type} on {incident.component}",
            message=f"""Severity: {incident.severity.upper()}
Component: {incident.component}
Type: {incident.type}
Detected: {incident.detected_at}

Details: {json.dumps(incident.details, indent=2)[:500]}""",
            level="error" if incident.severity in ["high", "critical"] else "warning",
            source="proactive_monitor",
            channels=["dashboard"],
        )
        self.human_escalations += 1
        self._log(f"📢 Escalated to humans: {incident.id}")

    def record_incident(self, incident: Incident) -> None:
        """Record an incident for tracking."""
        with self._lock:
            self._incidents.append(incident)
            self.total_incidents += 1

            # Keep only last 100 incidents
            if len(self._incidents) > 100:
                self._incidents = self._incidents[-100:]

    def resolve_incident(self, incident_id: str, resolution: str) -> bool:
        """Mark an incident as resolved."""
        with self._lock:
            for incident in self._incidents:
                if incident.id == incident_id and not incident.resolved_at:
                    incident.resolved_at = self._now_iso()
                    incident.resolution = resolution
                    return True
        return False

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        self._log("🔍 Proactive monitor started")

        while self._running:
            try:
                # Run health check
                health = self.check_health()

                # Detect incidents
                incident = self.detect_incident(health)
                if incident:
                    self.record_incident(incident)
                    self._log(f"⚠️ Incident detected: {incident.type}")

                    # Try auto-recovery
                    if not self.trigger_recovery(incident):
                        # Escalate to humans
                        self.escalate_to_humans(incident)

                # Reset recovery attempts on healthy state
                if health["overall"] == self.HEALTHY:
                    self._recovery_attempts.clear()

            except Exception as e:
                self._log(f"Monitor loop error: {e}")

            time.sleep(self.check_interval_sec)

        self._log("🔍 Proactive monitor stopped")

    def _log(self, message: str) -> None:
        """Log a message."""
        print(f"[ProactiveMonitor] {message}")

    def start(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_status(self) -> Dict[str, Any]:
        """Get monitor status."""
        with self._lock:
            return {
                "running": self._running,
                "auto_recovery_enabled": self.auto_recovery_enabled,
                "last_check_at": self._last_check_at.isoformat() if self._last_check_at else None,
                "total_checks": self.total_checks,
                "total_incidents": self.total_incidents,
                "auto_recoveries": self.auto_recoveries,
                "human_escalations": self.human_escalations,
                "active_incidents": len([i for i in self._incidents if not i.resolved_at]),
                "health_checks": {k: {"status": v.status, "message": v.message}
                                  for k, v in self._health_checks.items()},
            }

    def get_recent_incidents(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent incidents."""
        with self._lock:
            return [
                {
                    "id": i.id,
                    "type": i.type,
                    "component": i.component,
                    "severity": i.severity,
                    "detected_at": i.detected_at,
                    "resolved_at": i.resolved_at,
                    "resolution": i.resolution,
                    "auto_recovered": i.auto_recovered,
                }
                for i in self._incidents[-limit:]
            ]


# ============================================
# Singleton
# ============================================

_monitor: Optional[ProactiveMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> ProactiveMonitor:
    """Get the singleton monitor instance."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = ProactiveMonitor()
        return _monitor


def start_monitoring() -> None:
    """Start proactive monitoring."""
    get_monitor().start()


def stop_monitoring() -> None:
    """Stop proactive monitoring."""
    get_monitor().stop()
