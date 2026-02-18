"""
SELF-REVIEW & META-LEARNING ENGINE
==================================
Liên tục review kiến trúc - Học từ chính mình - Tự cải tiến

CAPABILITIES:
1. Review toàn bộ kiến trúc hệ thống
2. Phát hiện bottlenecks và issues
3. Đề xuất cải tiến
4. Học từ patterns của chính mình
5. Meta-learning - học cách học tốt hơn
"""

import json
import os
import sys
import time
import threading
import ast
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
import random

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"


@dataclass
class SystemMetric:
    """A system performance metric"""
    name: str
    value: float
    timestamp: str
    trend: str  # up, down, stable


@dataclass
class Improvement:
    """A suggested improvement"""
    id: str
    category: str  # performance, architecture, memory, learning
    description: str
    priority: int  # 1-10
    impact: str  # high, medium, low
    status: str  # pending, in_progress, implemented
    created_at: str


@dataclass
class LearningPattern:
    """A pattern learned from self-analysis"""
    pattern_type: str
    description: str
    frequency: int
    last_seen: str
    action_taken: str


class SelfReviewEngine:
    """
    Tự review và cải tiến hệ thống
    """

    def __init__(self, brain=None):
        self.brain = brain
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Storage
        self.metrics_file = self.data_dir / "system_metrics.json"
        self.improvements_file = self.data_dir / "improvements.json"
        self.patterns_file = self.data_dir / "learning_patterns.json"
        self.review_log = self.data_dir / "review.log"

        # State
        self.metrics: Dict[str, List[SystemMetric]] = defaultdict(list)
        self.improvements: List[Improvement] = []
        self.patterns: List[LearningPattern] = []

        # Review schedule
        self.last_review = None
        self.review_interval = 3600  # 1 hour

        self._load()
        self._start_review_thread()

    def _load(self):
        """Load existing data"""
        if self.metrics_file.exists():
            with open(self.metrics_file, 'r') as f:
                data = json.load(f)
                for name, metrics in data.get("metrics", {}).items():
                    self.metrics[name] = [SystemMetric(**m) for m in metrics]

        if self.improvements_file.exists():
            with open(self.improvements_file, 'r') as f:
                data = json.load(f)
                self.improvements = [Improvement(**i) for i in data.get("improvements", [])]

        if self.patterns_file.exists():
            with open(self.patterns_file, 'r') as f:
                data = json.load(f)
                self.patterns = [LearningPattern(**p) for p in data.get("patterns", [])]

    def _save(self):
        """Save data"""
        with open(self.metrics_file, 'w') as f:
            json.dump({
                "metrics": {
                    name: [vars(m) for m in metrics]
                    for name, metrics in self.metrics.items()
                },
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

        with open(self.improvements_file, 'w') as f:
            json.dump({
                "improvements": [vars(i) for i in self.improvements]
            }, f, indent=2)

        with open(self.patterns_file, 'w') as f:
            json.dump({
                "patterns": [vars(p) for p in self.patterns]
            }, f, indent=2)

    def _log(self, message: str, level: str = "INFO"):
        """Log review event"""
        timestamp = datetime.now().isoformat()
        line = f"[{timestamp}] [{level}] {message}\n"
        with open(self.review_log, 'a') as f:
            f.write(line)
        print(f"[REVIEW] [{level}] {message}")

    # ==================== METRICS COLLECTION ====================

    def collect_metrics(self) -> Dict[str, float]:
        """Collect system metrics"""
        metrics = {}

        # Memory usage
        try:
            import psutil
            process = psutil.Process()
            metrics["memory_mb"] = process.memory_info().rss / 1024 / 1024
            metrics["cpu_percent"] = process.cpu_percent()
        except:
            metrics["memory_mb"] = 0
            metrics["cpu_percent"] = 0

        # Knowledge metrics (if brain available)
        if self.brain:
            try:
                stats = self.brain.get_stats()
                metrics["knowledge_items"] = stats.get("memory", {}).get("total_knowledge", 0)
                metrics["skills_count"] = len(stats.get("skills", {}))
            except:
                pass

        # File metrics
        try:
            data_size = sum(f.stat().st_size for f in self.data_dir.rglob("*") if f.is_file())
            metrics["data_size_mb"] = data_size / 1024 / 1024
        except:
            metrics["data_size_mb"] = 0

        # Store metrics
        for name, value in metrics.items():
            # Calculate trend
            trend = "stable"
            if name in self.metrics and self.metrics[name]:
                last_value = self.metrics[name][-1].value
                if value > last_value * 1.1:
                    trend = "up"
                elif value < last_value * 0.9:
                    trend = "down"

            self.metrics[name].append(SystemMetric(
                name=name,
                value=value,
                timestamp=datetime.now().isoformat(),
                trend=trend
            ))

            # Keep only last 1000 metrics per type
            self.metrics[name] = self.metrics[name][-1000:]

        self._save()
        return metrics

    # ==================== ARCHITECTURE REVIEW ====================

    def review_architecture(self) -> List[Dict]:
        """Review system architecture"""
        issues = []

        # Check for code files
        src_dir = PROJECT_ROOT / "src"
        brain_dir = src_dir / "brain"

        if brain_dir.exists():
            # Check file sizes
            for f in brain_dir.glob("*.py"):
                size = f.stat().st_size
                if size > 50000:  # > 50KB
                    issues.append({
                        "type": "large_file",
                        "file": str(f),
                        "size_kb": size / 1024,
                        "suggestion": "Consider splitting into smaller modules"
                    })

            # Check for duplicate code patterns
            # (Would use AST analysis in production)

        # Check data directory
        if self.data_dir.exists():
            total_size = sum(f.stat().st_size for f in self.data_dir.rglob("*") if f.is_file())
            if total_size > 100 * 1024 * 1024:  # > 100MB
                issues.append({
                    "type": "data_bloat",
                    "size_mb": total_size / 1024 / 1024,
                    "suggestion": "Consider data compression and cleanup"
                })

        return issues

    def review_learning_efficiency(self) -> Dict:
        """Review how well the system is learning"""
        efficiency = {
            "score": 0,
            "issues": [],
            "recommendations": []
        }

        # Check improvement implementation rate
        if self.improvements:
            implemented = sum(1 for i in self.improvements if i.status == "implemented")
            rate = implemented / len(self.improvements)
            if rate < 0.5:
                efficiency["issues"].append("Low improvement implementation rate")
                efficiency["recommendations"].append("Prioritize implementing pending improvements")

        # Check pattern utilization
        if self.patterns:
            utilized = sum(1 for p in self.patterns if p.action_taken != "none")
            rate = utilized / len(self.patterns) if self.patterns else 0
            if rate < 0.3:
                efficiency["issues"].append("Patterns not being utilized")
                efficiency["recommendations"].append("Create actions for learned patterns")

        # Calculate overall score
        efficiency["score"] = max(0, 100 - len(efficiency["issues"]) * 20)

        return efficiency

    # ==================== IMPROVEMENT GENERATION ====================

    def generate_improvements(self) -> List[Improvement]:
        """Generate improvement suggestions"""
        new_improvements = []

        # Collect metrics first
        metrics = self.collect_metrics()

        # Memory-based improvements
        if metrics.get("memory_mb", 0) > 500:
            new_improvements.append(Improvement(
                id=f"imp_{datetime.now().strftime('%Y%m%d%H%M%S')}_mem",
                category="memory",
                description="High memory usage - implement caching and cleanup",
                priority=8,
                impact="high",
                status="pending",
                created_at=datetime.now().isoformat()
            ))

        # Data size improvements
        if metrics.get("data_size_mb", 0) > 50:
            new_improvements.append(Improvement(
                id=f"imp_{datetime.now().strftime('%Y%m%d%H%M%S')}_data",
                category="storage",
                description="Large data size - implement compression",
                priority=6,
                impact="medium",
                status="pending",
                created_at=datetime.now().isoformat()
            ))

        # Architecture improvements
        arch_issues = self.review_architecture()
        for issue in arch_issues:
            new_improvements.append(Improvement(
                id=f"imp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{issue['type']}",
                category="architecture",
                description=f"{issue['type']}: {issue.get('suggestion', '')}",
                priority=5,
                impact="medium",
                status="pending",
                created_at=datetime.now().isoformat()
            ))

        # Add to list
        self.improvements.extend(new_improvements)
        self._save()

        return new_improvements

    # ==================== META-LEARNING ====================

    def analyze_patterns(self) -> List[LearningPattern]:
        """Analyze patterns in system behavior"""
        new_patterns = []

        # Analyze metric trends
        for metric_name, metric_list in self.metrics.items():
            if len(metric_list) >= 10:
                # Check for consistent trends
                recent = metric_list[-10:]
                values = [m.value for m in recent]

                # Upward trend
                if all(values[i] <= values[i+1] for i in range(len(values)-1)):
                    pattern = LearningPattern(
                        pattern_type="metric_trend",
                        description=f"{metric_name} consistently increasing",
                        frequency=len(values),
                        last_seen=datetime.now().isoformat(),
                        action_taken="monitoring"
                    )
                    new_patterns.append(pattern)

                # Downward trend
                elif all(values[i] >= values[i+1] for i in range(len(values)-1)):
                    pattern = LearningPattern(
                        pattern_type="metric_trend",
                        description=f"{metric_name} consistently decreasing",
                        frequency=len(values),
                        last_seen=datetime.now().isoformat(),
                        action_taken="monitoring"
                    )
                    new_patterns.append(pattern)

        # Store patterns
        for pattern in new_patterns:
            # Check if similar pattern exists
            exists = any(
                p.pattern_type == pattern.pattern_type and
                p.description == pattern.description
                for p in self.patterns
            )
            if not exists:
                self.patterns.append(pattern)

        self._save()
        return new_patterns

    def learn_from_self(self) -> Dict:
        """Meta-learning - learn about own learning"""
        learning_report = {
            "timestamp": datetime.now().isoformat(),
            "patterns_learned": len(self.patterns),
            "improvements_generated": len(self.improvements),
            "improvements_implemented": sum(1 for i in self.improvements if i.status == "implemented"),
            "metrics_tracked": len(self.metrics),
            "insights": []
        }

        # Generate insights
        if learning_report["improvements_generated"] > 10 and learning_report["improvements_implemented"] < 3:
            learning_report["insights"].append({
                "type": "inefficiency",
                "message": "Many improvements generated but few implemented - consider prioritization"
            })

        if len(self.patterns) > 20:
            learning_report["insights"].append({
                "type": "pattern_rich",
                "message": "Many patterns detected - ensure they're being utilized"
            })

        return learning_report

    # ==================== FULL REVIEW ====================

    def run_full_review(self) -> Dict:
        """Run comprehensive system review"""
        review = {
            "timestamp": datetime.now().isoformat(),
            "metrics": {},
            "architecture_issues": [],
            "learning_efficiency": {},
            "new_improvements": [],
            "new_patterns": [],
            "meta_insights": {},
            "overall_health": 0
        }

        # Collect metrics
        review["metrics"] = self.collect_metrics()

        # Review architecture
        review["architecture_issues"] = self.review_architecture()

        # Review learning
        review["learning_efficiency"] = self.review_learning_efficiency()

        # Generate improvements
        review["new_improvements"] = self.generate_improvements()

        # Analyze patterns
        review["new_patterns"] = self.analyze_patterns()

        # Meta-learning
        review["meta_insights"] = self.learn_from_self()

        # Calculate overall health
        health = 100
        health -= len(review["architecture_issues"]) * 10
        health -= (100 - review["learning_efficiency"]["score"]) * 0.5
        health -= len([i for i in self.improvements if i.status == "pending"]) * 2
        review["overall_health"] = max(0, min(100, health))

        self._log(f"Full review complete - Health: {review['overall_health']}%")

        self.last_review = datetime.now()
        self._save()

        return review

    # ==================== BACKGROUND REVIEW ====================

    def _review_loop(self):
        """Background review loop"""
        while True:
            try:
                time.sleep(self.review_interval)

                # Check if should run review
                if self.last_review:
                    elapsed = (datetime.now() - self.last_review).total_seconds()
                    if elapsed < self.review_interval:
                        continue

                self.run_full_review()

            except Exception as e:
                self._log(f"Review error: {e}", "ERROR")
                time.sleep(300)

    def _start_review_thread(self):
        """Start background review thread"""
        thread = threading.Thread(target=self._review_loop, daemon=True)
        thread.start()

    # ==================== STATS ====================

    def get_stats(self) -> Dict:
        """Get review statistics"""
        return {
            "total_metrics": sum(len(m) for m in self.metrics.values()),
            "metric_types": len(self.metrics),
            "total_improvements": len(self.improvements),
            "pending_improvements": sum(1 for i in self.improvements if i.status == "pending"),
            "implemented_improvements": sum(1 for i in self.improvements if i.status == "implemented"),
            "total_patterns": len(self.patterns),
            "last_review": self.last_review.isoformat() if self.last_review else None
        }


# ==================== CONVENIENCE ====================

_review_engine: Optional[SelfReviewEngine] = None


def get_review() -> SelfReviewEngine:
    """Get singleton review engine"""
    global _review_engine
    if _review_engine is None:
        _review_engine = SelfReviewEngine()
    return _review_engine


def run_review() -> Dict:
    """Run full system review"""
    return get_review().run_full_review()


def get_review_stats() -> Dict:
    """Get review stats"""
    return get_review().get_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("SELF-REVIEW & META-LEARNING ENGINE")
    print("=" * 70)

    engine = get_review()

    print("\nRunning full system review...")
    review = engine.run_full_review()

    print(f"\nOverall Health: {review['overall_health']}%")
    print(f"Architecture Issues: {len(review['architecture_issues'])}")
    print(f"New Improvements: {len(review['new_improvements'])}")
    print(f"New Patterns: {len(review['new_patterns'])}")

    if review['meta_insights'].get('insights'):
        print("\nInsights:")
        for insight in review['meta_insights']['insights']:
            print(f"  - {insight['message']}")

    print("\nReview engine running in background...")
