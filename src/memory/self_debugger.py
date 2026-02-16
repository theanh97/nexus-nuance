"""
Self-Debugger - Auto-detect, Diagnose & Fix Issues
Part of The Dream Team Self-Learning Infrastructure

Features:
- Track all decisions & actions
- Detect anomalies and performance issues
- Root cause analysis (5 Whys)
- Auto-fix proposals
- Thread-safe implementation
- ROBUST ERROR HANDLING
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from enum import Enum
import statistics
import logging
import threading

# Import memory safely
try:
    from .memory_manager import get_memory, learn_lesson, learn_pattern
except ImportError:
    # Fallback for standalone execution
    def get_memory(): return None
    def learn_lesson(*args, **kwargs): return None
    def learn_pattern(*args, **kwargs): return None

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueType(Enum):
    PERFORMANCE = "performance"
    ERROR = "error"
    QUALITY = "quality"
    BEHAVIOR = "behavior"
    RESOURCE = "resource"


class SelfDebugger:
    """
    Self-debugging system for The Dream Team.
    Monitors, detects issues, and proposes fixes.
    Thread-safe implementation.
    """

    _instance = None
    _lock = threading.Lock()

    # Anomaly detection thresholds
    THRESHOLDS = {
        "iteration_duration_warning": 60,      # seconds
        "iteration_duration_critical": 120,    # seconds
        "error_rate_warning": 0.05,            # 5%
        "error_rate_critical": 0.10,           # 10%
        "quality_score_warning": 6,
        "quality_score_critical": 4,
        "token_budget_warning": 0.80,
        "repeat_action_threshold": 5,
        "duplicate_issue_cooldown_seconds": 1800,  # 30 minutes
    }

    # Actions that are EXPECTED to repeat (not infinite loops)
    EXPECTED_REPEATING_ACTIONS = {
        "iteration",        # Learning loop iterations
        "health_check",     # Periodic health checks
        "knowledge_scan",   # Scheduled scans
        "save_state",       # State saves
        "check_improvements",  # Improvement checks
        "heartbeat",        # Heartbeat signals
        "ping",             # Ping/pong
        "poll",             # Polling actions
    }

    def __new__(cls, *args, **kwargs):
        """Thread-safe singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, memory_path: str = None):
        if hasattr(self, '_initialized') and self._initialized:
            return

        self._lock = threading.RLock()

        # Handle path safely - use data/memory/ by default
        if memory_path:
            base_path = Path(memory_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                base_path = project_root / "data" / "memory"
            except NameError:
                base_path = Path.cwd() / "data" / "memory"

        base_path.mkdir(parents=True, exist_ok=True)

        self.log_path = base_path / "decision_log.json"
        self.issues_path = base_path / "issues.json"
        self.metrics_path = base_path / "metrics.json"
        self.session_history_max = int(os.getenv("DEBUGGER_SESSION_HISTORY_MAX", "300"))
        self.metrics_history_max = int(os.getenv("DEBUGGER_METRICS_HISTORY_MAX", "1500"))
        self.resolved_issues_max = int(os.getenv("DEBUGGER_RESOLVED_ISSUES_MAX", "1000"))
        self.open_issues_max = int(os.getenv("DEBUGGER_OPEN_ISSUES_MAX", "500"))

        self._init_files()

        # Runtime tracking
        self.current_session = {
            "start_time": datetime.now().isoformat(),
            "decisions": [],
            "actions": [],
            "errors": [],
            "metrics": []
        }

        self._initialized = True

    def _init_files(self):
        """Initialize storage files."""
        for path, default in [
            (self.log_path, {"sessions": []}),
            (self.issues_path, {"issues": [], "resolved": []}),
            (self.metrics_path, {"history": []})
        ]:
            if not path.exists():
                self._save_json(path, default)

    def _save_json(self, path: Path, data: Dict) -> bool:
        """Save JSON safely."""
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, path)
            return True
        except (IOError, OSError, TypeError, ValueError) as e:
            logger.error(f"Failed to save {path}: {e}")
            return False

    def _load_json(self, path: Path) -> Dict:
        """Load JSON safely."""
        try:
            if not path.exists():
                return {}
            with self._lock:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (IOError, OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load {path}: {e}")
            return {}

    # ==================== LOGGING ====================

    def log_decision(self, agent: str, decision_type: str,
                     description: str, reasoning: str = None,
                     alternatives: List[str] = None, confidence: float = 0.0) -> int:
        """Log a decision made by an agent. Returns decision index."""
        try:
            decision = {
                "timestamp": datetime.now().isoformat(),
                "agent": agent[:50] if agent else "UNKNOWN",  # Limit length
                "type": decision_type[:50] if decision_type else "unknown",
                "description": description[:500] if description else "",
                "reasoning": reasoning[:500] if reasoning else None,
                "alternatives": (alternatives or [])[:5],  # Max 5 alternatives
                "confidence": max(0.0, min(1.0, confidence)),
                "outcome": None
            }
            with self._lock:
                self.current_session["decisions"].append(decision)
            return len(self.current_session["decisions"]) - 1
        except Exception as e:
            logger.error(f"Failed to log decision: {e}")
            return -1

    def log_action(self, agent: str, action_type: str,
                   details: Dict, success: bool = True,
                   duration_ms: int = 0, tokens_used: int = 0):
        """Log an action taken by an agent."""
        try:
            action = {
                "timestamp": datetime.now().isoformat(),
                "agent": agent[:50] if agent else "UNKNOWN",
                "type": action_type[:50] if action_type else "unknown",
                "details": details if isinstance(details, dict) else {},
                "success": bool(success),
                "duration_ms": max(0, int(duration_ms)),
                "tokens_used": max(0, int(tokens_used))
            }
            with self._lock:
                self.current_session["actions"].append(action)

            # Check for anomalies
            self._check_action_anomalies(action)
        except Exception as e:
            logger.error(f"Failed to log action: {e}")

    def log_error(self, agent: str, error_type: str,
                  message: str, context: Dict = None,
                  recoverable: bool = True):
        """Log an error that occurred."""
        try:
            error = {
                "timestamp": datetime.now().isoformat(),
                "agent": agent[:50] if agent else "UNKNOWN",
                "type": error_type[:50] if error_type else "unknown",
                "message": message[:500] if message else "",
                "context": context if isinstance(context, dict) else {},
                "recoverable": bool(recoverable),
                "resolved": False
            }
            with self._lock:
                self.current_session["errors"].append(error)

            # Check for patterns
            self._check_error_patterns(error)
        except Exception as e:
            logger.error(f"Failed to log error: {e}")

    def log_metric(self, metric_name: str, value: float,
                   agent: str = None, tags: Dict = None):
        """Log a performance metric."""
        try:
            metric = {
                "timestamp": datetime.now().isoformat(),
                "name": metric_name[:50] if metric_name else "unknown",
                "value": float(value) if value is not None else 0.0,
                "agent": agent[:50] if agent else None,
                "tags": tags if isinstance(tags, dict) else {}
            }
            with self._lock:
                self.current_session["metrics"].append(metric)

            # Check thresholds
            self._check_metric_thresholds(metric)
        except Exception as e:
            logger.error(f"Failed to log metric: {e}")

    # ==================== ANOMALY DETECTION ====================

    def _check_action_anomalies(self, action: Dict):
        """Check if action indicates an anomaly."""
        try:
            duration_s = action.get("duration_ms", 0) / 1000

            # Check duration
            if duration_s > self.THRESHOLDS["iteration_duration_critical"]:
                self._create_issue(
                    issue_type=IssueType.PERFORMANCE,
                    severity=Severity.CRITICAL,
                    title="Very slow action detected",
                    description=f"{action['agent']} took {duration_s:.1f}s for {action['type']}",
                    affected_agent=action["agent"]
                )
            elif duration_s > self.THRESHOLDS["iteration_duration_warning"]:
                self._create_issue(
                    issue_type=IssueType.PERFORMANCE,
                    severity=Severity.MEDIUM,
                    title="Slow action detected",
                    description=f"{action['agent']} took {duration_s:.1f}s",
                    affected_agent=action["agent"]
                )

            # Check for repeated actions (but exclude expected repeating actions)
            action_type = action.get("type", "")

            # Skip check for actions that are expected to repeat
            if action_type not in self.EXPECTED_REPEATING_ACTIONS:
                with self._lock:
                    recent_actions = list(self.current_session["actions"][-20:])

                same_type = [a for a in recent_actions
                             if a.get("type") == action_type and
                             a.get("agent") == action.get("agent")]

                if len(same_type) >= self.THRESHOLDS["repeat_action_threshold"]:
                    self._create_issue(
                        issue_type=IssueType.BEHAVIOR,
                        severity=Severity.HIGH,
                        title="Possible infinite loop detected",
                        description=f"{action['agent']} repeated {action_type} {len(same_type)} times",
                        affected_agent=action["agent"]
                    )
        except Exception as e:
            logger.error(f"Error checking action anomalies: {e}")

    def _check_metric_thresholds(self, metric: Dict):
        """Check if metric violates thresholds."""
        try:
            name = metric.get("name", "")
            value = metric.get("value", 0)

            if name == "quality_score":
                if value < self.THRESHOLDS["quality_score_critical"]:
                    self._create_issue(
                        issue_type=IssueType.QUALITY,
                        severity=Severity.CRITICAL,
                        title="Critical quality drop",
                        description=f"Quality score: {value}",
                        affected_agent=metric.get("agent")
                    )
                elif value < self.THRESHOLDS["quality_score_warning"]:
                    self._create_issue(
                        issue_type=IssueType.QUALITY,
                        severity=Severity.MEDIUM,
                        title="Quality below target",
                        description=f"Quality score: {value}",
                        affected_agent=metric.get("agent")
                    )

            elif name == "error_rate":
                if value > self.THRESHOLDS["error_rate_critical"]:
                    self._create_issue(
                        issue_type=IssueType.ERROR,
                        severity=Severity.CRITICAL,
                        title="High error rate",
                        description=f"Error rate: {value*100:.1f}%",
                        affected_agent=metric.get("agent")
                    )
        except Exception as e:
            logger.error(f"Error checking metric thresholds: {e}")

    def _check_error_patterns(self, error: Dict):
        """Check if error indicates a pattern."""
        try:
            with self._lock:
                recent_errors = list(self.current_session["errors"][-10:])

            same_type = [e for e in recent_errors if e.get("type") == error.get("type")]

            if len(same_type) >= 3:
                learn_pattern(
                    pattern_type="error_pattern",
                    pattern_data={
                        "error_type": error.get("type"),
                        "agent": error.get("agent"),
                        "frequency": len(same_type)
                    },
                    success_rate=0.0
                )

                self._create_issue(
                    issue_type=IssueType.ERROR,
                    severity=Severity.HIGH,
                    title="Recurring error pattern",
                    description=f"{error.get('type')} occurred {len(same_type)} times",
                    affected_agent=error.get("agent")
                )
        except Exception as e:
            logger.error(f"Error checking error patterns: {e}")

    # ==================== ISSUE MANAGEMENT ====================

    def _create_issue(self, issue_type: IssueType, severity: Severity,
                      title: str, description: str,
                      affected_agent: str = None) -> Optional[Dict]:
        """Create and log an issue."""
        try:
            data = self._load_json(self.issues_path)
            if not data:
                data = {"issues": [], "resolved": []}

            now = datetime.now()
            open_issues = data.get("issues", [])

            # Merge duplicate open issues instead of creating noise.
            for existing in open_issues:
                if existing.get("status") != "open":
                    continue
                same_type = existing.get("type") == issue_type.value
                same_title = existing.get("title") == title[:100]
                same_agent = existing.get("affected_agent") == (affected_agent[:50] if affected_agent else None)
                if not (same_type and same_title and same_agent):
                    continue

                ts = existing.get("timestamp")
                try:
                    existing_time = datetime.fromisoformat(ts) if ts else now
                except (ValueError, TypeError):
                    existing_time = now
                if (now - existing_time).total_seconds() <= self.THRESHOLDS["duplicate_issue_cooldown_seconds"]:
                    existing["occurrence_count"] = int(existing.get("occurrence_count", 1)) + 1
                    existing["last_seen"] = now.isoformat()
                    self._save_json(self.issues_path, data)
                    return existing

            issue = {
                "id": f"issue_{now.strftime('%Y%m%d_%H%M%S')}_{hash((title, description, affected_agent)) % 100000:05d}",
                "timestamp": now.isoformat(),
                "type": issue_type.value,
                "severity": severity.value,
                "title": title[:100],
                "description": description[:500],
                "affected_agent": affected_agent[:50] if affected_agent else None,
                "status": "open",
                "occurrence_count": 1,
                "last_seen": now.isoformat(),
                "fix_proposal": self._generate_fix_proposal(issue_type.value),
                "resolved_at": None
            }

            # Save issue
            data["issues"].append(issue)
            if self.open_issues_max > 0 and len(data["issues"]) > self.open_issues_max:
                data["issues"] = data["issues"][-self.open_issues_max:]
            self._save_json(self.issues_path, data)

            # Learn from issue
            learn_lesson(
                lesson_type="failure",
                description=f"{title}: {description}",
                context=f"Agent: {affected_agent}",
                impact=severity.value
            )

            return issue
        except Exception as e:
            logger.error(f"Failed to create issue: {e}")
            return None

    def _generate_fix_proposal(self, issue_type: str) -> Dict:
        """Generate a fix proposal based on issue type."""
        proposals = {
            IssueType.PERFORMANCE.value: {
                "method": "5_whys_analysis",
                "recommended_fix": "Optimize prompts, implement caching, or use faster model"
            },
            IssueType.ERROR.value: {
                "method": "5_whys_analysis",
                "recommended_fix": "Add input validation and implement retry with backoff"
            },
            IssueType.QUALITY.value: {
                "method": "5_whys_analysis",
                "recommended_fix": "Enhance prompts with examples and clearer instructions"
            },
            IssueType.BEHAVIOR.value: {
                "method": "5_whys_analysis",
                "recommended_fix": "Add iteration limits and progress detection"
            },
            IssueType.RESOURCE.value: {
                "method": "5_whys_analysis",
                "recommended_fix": "Optimize resource usage and add cleanup routines"
            }
        }
        return proposals.get(issue_type, {"method": "unknown", "recommended_fix": "Investigate manually"})

    # ==================== SESSION MANAGEMENT ====================

    def end_session(self) -> Dict:
        """End current session and save to log."""
        try:
            self.current_session["end_time"] = datetime.now().isoformat()

            stats = self._calculate_session_stats()
            self.current_session["stats"] = stats

            # Save session
            data = self._load_json(self.log_path)
            if not data:
                data = {"sessions": []}
            data["sessions"].append(self.current_session)
            if self.session_history_max > 0 and len(data["sessions"]) > self.session_history_max:
                data["sessions"] = data["sessions"][-self.session_history_max:]
            self._save_json(self.log_path, data)

            # Save metrics history
            metrics_data = self._load_json(self.metrics_path)
            if not metrics_data:
                metrics_data = {"history": []}
            metrics_data["history"].append({
                "timestamp": datetime.now().isoformat(),
                "stats": stats
            })
            if self.metrics_history_max > 0 and len(metrics_data["history"]) > self.metrics_history_max:
                metrics_data["history"] = metrics_data["history"][-self.metrics_history_max:]
            self._save_json(self.metrics_path, metrics_data)

            # Start fresh session
            with self._lock:
                self.current_session = {
                    "start_time": datetime.now().isoformat(),
                    "decisions": [],
                    "actions": [],
                    "errors": [],
                    "metrics": []
                }

            return stats
        except Exception as e:
            logger.error(f"Failed to end session: {e}")
            return {}

    def _calculate_session_stats(self) -> Dict:
        """Calculate statistics for current session."""
        try:
            actions = self.current_session.get("actions", [])
            errors = self.current_session.get("errors", [])
            decisions = self.current_session.get("decisions", [])

            durations = [a.get("duration_ms", 0) for a in actions if a.get("duration_ms", 0) > 0]
            tokens = [a.get("tokens_used", 0) for a in actions if a.get("tokens_used", 0) > 0]
            success_count = sum(1 for a in actions if a.get("success", False))

            total_actions = max(1, len(actions))

            return {
                "total_decisions": len(decisions),
                "total_actions": len(actions),
                "total_errors": len(errors),
                "error_rate": len(errors) / total_actions if total_actions > 0 else 0,
                "avg_duration_ms": statistics.mean(durations) if durations else 0,
                "total_tokens": sum(tokens),
                "success_rate": success_count / total_actions if total_actions > 0 else 0
            }
        except Exception as e:
            logger.error(f"Failed to calculate stats: {e}")
            return {
                "total_decisions": 0,
                "total_actions": 0,
                "total_errors": 0,
                "error_rate": 0,
                "avg_duration_ms": 0,
                "total_tokens": 0,
                "success_rate": 0
            }

    # ==================== REPORTING ====================

    def get_health_report(self) -> Dict:
        """Generate a health report."""
        try:
            issues_data = self._load_json(self.issues_path)
            metrics_data = self._load_json(self.metrics_path)

            issues = issues_data.get("issues", []) if issues_data else []
            open_issues = [i for i in issues if i.get("status") == "open"]
            critical_issues = [i for i in open_issues if i.get("severity") == "critical"]

            # Calculate health score
            health_score = 100
            health_score -= len(critical_issues) * 20
            health_score -= len([i for i in open_issues if i.get("severity") == "high"]) * 10
            health_score -= len([i for i in open_issues if i.get("severity") == "medium"]) * 5
            health_score = max(0, min(100, health_score))

            # Get recent stats
            history = metrics_data.get("history", []) if metrics_data else []
            recent_stats = history[-1].get("stats", {}) if history else {}

            return {
                "health_score": health_score,
                "status": "healthy" if health_score >= 80 else "degraded" if health_score >= 50 else "critical",
                "open_issues": len(open_issues),
                "critical_issues": len(critical_issues),
                "recent_stats": recent_stats,
                "top_issues": sorted(
                    open_issues,
                    key=lambda x: {
                        "critical": 0,
                        "high": 1,
                        "medium": 2,
                        "low": 3
                    }.get(x.get("severity", "low"), 99)
                )[:5]
            }
        except Exception as e:
            logger.error(f"Failed to get health report: {e}")
            return {
                "health_score": 0,
                "status": "unknown",
                "open_issues": 0,
                "critical_issues": 0,
                "recent_stats": {},
                "top_issues": []
            }

    def get_open_issues(self) -> List[Dict]:
        """Get all open issues."""
        try:
            data = self._load_json(self.issues_path)
            issues = data.get("issues", []) if data else []
            return [i for i in issues if i.get("status") == "open"]
        except Exception as e:
            logger.error(f"Failed to get open issues: {e}")
            return []

    def resolve_issue(self, issue_id: str, resolution: str = None) -> bool:
        """Mark an issue as resolved."""
        try:
            data = self._load_json(self.issues_path)
            if not data:
                return False

            issues = data.get("issues", [])
            for issue in issues:
                if issue.get("id") == issue_id:
                    issue["status"] = "resolved"
                    issue["resolved_at"] = datetime.now().isoformat()
                    issue["resolution"] = resolution

                    # Move to resolved list
                    resolved = data.get("resolved", [])
                    resolved.append(issue)
                    if self.resolved_issues_max > 0 and len(resolved) > self.resolved_issues_max:
                        resolved = resolved[-self.resolved_issues_max:]
                    data["resolved"] = resolved
                    issues.remove(issue)
                    data["issues"] = issues

                    self._save_json(self.issues_path, data)

                    learn_lesson(
                        lesson_type="success",
                        description=f"Resolved: {issue.get('title')}",
                        context=f"Fix: {resolution or 'N/A'}",
                        impact="medium"
                    )
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to resolve issue: {e}")
            return False


# ==================== CONVENIENCE FUNCTIONS ====================

_debugger = None

def get_debugger() -> SelfDebugger:
    """Get singleton debugger instance."""
    global _debugger
    if _debugger is None:
        _debugger = SelfDebugger()
    return _debugger


def track_decision(agent: str, decision_type: str, description: str, **kwargs) -> int:
    """Quick decision logging."""
    return get_debugger().log_decision(agent, decision_type, description, **kwargs)


def track_action(agent: str, action_type: str, details: Dict, **kwargs):
    """Quick action logging."""
    return get_debugger().log_action(agent, action_type, details, **kwargs)


def track_error(agent: str, error_type: str, message: str, **kwargs):
    """Quick error logging."""
    return get_debugger().log_error(agent, error_type, message, **kwargs)


def health_check() -> Dict:
    """Quick health check."""
    return get_debugger().get_health_report()


# ==================== TEST ====================

if __name__ == "__main__":
    print("🔧 Self-Debugger Test")
    print("=" * 50)

    debugger = SelfDebugger()

    # Simulate activity
    debugger.log_decision("ORION", "test", "Testing decision", confidence=0.9)
    debugger.log_action("NOVA", "code_gen", {"file": "test.tsx"}, success=True, duration_ms=1500)
    debugger.log_error("CIPHER", "timeout", "Test timeout")

    # Health check
    health = debugger.get_health_report()
    print(f"Health: {health['health_score']}/100 - {health['status']}")

    # End session
    stats = debugger.end_session()
    print(f"Stats: {stats}")
    print("✅ Self-Debugger OK")
