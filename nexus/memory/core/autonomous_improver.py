"""
Autonomous Improvement System
NEXUS learns from ALL user inputs and continuously improves the system.

THE CORE PRINCIPLE:
"Learn everything valuable, then CREATE or UPDATE the system."
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import hashlib
import threading


class AutonomousImprover:
    """
    NEXUS learns from everything and continuously improves.
    - Learns from user inputs
    - Generates improvements
    - Updates/creates system components
    """

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "autonomous"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "autonomous"

        self.learnings_file = self.base_path / "all_learnings.json"
        self.improvements_file = self.base_path / "improvements.json"
        self.system_updates_file = self.base_path / "system_updates.json"

        # In-memory state
        self.all_learnings: List[Dict] = []
        self.improvements: List[Dict] = []
        self.system_updates: List[Dict] = []

        # Thread safety
        self._lock = threading.RLock()

        # Load data
        self._load()

    def _load(self):
        """Load all learnings."""
        if self.learnings_file.exists():
            try:
                with open(self.learnings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.all_learnings = data.get("learnings", [])
            except Exception:
                pass

        if self.improvements_file.exists():
            try:
                with open(self.improvements_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.improvements = data.get("improvements", [])
            except Exception:
                pass

        if self.system_updates_file.exists():
            try:
                with open(self.system_updates_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.system_updates = data.get("updates", [])
            except Exception:
                pass

    def _save(self):
        """Save all data."""
        with self._lock:
            with open(self.learnings_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "learnings": self.all_learnings[-5000:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

            with open(self.improvements_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "improvements": self.improvements[-1000:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

            with open(self.system_updates_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "updates": self.system_updates[-500:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

    # ==================== CORE LEARNING ====================

    def learn_from_input(
        self,
        input_type: str,
        content: str,
        value_score: float = 0.5,
        context: Optional[Dict] = None,
    ) -> Dict:
        """
        Learn from ANY user input.
        input_type: command, feedback, question, code, file, idea, etc.
        content: the actual input
        value_score: 0-1 how valuable this is
        """
        with self._lock:
            # Check if already learned (avoid duplicates)
            content_hash = hashlib.md5(content.encode()).hexdigest()
            for learning in self.all_learnings[-100:]:
                if learning.get("content_hash") == content_hash:
                    # Update count, don't duplicate
                    learning["seen_count"] = learning.get("seen_count", 0) + 1
                    learning["last_seen"] = datetime.now().isoformat()
                    self._save()
                    return learning

            learning = {
                "id": f"learn_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.all_learnings)}",
                "input_type": input_type,
                "content": content[:2000],  # Truncate long content
                "content_hash": content_hash,
                "value_score": value_score,
                "context": context or {},
                "learned_at": datetime.now().isoformat(),
                "seen_count": 1,
                "used_for_improvement": False,
                "tags": self._extract_tags(content, input_type),
            }

            self.all_learnings.append(learning)

            # If high value, generate improvement
            if value_score >= 0.7:
                self._generate_improvement(learning)

            self._save()
            return learning

    def _extract_tags(self, content: str, input_type: str) -> List[str]:
        """Extract tags from content for better organization."""
        tags = [input_type]

        # Common patterns
        content_lower = content.lower()

        if any(kw in content_lower for kw in ["fix", "bug", "error", "issue"]):
            tags.append("bug_fix")
        if any(kw in content_lower for kw in ["add", "new", "create", "implement"]):
            tags.append("feature")
        if any(kw in content_lower for kw in ["improve", "better", "enhance"]):
            tags.append("improvement")
        if any(kw in content_lower for kw in ["learn", "understand", "know"]):
            tags.append("learning")
        if any(kw in content_lower for kw in ["api", "function", "class", "method"]):
            tags.append("code")
        if any(kw in content_lower for kw in ["data", "store", "memory"]):
            tags.append("data")

        return tags[:5]

    # ==================== IMPROVEMENT GENERATION ====================

    def _generate_improvement(self, learning: Dict):
        """Generate improvement from learning."""
        content = learning.get("content", "")
        input_type = learning.get("input_type", "")
        tags = learning.get("tags", [])

        # Categorize and generate improvement
        improvement = {
            "id": f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.improvements)}",
            "source_learning_id": learning.get("id"),
            "type": input_type,
            "content": content,
            "tags": tags,
            "suggestions": [],
            "created_at": datetime.now().isoformat(),
            "status": "pending",
        }

        # Generate suggestions based on type
        if "bug_fix" in tags or "error" in content.lower():
            improvement["suggestions"].append({
                "type": "fix_bug",
                "description": "Cần fix bug/issue được phát hiện",
                "priority": "high",
            })

        if "feature" in tags:
            improvement["suggestions"].append({
                "type": "add_feature",
                "description": "Thêm feature mới",
                "priority": "medium",
            })

        if "learning" in tags or "know" in content.lower():
            improvement["suggestions"].append({
                "type": "update_knowledge",
                "description": "Cập nhật knowledge base",
                "priority": "low",
            })

        if improvement["suggestions"]:
            self.improvements.append(improvement)

    def run_auto_improvement(self) -> Dict:
        """Run automatic improvement cycle."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "learnings_processed": 0,
            "improvements_generated": 0,
            "updates_created": 0,
        }

        # Process recent learnings
        recent = self.all_learnings[-50:]
        results["learnings_processed"] = len(recent)

        for learning in recent:
            if not learning.get("used_for_improvement") and learning.get("value_score", 0) >= 0.6:
                # Mark as used
                learning["used_for_improvement"] = True

                # Generate improvement if not already
                if learning.get("value_score", 0) >= 0.7:
                    self._generate_improvement(learning)
                    results["improvements_generated"] += 1

        # Create system updates
        if len(self.improvements) > 0:
            pending = [i for i in self.improvements if i.get("status") == "pending"]
            if pending:
                # Create update
                update = {
                    "id": f"update_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.system_updates)}",
                    "timestamp": datetime.now().isoformat(),
                    "improvements_count": len(pending),
                    "details": pending[:5],
                }
                self.system_updates.append(update)
                results["updates_created"] = len(pending)

                # Mark improvements as processed
                for imp in pending:
                    imp["status"] = "processed"

        self._save()
        return results

    # ==================== QUERY ====================

    def get_all_learnings(self, input_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get all learnings with optional filter."""
        learnings = self.all_learnings
        if input_type:
            learnings = [l for l in learnings if l.get("input_type") == input_type]
        return learnings[-limit:]

    def get_improvements(self, status: Optional[str] = None) -> List[Dict]:
        """Get improvements with optional filter."""
        improvements = self.improvements
        if status:
            improvements = [i for i in improvements if i.get("status") == status]
        return improvements

    def get_pending_improvements(self) -> List[Dict]:
        """Get improvements needing attention."""
        return [i for i in self.improvements if i.get("status") == "pending"]

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get comprehensive stats."""
        by_type = {}
        for l in self.all_learnings:
            t = l.get("input_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total_learnings": len(self.all_learnings),
            "by_type": by_type,
            "high_value": sum(1 for l in self.all_learnings if l.get("value_score", 0) >= 0.7),
            "total_improvements": len(self.improvements),
            "pending_improvements": len(self.get_pending_improvements()),
            "system_updates": len(self.system_updates),
        }

    def generate_report(self) -> str:
        """Generate comprehensive report."""
        stats = self.get_stats()

        lines = [
            "🧠 AUTONOMOUS IMPROVEMENT REPORT",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Statistics",
            f"- Total Learnings: {stats['total_learnings']}",
            f"- High Value: {stats['high_value']}",
            f"- Improvements: {stats['total_improvements']}",
            f"- Pending: {stats['pending_improvements']}",
            f"- System Updates: {stats['system_updates']}",
            "",
            "## By Type",
        ]

        for t, count in stats.get("by_type", {}).items():
            lines.append(f"- {t}: {count}")

        # Recent learnings
        recent = self.get_all_learnings(limit=10)
        if recent:
            lines.extend(["", "## Recent Learnings"])
            for l in recent:
                lines.append(f"- [{l.get('input_type')}] {l.get('content', '')[:60]}")

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_improver = None


def get_autonomous_improver() -> AutonomousImprover:
    """Get singleton improver instance."""
    global _improver
    if _improver is None:
        _improver = AutonomousImprover()
    return _improver


def learn_from_input(input_type: str, content: str, value_score: float = 0.5, context: Optional[Dict] = None) -> Dict:
    """Learn from user input."""
    return get_autonomous_improver().learn_from_input(input_type, content, value_score, context)


def run_auto_improvement() -> Dict:
    """Run automatic improvement."""
    return get_autonomous_improver().run_auto_improvement()


def get_autonomous_stats() -> Dict:
    """Get stats."""
    return get_autonomous_improver().get_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    improver = get_autonomous_improver()

    # Test learning
    print("Testing autonomous learning...")

    # Learn from various inputs
    improver.learn_from_input("command", "fix the login bug", 0.9)
    improver.learn_from_input("idea", "add dark mode to dashboard", 0.8)
    improver.learn_from_input("question", "how does the learning system work", 0.6)
    improver.learn_from_input("feedback", "make the output more concise", 0.7)
    improver.learn_from_input("code", "function calculate_score(data): return sum(data)", 0.5)

    # Run improvement
    results = improver.run_auto_improvement()
    print(f"Improvement results: {results}")

    # Report
    print("\n" + improver.generate_report())
