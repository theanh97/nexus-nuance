"""
Experiment Tracker - Đo lường giá trị thực của việc học
=========================================================

Mọi discovery phải được:
1. Evaluate - Đánh giá giá trị
2. Experiment - Thử nghiệm
3. Measure - Đo lường kết quả
4. Apply/Archive - Apply hoặc archive

Không có experiment = Không có giá trị thực
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import threading


class ExperimentTracker:
    """
    Track experiments from discoveries.
    Ensure learning has REAL value.
    """

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_ARCHIVED = "archived"

    def __init__(self, data_path: str = None):
        self._lock = threading.RLock()

        if data_path:
            self.data_path = Path(data_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.data_path = project_root / "data" / "experiments"
            except:
                self.data_path = Path.cwd() / "data" / "experiments"

        self.data_path.mkdir(parents=True, exist_ok=True)
        self.tracker_file = self.data_path / "experiment_tracker.json"
        self.experiments: List[Dict] = []
        self._load()

    def _load(self):
        """Load experiments from file."""
        if self.tracker_file.exists():
            try:
                with open(self.tracker_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.experiments = data.get("experiments", [])
            except Exception:
                self.experiments = []

    def _save(self):
        """Save experiments to file."""
        with self._lock:
            data = {
                "version": "1.0.0",
                "last_updated": datetime.now().isoformat(),
                "experiments": self.experiments,
                "metrics": self._calculate_metrics()
            }
            with open(self.tracker_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

    def _calculate_metrics(self) -> Dict:
        """Calculate experiment metrics."""
        total = len(self.experiments)
        completed = len([e for e in self.experiments if e.get("status") == self.STATUS_COMPLETED])
        pending = len([e for e in self.experiments if e.get("status") == self.STATUS_PENDING])
        running = len([e for e in self.experiments if e.get("status") == self.STATUS_RUNNING])
        failed = len([e for e in self.experiments if e.get("status") == self.STATUS_FAILED])

        # Calculate average value
        values = [e.get("value_score", e.get("expected_value", 0))
                  for e in self.experiments if e.get("status") == self.STATUS_COMPLETED]
        avg_value = sum(values) / len(values) if values else 0

        # Calculate applied rate
        applied = len([e for e in self.experiments if e.get("applied")])

        return {
            "total_experiments": total,
            "completed": completed,
            "pending": pending,
            "running": running,
            "failed": failed,
            "applied": applied,
            "apply_rate": round(applied / completed * 100, 1) if completed > 0 else 0,
            "average_value": round(avg_value, 1)
        }

    def create_experiment(
        self,
        title: str,
        hypothesis: str,
        discovery_source: str,
        action_items: List[str],
        expected_value: float = 5.0,
        priority: str = "medium",
        tags: List[str] = None
    ) -> Dict:
        """Create a new experiment from a discovery."""

        # Generate ID
        exp_num = len(self.experiments) + 1
        exp_id = f"EXP-{exp_num:03d}"

        experiment = {
            "id": exp_id,
            "discovery_source": discovery_source,
            "title": title,
            "hypothesis": hypothesis,
            "status": self.STATUS_PENDING,
            "priority": priority,
            "action_items": action_items,
            "action_completed": [],
            "expected_value": expected_value,
            "value_score": None,
            "tags": tags or [],
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "results": {},
            "applied": False,
            "files_changed": [],
            "notes": []
        }

        self.experiments.append(experiment)
        self._save()

        return experiment

    def start_experiment(self, exp_id: str) -> Optional[Dict]:
        """Mark experiment as running."""
        for exp in self.experiments:
            if exp["id"] == exp_id:
                exp["status"] = self.STATUS_RUNNING
                exp["started_at"] = datetime.now().isoformat()
                self._save()
                return exp
        return None

    def complete_experiment(
        self,
        exp_id: str,
        results: Dict,
        value_score: float,
        applied: bool = False,
        files_changed: List[str] = None,
        notes: str = None
    ) -> Optional[Dict]:
        """Mark experiment as completed with results."""
        for exp in self.experiments:
            if exp["id"] == exp_id:
                exp["status"] = self.STATUS_COMPLETED
                exp["completed_at"] = datetime.now().isoformat()
                exp["results"] = results
                exp["value_score"] = value_score
                exp["applied"] = applied
                if files_changed:
                    exp["files_changed"] = files_changed
                if notes:
                    exp["notes"].append({
                        "timestamp": datetime.now().isoformat(),
                        "note": notes
                    })
                self._save()
                return exp
        return None

    def fail_experiment(self, exp_id: str, reason: str) -> Optional[Dict]:
        """Mark experiment as failed."""
        for exp in self.experiments:
            if exp["id"] == exp_id:
                exp["status"] = self.STATUS_FAILED
                exp["completed_at"] = datetime.now().isoformat()
                exp["notes"].append({
                    "timestamp": datetime.now().isoformat(),
                    "note": f"FAILED: {reason}"
                })
                self._save()
                return exp
        return None

    def archive_experiment(self, exp_id: str, reason: str) -> Optional[Dict]:
        """Archive an experiment (not valuable enough)."""
        for exp in self.experiments:
            if exp["id"] == exp_id:
                exp["status"] = self.STATUS_ARCHIVED
                exp["completed_at"] = datetime.now().isoformat()
                exp["notes"].append({
                    "timestamp": datetime.now().isoformat(),
                    "note": f"ARCHIVED: {reason}"
                })
                self._save()
                return exp
        return None

    def complete_action(self, exp_id: str, action: str) -> Optional[Dict]:
        """Mark an action item as completed."""
        for exp in self.experiments:
            if exp["id"] == exp_id:
                if action in exp.get("action_items", []):
                    if action not in exp.get("action_completed", []):
                        exp.setdefault("action_completed", []).append(action)
                        self._save()
                return exp
        return None

    def get_pending_experiments(self, limit: int = 10) -> List[Dict]:
        """Get pending experiments sorted by priority and expected value."""
        pending = [e for e in self.experiments if e.get("status") == self.STATUS_PENDING]
        priority_order = {"high": 0, "medium": 1, "low": 2}
        pending.sort(key=lambda x: (
            priority_order.get(x.get("priority", "medium"), 1),
            -x.get("expected_value", 0)
        ))
        return pending[:limit]

    def get_running_experiments(self) -> List[Dict]:
        """Get currently running experiments."""
        return [e for e in self.experiments if e.get("status") == self.STATUS_RUNNING]

    def get_completed_experiments(self, limit: int = 20) -> List[Dict]:
        """Get completed experiments."""
        completed = [e for e in self.experiments if e.get("status") == self.STATUS_COMPLETED]
        completed.sort(key=lambda x: x.get("completed_at", ""), reverse=True)
        return completed[:limit]

    def get_stats(self) -> Dict:
        """Get experiment statistics."""
        return self._calculate_metrics()

    def get_value_report(self) -> str:
        """Generate a human-readable value report."""
        metrics = self.get_stats()
        lines = [
            "# 📊 EXPERIMENT VALUE REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 📈 Metrics",
            f"- Total experiments: {metrics['total_experiments']}",
            f"- Completed: {metrics['completed']}",
            f"- Applied: {metrics['applied']} ({metrics['apply_rate']}%)",
            f"- Average value: {metrics['average_value']}/10",
            "",
            "## ✅ Recent Completed",
        ]

        for exp in self.get_completed_experiments(5):
            value = exp.get("value_score", "?")
            applied = "✓" if exp.get("applied") else "○"
            lines.append(f"- [{applied}] {exp['title']} (value: {value})")

        lines.extend([
            "",
            "## 🔄 Pending",
        ])

        for exp in self.get_pending_experiments(5):
            priority = exp.get("priority", "medium")
            expected = exp.get("expected_value", "?")
            lines.append(f"- [{priority}] {exp['title']} (expected: {expected})")

        return "\n".join(lines)


# Singleton
_tracker: Optional[ExperimentTracker] = None


def get_experiment_tracker() -> ExperimentTracker:
    """Get singleton experiment tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ExperimentTracker()
    return _tracker


def create_experiment(title: str, hypothesis: str, discovery_source: str,
                      action_items: List[str], **kwargs) -> Dict:
    """Create a new experiment."""
    return get_experiment_tracker().create_experiment(
        title=title,
        hypothesis=hypothesis,
        discovery_source=discovery_source,
        action_items=action_items,
        **kwargs
    )


def get_pending_experiments(limit: int = 10) -> List[Dict]:
    """Get pending experiments."""
    return get_experiment_tracker().get_pending_experiments(limit)


def complete_experiment(exp_id: str, results: Dict, value_score: float, **kwargs) -> Optional[Dict]:
    """Complete an experiment."""
    return get_experiment_tracker().complete_experiment(
        exp_id=exp_id,
        results=results,
        value_score=value_score,
        **kwargs
    )


def get_experiment_stats() -> Dict:
    """Get experiment statistics."""
    return get_experiment_tracker().get_stats()


def generate_value_report() -> str:
    """Generate value report."""
    return get_experiment_tracker().get_value_report()
