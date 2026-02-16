"""
Daily Report Generator
Generates comprehensive daily reports for users.

THE CORE PRINCIPLE:
"Transparency - user gets full visibility every day."
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import threading


class DailyReportGenerator:
    """
    Generates comprehensive daily reports.
    Covers learning, actions, insights, and pending decisions.
    """

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "reports"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "reports"

        self.reports_dir = self.base_path / "daily"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ==================== REPORT GENERATION ====================

    def generate_report(self, date: Optional[datetime] = None) -> str:
        """
        Generate comprehensive daily report.

        Args:
            date: Date for report (defaults to today)

        Returns:
            Formatted report string
        """
        date = date or datetime.now()
        lines = []

        # Header
        lines.extend([
            "=" * 60,
            f"📊 NEXUS DAILY REPORT - {date.strftime('%Y-%m-%d')}",
            "=" * 60,
            "",
        ])

        # Learning Summary
        lines.extend(self._get_learning_summary())

        # Actions Taken
        lines.extend(self._get_actions_summary())

        # Insights
        lines.extend(self._get_insights_summary())

        # Pending Decisions
        lines.extend(self._get_pending_summary())

        # System Health
        lines.extend(self._get_health_summary())

        # Footer
        lines.extend([
            "",
            "=" * 60,
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "The Dream Team - Continuous Learning System",
        ])

        return "\n".join(lines)

    def _get_learning_summary(self) -> List[str]:
        """Get learning summary section."""
        lines = ["🧠 LEARNING SUMMARY", "-" * 40]

        try:
            # Web learning stats
            from .web_search_learner import get_web_learner
            learner = get_web_learner()
            stats = learner.get_stats()
            lines.append(f"   • Knowledge discovered: {stats.get('total_discoveries', 0)} items")

            # User feedback
            from .user_feedback import get_feedback_manager
            fm = get_feedback_manager()
            fb_stats = fm.get_feedback_summary()
            lines.append(f"   • User feedback learned: {fb_stats.get('total_feedback', 0)} items")
            lines.append(f"   • Patterns identified: {fb_stats.get('patterns_learned', 0)}")

            # Prioritizer
            from .learning_prioritizer import get_prioritizer
            p = get_prioritizer()
            p_stats = p.get_stats()
            lines.append(f"   • Learning topics: {p_stats.get('pending', 0)} pending")
            lines.append(f"   • Knowledge gaps: {p_stats.get('knowledge_gaps', 0)} identified")

        except Exception as e:
            lines.append(f"   • Error loading stats: {str(e)[:50]}")

        lines.append("")
        return lines

    def _get_actions_summary(self) -> List[str]:
        """Get actions summary section."""
        lines = ["🎯 ACTIONS TAKEN", "-" * 40]

        try:
            from .audit_logger import get_audit_logger
            logger = get_audit_logger()
            today = datetime.now() - timedelta(days=1)
            logs = logger.get_logs(since=today, limit=500)

            # Count by type
            by_action = {}
            for log in logs:
                action = log.get("action_type", "unknown")
                by_action[action] = by_action.get(action, 0) + 1

            lines.append(f"   • Total actions: {len(logs)}")

            # Approvals
            requires_approval = [l for l in logs if l.get("requires_approval")]
            approved = [l for l in requires_approval if l.get("approved") is True]
            denied = [l for l in requires_approval if l.get("approved") is False]
            autonomous = [l for l in logs if not l.get("requires_approval")]

            lines.append(f"   • Approved: {len(approved)} actions")
            lines.append(f"   • Denied: {len(denied)} actions")
            lines.append(f"   • Autonomous: {len(autonomous)} actions")

        except Exception as e:
            lines.append(f"   • Error loading stats: {str(e)[:50]}")

        lines.append("")
        return lines

    def _get_insights_summary(self) -> List[str]:
        """Get insights summary section."""
        lines = ["💡 INSIGHTS", "-" * 40]

        try:
            # Daily intelligence insights
            from .daily_intelligence import get_daily_intelligence
            di = get_daily_intelligence()
            insights = di.get_insights(since=datetime.now() - timedelta(days=1), limit=5)

            if insights:
                for insight in insights[:3]:
                    text = insight.get("insight", "")[:60]
                    lines.append(f"   • {text}")
            else:
                lines.append("   • No insights generated yet")

        except Exception as e:
            lines.append(f"   • Error loading insights: {str(e)[:50]}")

        lines.append("")
        return lines

    def _get_pending_summary(self) -> List[str]:
        """Get pending decisions section."""
        lines = ["⚠️ PENDING DECISIONS", "-" * 40]

        try:
            from .control_center import get_control_center
            cc = get_control_center()
            pending = cc.get_pending()

            if pending:
                for p in pending[:5]:
                    action = p.get("action", "Unknown")[:40]
                    lines.append(f"   • [{p.get('id', '')}] {action}")
            else:
                lines.append("   • No pending decisions")

        except Exception as e:
            lines.append(f"   • Error loading pending: {str(e)[:50]}")

        lines.append("")
        return lines

    def _get_health_summary(self) -> List[str]:
        """Get system health section."""
        lines = ["📈 SYSTEM HEALTH", "-" * 40]

        try:
            # Memory stats
            from .memory_manager import get_memory
            memory = get_memory()
            mem_stats = memory.get_stats()
            lines.append(f"   • Memory entries: {mem_stats.get('total_entries', 0)}")

            # Learning loop
            from .learning_loop import get_learning_loop
            loop = get_learning_loop()
            status = loop.get_status_report()
            lines.append(f"   • Health score: {status.get('health', {}).get('health_score', 0)}/100")
            lines.append(f"   • Total iterations: {status.get('stats', {}).get('total_iterations', 0)}")

        except Exception as e:
            lines.append(f"   • Error loading health: {str(e)[:50]}")

        lines.append("")
        return lines

    # ==================== EXPORT ====================

    def save_report(self, date: Optional[datetime] = None) -> Path:
        """Save report to file."""
        date = date or datetime.now()
        report = self.generate_report(date)

        filename = f"daily_report_{date.strftime('%Y%m%d')}.txt"
        filepath = self.reports_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)

        return filepath

    def get_report_path(self, date: Optional[datetime] = None) -> Path:
        """Get path to saved report."""
        date = date or datetime.now()
        filename = f"daily_report_{date.strftime('%Y%m%d')}.txt"
        return self.reports_dir / filename

    def get_recent_reports(self, days: int = 7) -> List[Dict]:
        """Get recent reports."""
        reports = []
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            path = self.get_report_path(date)
            if path.exists():
                reports.append({
                    "date": date.strftime('%Y-%m-%d'),
                    "path": str(path),
                })
        return reports

    # ==================== JSON REPORT ====================

    def generate_json_report(self, date: Optional[datetime] = None) -> Dict:
        """Generate JSON format report."""
        date = date or datetime.now()

        report = {
            "date": date.strftime('%Y-%m-%d'),
            "generated_at": datetime.now().isoformat(),
            "sections": {},
        }

        # Learning
        try:
            from .web_search_learner import get_web_learner
            learner = get_web_learner()
            report["sections"]["learning"] = learner.get_stats()
        except Exception:
            pass

        # User feedback
        try:
            from .user_feedback import get_feedback_manager
            fm = get_feedback_manager()
            report["sections"]["feedback"] = fm.get_feedback_summary()
        except Exception:
            pass

        # Actions
        try:
            from .audit_logger import get_audit_logger
            logger = get_audit_logger()
            today = datetime.now() - timedelta(days=1)
            logs = logger.get_logs(since=today, limit=500)
            report["sections"]["actions"] = {
                "total": len(logs),
                "by_type": {},
            }
        except Exception:
            pass

        # Pending
        try:
            from .control_center import get_control_center
            cc = get_control_center()
            pending = cc.get_pending()
            report["sections"]["pending"] = {
                "count": len(pending),
                "items": pending[:10],
            }
        except Exception:
            pass

        return report


# ==================== CONVENIENCE FUNCTIONS ====================

_report_generator = None


def get_report_generator() -> DailyReportGenerator:
    """Get singleton report generator instance."""
    global _report_generator
    if _report_generator is None:
        _report_generator = DailyReportGenerator()
    return _report_generator


def generate_daily_report() -> str:
    """Generate daily report."""
    return get_report_generator().generate_report()


def save_daily_report() -> Path:
    """Save daily report to file."""
    return get_report_generator().save_report()


def get_recent_reports(days: int = 7) -> List[Dict]:
    """Get recent reports."""
    return get_report_generator().get_recent_reports(days)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Daily Report Generator")
    print("=" * 50)

    generator = DailyReportGenerator()

    # Generate report
    print("\n" + generator.generate_report())

    # Save report
    path = generator.save_report()
    print(f"\nReport saved to: {path}")
