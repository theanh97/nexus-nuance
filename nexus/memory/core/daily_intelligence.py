"""
Daily Intelligence System
Daily learning routine - morning query, evening reflection.

THE CORE PRINCIPLE:
"Daily refresh - morning ask, evening reflect."
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import threading


class DailyIntelligence:
    """
    Manages daily learning routines.
    Morning: What should I learn?
    Evening: What did I learn?
    """

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "daily_intelligence"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "daily_intelligence"

        self.morning_file = self.base_path / "morning_queries.jsonl"
        self.evening_file = self.base_path / "evening_reflections.jsonl"
        self.insights_file = self.base_path / "insights.json"

        # Configuration
        self.morning_hour = int(os.getenv("MORNING_QUERY_HOUR", "8"))  # 8 AM
        self.evening_hour = int(os.getenv("EVENING_REFLECTION_HOUR", "20"))  # 8 PM

        # In-memory state
        self.morning_queries: List[Dict] = []
        self.evening_reflections: List[Dict] = []
        self.insights: List[Dict] = []

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load()

    def _load(self):
        """Load daily intelligence data."""
        # Load morning queries
        if self.morning_file.exists():
            try:
                with open(self.morning_file, 'r', encoding='utf-8') as f:
                    self.morning_queries = [json.loads(line) for line in f if line.strip()]
            except Exception:
                pass

        # Load evening reflections
        if self.evening_file.exists():
            try:
                with open(self.evening_file, 'r', encoding='utf-8') as f:
                    self.evening_reflections = [json.loads(line) for line in f if line.strip()]
            except Exception:
                pass

        # Load insights
        if self.insights_file.exists():
            try:
                with open(self.insights_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.insights = data.get("insights", [])
            except Exception:
                pass

    def _save(self):
        """Save daily intelligence data."""
        with self._lock:
            # Save morning queries
            with open(self.morning_file, 'w', encoding='utf-8') as f:
                for q in self.morning_queries[-100:]:
                    f.write(json.dumps(q, ensure_ascii=False) + "\n")

            # Save evening reflections
            with open(self.evening_file, 'w', encoding='utf-8') as f:
                for r in self.evening_reflections[-100:]:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

            # Save insights
            with open(self.insights_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "insights": self.insights[-200:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

    # ==================== MORNING QUERY ====================

    def morning_query(
        self,
        context: Optional[Dict] = None,
    ) -> Dict:
        """
        Morning query - what should I learn today?

        Args:
            context: Current context (project, goals, etc.)

        Returns:
            Morning query with recommendations
        """
        context = context or {}

        # Get recommendations from prioritizer
        try:
            from .learning_prioritizer import get_prioritizer
            p = get_prioritizer()
            priorities = p.prioritize(context, max_topics=5)
            top_topics = [t.get("topic") for t in priorities]
        except Exception:
            top_topics = []

        # Get trending topics
        try:
            from .web_search_learner import get_web_learner
            learner = get_web_learner()
            trending = learner.get_trending(limit=5)
        except Exception:
            trending = []

        # Get user preferences
        try:
            from .user_feedback import get_feedback_manager
            fm = get_feedback_manager()
            preferred = fm.get_preferred_actions()[:3]
        except Exception:
            preferred = []

        query = {
            "id": f"morning_{datetime.now().strftime('%Y%m%d')}",
            "type": "morning_query",
            "date": datetime.now().date().isoformat(),
            "timestamp": datetime.now().isoformat(),
            "context": context,
            "recommendations": {
                "top_learning_topics": top_topics,
                "trending_topics": trending,
                "user_preferences": preferred,
            },
            "suggested_queries": self._generate_morning_queries(top_topics, trending),
        }

        self.morning_queries.append(query)
        self._save()

        return query

    def _generate_morning_queries(self, priorities: List[str], trending: List[str]) -> List[str]:
        """Generate suggested search queries for the morning."""
        queries = []

        # Add priority-based queries
        for p in priorities[:3]:
            queries.append(f"{p} best practices 2024")
            queries.append(f"how to implement {p}")

        # Add trending queries
        for t in trending[:2]:
            queries.append(f"{t} latest developments")

        return queries[:10]

    # ==================== EVENING REFLECTION ====================

    def evening_reflection(
        self,
        learnings: Optional[List[Dict]] = None,
        achievements: Optional[List[str]] = None,
        challenges: Optional[List[str]] = None,
    ) -> Dict:
        """
        Evening reflection - what did I learn today?

        Args:
            learnings: What was learned today
            achievements: Key achievements
            challenges: Challenges faced

        Returns:
            Evening reflection
        """
        learnings = learnings or []
        achievements = achievements or []
        challenges = challenges or []

        # Get today's activities
        try:
            from .audit_logger import get_audit_logger
            logger = get_audit_logger()
            today_logs = logger.get_today_logs(limit=100)
            actions_today = len(today_logs)
        except Exception:
            actions_today = 0

        # Get today's discoveries
        try:
            from .web_search_learner import get_web_learner
            learner = get_web_learner()
            discoveries = learner.get_discoveries(
                since=datetime.now() - timedelta(days=1),
                limit=20,
            )
            discovery_count = len(discoveries)
        except Exception:
            discovery_count = 0

        reflection = {
            "id": f"evening_{datetime.now().strftime('%Y%m%d')}",
            "type": "evening_reflection",
            "date": datetime.now().date().isoformat(),
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_actions": actions_today,
                "discoveries": discovery_count,
                "learnings_count": len(learnings),
            },
            "learnings": learnings,
            "achievements": achievements,
            "challenges": challenges,
            "insights": self._generate_insights(learnings, achievements, challenges),
        }

        self.evening_reflections.append(reflection)
        self._save()

        return reflection

    def _generate_insights(
        self,
        learnings: List[Dict],
        achievements: List[str],
        challenges: List[str],
    ) -> List[str]:
        """Generate insights from the day's activities."""
        insights = []

        # Generate insights based on patterns
        if len(achievements) > len(challenges):
            insights.append("Productive day with more achievements than challenges")

        if len(learnings) > 5:
            insights.append("High learning velocity today")

        if challenges:
            insights.append(f"Opportunity to address {len(challenges)} challenge(s) tomorrow")

        return insights

    # ==================== INSIGHTS ====================

    def add_insight(
        self,
        insight: str,
        source: str = "auto",
        context: Optional[Dict] = None,
    ) -> Dict:
        """Add an insight."""
        insight_entry = {
            "id": f"insight_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.insights)}",
            "insight": insight,
            "source": source,
            "context": context or {},
            "timestamp": datetime.now().isoformat(),
        }

        self.insights.append(insight_entry)
        self._save()

        return insight_entry

    def get_insights(
        self,
        since: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Get insights with optional filter."""
        insights = self.insights

        if since:
            insights = [
                i for i in insights
                if datetime.fromisoformat(i.get("timestamp", "2000-01-01")) >= since
            ]

        return insights[-limit:]

    # ==================== DAILY CYCLE ====================

    def should_run_morning(self) -> bool:
        """Check if it's time for morning query."""
        now = datetime.now()
        return now.hour >= self.morning_hour and now.hour < self.morning_hour + 1

    def should_run_evening(self) -> bool:
        """Check if it's time for evening reflection."""
        now = datetime.now()
        return now.hour >= self.evening_hour and now.hour < self.evening_hour + 1

    def run_daily_cycle(self, context: Optional[Dict] = None) -> Dict:
        """Run the appropriate daily cycle based on time."""
        now = datetime.now()

        if now.hour < 12:
            # Morning
            result = self.morning_query(context)
            result["cycle_type"] = "morning"
        else:
            # Evening
            result = self.evening_reflection()
            result["cycle_type"] = "evening"

        return result

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get daily intelligence statistics."""
        today = datetime.now().date().isoformat()

        # Count today's entries
        morning_today = sum(1 for q in self.morning_queries if q.get("date") == today)
        evening_today = sum(1 for r in self.evening_reflections if r.get("date") == today)

        return {
            "total_morning_queries": len(self.morning_queries),
            "total_evening_reflections": len(self.evening_reflections),
            "total_insights": len(self.insights),
            "morning_today": morning_today,
            "evening_today": evening_today,
        }

    def get_today_summary(self) -> Dict:
        """Get today's summary."""
        today = datetime.now().date().isoformat()

        morning = None
        for q in reversed(self.morning_queries):
            if q.get("date") == today:
                morning = q
                break

        evening = None
        for r in reversed(self.evening_reflections):
            if r.get("date") == today:
                evening = r
                break

        insights = self.get_insights(
            since=datetime.now() - timedelta(days=1),
            limit=10,
        )

        return {
            "date": today,
            "morning_query": morning,
            "evening_reflection": evening,
            "insights": insights,
        }

    def generate_daily_report(self) -> str:
        """Generate daily intelligence report."""
        summary = self.get_today_summary()
        stats = self.get_stats()

        lines = [
            "☀️ Daily Intelligence Report",
            f"Date: {summary['date']}",
            "",
        ]

        if summary.get("morning_query"):
            m = summary["morning_query"]
            lines.extend([
                "## 🌅 Morning Query",
                f"Suggested queries: {len(m.get('suggested_queries', []))}",
            ])
            for q in m.get("suggested_queries", [])[:5]:
                lines.append(f"- {q}")
            lines.append("")

        if summary.get("evening_reflection"):
            e = summary["evening_reflection"]
            lines.extend([
                "## 🌙 Evening Reflection",
                f"Total actions: {e.get('summary', {}).get('total_actions', 0)}",
                f"Discoveries: {e.get('summary', {}).get('discoveries', 0)}",
                f"Learnings: {e.get('summary', {}).get('learnings_count', 0)}",
            ])

            if e.get("achievements"):
                lines.append("Achievements:")
                for a in e.get("achievements", []):
                    lines.append(f"- {a}")

            if e.get("insights"):
                lines.append("Insights:")
                for i in e.get("insights", []):
                    lines.append(f"- {i}")
            lines.append("")

        # Overall stats
        lines.extend([
            "## 📊 Statistics",
            f"- Morning queries: {stats['total_morning_queries']}",
            f"- Evening reflections: {stats['total_evening_reflections']}",
            f"- Total insights: {stats['total_insights']}",
        ])

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_daily_intelligence = None


def get_daily_intelligence() -> DailyIntelligence:
    """Get singleton daily intelligence instance."""
    global _daily_intelligence
    if _daily_intelligence is None:
        _daily_intelligence = DailyIntelligence()
    return _daily_intelligence


def morning_query(context: Optional[Dict] = None) -> Dict:
    """Run morning query."""
    return get_daily_intelligence().morning_query(context)


def evening_reflection(
    learnings: Optional[List[Dict]] = None,
    achievements: Optional[List[str]] = None,
    challenges: Optional[List[str]] = None,
) -> Dict:
    """Run evening reflection."""
    return get_daily_intelligence().evening_reflection(learnings, achievements, challenges)


def add_insight(insight: str, source: str = "auto") -> Dict:
    """Add an insight."""
    return get_daily_intelligence().add_insight(insight, source)


def get_today_summary() -> Dict:
    """Get today's summary."""
    return get_daily_intelligence().get_today_summary()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Daily Intelligence System")
    print("=" * 50)

    di = DailyIntelligence()

    # Test morning query
    print("\nMorning Query:")
    morning = di.morning_query({"project": "automation"})
    print(f"Date: {morning['date']}")
    print(f"Suggested queries: {morning.get('suggested_queries', [])[:3]}")

    # Test evening reflection
    print("\nEvening Reflection:")
    evening = di.evening_reflection(
        learnings=[{"topic": "AI agents", "learned": True}],
        achievements=["Fixed critical bug", "Deployed new feature"],
        challenges=["Need more testing"],
    )
    print(f"Summary: {evening.get('summary')}")
    print(f"Insights: {evening.get('insights')}")

    # Print daily report
    print("\n" + di.generate_daily_report())
