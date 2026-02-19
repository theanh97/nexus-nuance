"""
Archive Analyzer Module
=======================

Analyzes the archive of improvements to find patterns and track progress.
Based on SICA's archive analysis approach.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
import threading
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ArchiveEntry:
    """An entry in the improvement archive."""
    entry_id: str
    iteration: int
    timestamp: str
    improvement_type: str
    success: bool
    score_before: float
    score_after: float
    improvement_delta: float
    patch_id: Optional[str] = None
    file_changed: Optional[str] = None
    notes: str = ""


class ArchiveAnalyzer:
    """
    Analyzes the archive of improvements.

    Capabilities:
    1. Track improvement trends
    2. Identify successful patterns
    3. Calculate statistics
    4. Predict best strategies
    """

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Storage
        self.archive: List[ArchiveEntry] = []
        self.archive_file = self.project_root / "wisdom" / "experiments" / "archive.json"
        self.archive_file.parent.mkdir(parents=True, exist_ok=True)

        self._load()

    def _load(self):
        """Load archive from file."""
        if self.archive_file.exists():
            try:
                with open(self.archive_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.archive = [ArchiveEntry(**e) for e in data.get("entries", [])]
            except Exception as e:
                logger.warning(f"Failed to load archive: {e}")

    def _save(self):
        """Save archive to file."""
        with self._lock:
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_entries": len(self.archive),
                "statistics": self._calculate_statistics(),
                "entries": [asdict(e) for e in self.archive]
            }
            with open(self.archive_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

    def record_improvement(
        self,
        improvement_type: str,
        success: bool,
        score_before: float,
        score_after: float,
        patch_id: str = None,
        file_changed: str = None,
        notes: str = ""
    ) -> ArchiveEntry:
        """Record an improvement attempt."""
        iteration = len(self.archive) + 1
        entry_id = f"ARCH-{iteration:04d}"

        entry = ArchiveEntry(
            entry_id=entry_id,
            iteration=iteration,
            timestamp=datetime.now().isoformat(),
            improvement_type=improvement_type,
            success=success,
            score_before=score_before,
            score_after=score_after,
            improvement_delta=score_after - score_before,
            patch_id=patch_id,
            file_changed=file_changed,
            notes=notes
        )

        with self._lock:
            self.archive.append(entry)
            self._save()

        return entry

    def _calculate_statistics(self) -> Dict:
        """Calculate archive statistics."""
        if not self.archive:
            return {
                "total_improvements": 0,
                "successful": 0,
                "failed": 0,
                "success_rate": 0,
                "average_improvement": 0,
                "best_improvement": 0,
            }

        successful = [e for e in self.archive if e.success]
        failed = [e for e in self.archive if not e.success]

        improvements = [e.improvement_delta for e in successful]

        return {
            "total_improvements": len(self.archive),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": round(len(successful) / len(self.archive) * 100, 1),
            "average_improvement": round(sum(improvements) / len(improvements), 2) if improvements else 0,
            "best_improvement": round(max(improvements), 2) if improvements else 0,
            "total_improvement_delta": round(sum(improvements), 2),
        }

    def get_statistics(self) -> Dict:
        """Get current statistics."""
        return self._calculate_statistics()

    def get_successful_patterns(self) -> Dict[str, int]:
        """Identify successful improvement patterns."""
        patterns = defaultdict(int)

        for entry in self.archive:
            if entry.success:
                key = f"{entry.improvement_type}:{entry.file_changed or 'general'}"
                patterns[key] += 1

        return dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True))

    def get_failure_patterns(self) -> Dict[str, int]:
        """Identify failure patterns to avoid."""
        patterns = defaultdict(int)

        for entry in self.archive:
            if not entry.success:
                key = f"{entry.improvement_type}:{entry.file_changed or 'general'}"
                patterns[key] += 1

        return dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True))

    def get_trend(self, window: int = 10) -> List[Tuple[int, float]]:
        """Get improvement trend over last N entries."""
        trend = []
        for entry in self.archive[-window:]:
            trend.append((entry.iteration, entry.score_after))

        return trend

    def predict_best_strategy(self) -> str:
        """Predict the best improvement strategy based on history."""
        if not self.archive:
            return "code_quality"  # Default

        # Count successes by type
        success_counts = defaultdict(int)
        for entry in self.archive:
            if entry.success:
                success_counts[entry.improvement_type] += 1

        if success_counts:
            return max(success_counts.items(), key=lambda x: x[1])[0]

        return "code_quality"

    def get_best_iteration(self) -> Optional[int]:
        """Get the iteration with the best score."""
        if not self.archive:
            return None

        best = max(self.archive, key=lambda e: e.score_after)
        return best.iteration

    def get_improvement_summary(self) -> str:
        """Generate a human-readable summary."""
        stats = self.get_statistics()

        lines = [
            "# 📊 Archive Analysis Summary",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 📈 Statistics",
            f"- Total improvements: {stats['total_improvements']}",
            f"- Success rate: {stats['success_rate']}%",
            f"- Average improvement: {stats['average_improvement']}",
            f"- Best improvement: {stats['best_improvement']}",
            "",
            "## ✅ Successful Patterns",
        ]

        for pattern, count in list(self.get_successful_patterns().items())[:5]:
            lines.append(f"- {pattern}: {count} successes")

        lines.extend([
            "",
            "## ❌ Failure Patterns (Avoid)",
        ])

        for pattern, count in list(self.get_failure_patterns().items())[:5]:
            lines.append(f"- {pattern}: {count} failures")

        lines.extend([
            "",
            f"## 🎯 Recommended Strategy: {self.predict_best_strategy()}",
        ])

        return "\n".join(lines)


# Singleton
_analyzer: Optional[ArchiveAnalyzer] = None


def get_archive_analyzer(project_root: str = None) -> ArchiveAnalyzer:
    """Get singleton archive analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = ArchiveAnalyzer(project_root)
    return _analyzer


def analyze_archive() -> Dict:
    """Analyze the archive."""
    return get_archive_analyzer().get_statistics()
