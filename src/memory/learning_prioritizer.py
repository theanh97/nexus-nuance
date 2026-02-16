"""
Learning Prioritizer
Decides WHAT to learn based on context, impact, and knowledge gaps.

THE CORE PRINCIPLE:
"Not all learning is equal - prioritize what matters."
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from collections import defaultdict
import threading


class LearningPrioritizer:
    """
    Smart prioritization of learning topics.
    Analyzes context and determines what to learn next.
    """

    # Priority factors
    FACTOR_USER_NEED = "user_need"      # What user needs
    FACTOR_PROJECT_CONTEXT = "project"   # Current project
    FACTOR_IMPACT = "impact"             # Potential impact
    FACTOR_KNOWLEDGE_GAP = "gap"         # Missing knowledge
    FACTOR_RELEVANCE = "relevance"       # Relevance to current work
    FACTOR_FRESHNESS = "freshness"       # How recent the knowledge is

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "memory"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "memory"

        self.priorities_file = self.base_path / "learning_priorities.json"
        self.topics_file = self.base_path / "learning_topics.json"

        # Configuration
        self.default_weights = {
            self.FACTOR_USER_NEED: 2.0,
            self.FACTOR_PROJECT_CONTEXT: 1.5,
            self.FACTOR_IMPACT: 1.8,
            self.FACTOR_KNOWLEDGE_GAP: 1.5,
            self.FACTOR_RELEVANCE: 1.3,
            self.FACTOR_FRESHNESS: 1.0,
        }

        # In-memory state
        self.weights = dict(self.default_weights)
        self.learning_topics: List[Dict] = []
        self.knowledge_gaps: Dict[str, float] = {}
        self.priority_history: List[Dict] = []

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load()

    def _load(self):
        """Load prioritization data."""
        if self.priorities_file.exists():
            try:
                with open(self.priorities_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.weights = data.get("weights", self.default_weights)
                    self.priority_history = data.get("history", [])
            except Exception:
                pass

        if self.topics_file.exists():
            try:
                with open(self.topics_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.learning_topics = data.get("topics", [])
                    self.knowledge_gaps = data.get("gaps", {})
            except Exception:
                pass

    def _save(self):
        """Save prioritization data."""
        with self._lock:
            with open(self.priorities_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "weights": self.weights,
                    "history": self.priority_history[-100:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

            with open(self.topics_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "topics": self.learning_topics[-200:],
                    "gaps": self.knowledge_gaps,
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

    # ==================== TOPIC MANAGEMENT ====================

    def add_topic(
        self,
        topic: str,
        category: str = "general",
        source: Optional[str] = None,
        context: Optional[Dict] = None,
    ) -> Dict:
        """
        Add a learning topic.

        Args:
            topic: Topic description
            category: Topic category
            source: Source of this topic
            context: Additional context

        Returns:
            Topic entry
        """
        with self._lock:
            # Check if already exists
            for t in self.learning_topics:
                if t.get("topic") == topic:
                    t["count"] = t.get("count", 0) + 1
                    t["last_seen"] = datetime.now().isoformat()
                    self._save()
                    return t

            topic_entry = {
                "id": f"topic_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.learning_topics)}",
                "topic": topic,
                "category": category,
                "source": source,
                "context": context or {},
                "added_at": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "count": 1,
                "priority_score": 0.0,
                "learned": False,
            }

            self.learning_topics.append(topic_entry)
            self._save()
            return topic_entry

    def mark_learned(self, topic: str):
        """Mark a topic as learned."""
        with self._lock:
            for t in self.learning_topics:
                if t.get("topic") == topic:
                    t["learned"] = True
                    t["learned_at"] = datetime.now().isoformat()
                    self._save()
                    return

    def add_knowledge_gap(self, gap: str, severity: float = 0.5):
        """
        Register a knowledge gap.

        Args:
            gap: Description of the gap
            severity: How critical (0-1)
        """
        with self._lock:
            self.knowledge_gaps[gap] = {
                "severity": severity,
                "identified_at": datetime.now().isoformat(),
                "addressed": False,
            }
            self._save()

    def mark_gap_addressed(self, gap: str):
        """Mark a knowledge gap as addressed."""
        with self._lock:
            if gap in self.knowledge_gaps:
                self.knowledge_gaps[gap]["addressed"] = True
                self.knowledge_gaps[gap]["addressed_at"] = datetime.now().isoformat()
                self._save()

    # ==================== PRIORITIZATION ====================

    def prioritize(
        self,
        context: Optional[Dict] = None,
        max_topics: int = 10,
    ) -> List[Dict]:
        """
        Prioritize learning topics based on context.

        Args:
            context: Current context (project, user needs, etc.)
            max_topics: Maximum topics to return

        Returns:
            Prioritized list of topics with scores
        """
        context = context or {}

        with self._lock:
            scored_topics = []

            for topic in self.learning_topics:
                if topic.get("learned"):
                    continue

                score = self._calculate_priority(topic, context)
                topic["priority_score"] = score
                scored_topics.append(topic)

            # Sort by score
            scored_topics.sort(key=lambda x: x.get("priority_score", 0), reverse=True)

            # Update history
            self.priority_history.append({
                "timestamp": datetime.now().isoformat(),
                "context": context,
                "top_topics": [t.get("topic") for t in scored_topics[:max_topics]],
            })

            self._save()
            return scored_topics[:max_topics]

    def _calculate_priority(self, topic: Dict, context: Dict) -> float:
        """Calculate priority score for a topic."""
        score = 0.0

        # Factor 1: User need
        user_need = context.get("user_needs", [])
        topic_text = topic.get("topic", "").lower()
        for need in user_need:
            if need.lower() in topic_text:
                score += self.weights[self.FACTOR_USER_NEED]

        # Factor 2: Project context
        project = context.get("project", "")
        if project and project.lower() in topic_text:
            score += self.weights[self.FACTOR_PROJECT_CONTEXT]

        # Factor 3: Impact
        impact = topic.get("impact_score", 0)
        score += impact * self.weights[self.FACTOR_IMPACT]

        # Factor 4: Knowledge gap
        for gap in self.knowledge_gaps:
            if not self.knowledge_gaps[gap].get("addressed", False):
                if gap.lower() in topic_text:
                    severity = self.knowledge_gaps[gap].get("severity", 0.5)
                    score += severity * self.weights[self.FACTOR_KNOWLEDGE_GAP]

        # Factor 5: Relevance
        relevance = context.get("relevance", [])
        for rel in relevance:
            if rel.lower() in topic_text:
                score += self.weights[self.FACTOR_RELEVANCE]

        # Factor 6: Freshness
        last_seen = topic.get("last_seen")
        if last_seen:
            try:
                seen_time = datetime.fromisoformat(last_seen)
                hours_ago = (datetime.now() - seen_time).total_seconds() / 3600
                # More recent = higher score (capped at 24 hours)
                freshness_score = max(0, 1 - (hours_ago / 24))
                score += freshness_score * self.weights[self.FACTOR_FRESHNESS]
            except Exception:
                pass

        # Factor 7: Category priority
        category_scores = {
            "security": 2.0,
            "performance": 1.8,
            "reliability": 1.8,
            "ux": 1.5,
            "general": 1.0,
        }
        category = topic.get("category", "general")
        score *= category_scores.get(category, 1.0)

        return round(score, 2)

    # ==================== RECOMMENDATIONS ====================

    def get_next_learning(
        self,
        context: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Get the next recommended learning topic."""
        prioritized = self.prioritize(context, max_topics=1)
        return prioritized[0] if prioritized else None

    def get_recommended_queries(self, context: Optional[Dict] = None) -> List[str]:
        """Get recommended search queries based on priorities."""
        context = context or {}
        queries = []

        # Get top priorities
        priorities = self.prioritize(context, max_topics=5)

        for p in priorities:
            topic = p.get("topic", "")
            # Convert topic to search query
            query = f"{topic} best practices 2024"
            queries.append(query)

        # Add gap-based queries
        for gap, info in self.knowledge_gaps.items():
            if not info.get("addressed", False):
                query = f"how to {gap} best practices"
                queries.append(query)

        return queries[:10]

    # ==================== WEIGHT ADJUSTMENT ====================

    def adjust_weights(self, factor: str, delta: float):
        """Adjust priority weights."""
        if factor in self.weights:
            self.weights[factor] = max(0.1, self.weights[factor] + delta)
            self._save()

    def get_weights(self) -> Dict:
        """Get current weights."""
        return dict(self.weights)

    def reset_weights(self):
        """Reset weights to defaults."""
        self.weights = dict(self.default_weights)
        self._save()

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get prioritizer statistics."""
        total = len(self.learning_topics)
        learned = sum(1 for t in self.learning_topics if t.get("learned"))
        pending = total - learned
        gaps = sum(1 for g in self.knowledge_gaps.values() if not g.get("addressed", False))

        return {
            "total_topics": total,
            "learned": learned,
            "pending": pending,
            "knowledge_gaps": gaps,
            "weights": self.weights,
        }

    def get_topics(self, learned: Optional[bool] = None, limit: int = 20) -> List[Dict]:
        """Get topics with optional filter."""
        topics = self.learning_topics
        if learned is not None:
            topics = [t for t in topics if t.get("learned") == learned]
        return topics[-limit:]

    def get_gaps(self) -> Dict:
        """Get knowledge gaps."""
        return dict(self.knowledge_gaps)

    def generate_report(self) -> str:
        """Generate prioritizer report."""
        stats = self.get_stats()
        gaps = self.get_gaps()

        lines = [
            "# 🎯 Learning Prioritization Report",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Statistics",
            f"- Total Topics: {stats['total_topics']}",
            f"- Learned: {stats['learned']}",
            f"- Pending: {stats['pending']}",
            f"- Knowledge Gaps: {stats['knowledge_gaps']}",
            "",
            "## Current Weights",
        ]

        for factor, weight in self.weights.items():
            lines.append(f"- {factor}: {weight}")

        if gaps:
            lines.extend(["", "## Open Knowledge Gaps"])
            for gap, info in gaps.items():
                if not info.get("addressed"):
                    lines.append(f"- [{info.get('severity')}] {gap}")

        # Add top priorities
        priorities = self.prioritize(max_topics=5)
        if priorities:
            lines.extend(["", "## Top Priorities"])
            for i, p in enumerate(priorities, 1):
                lines.append(f"{i}. {p.get('topic')} (score: {p.get('priority_score')})")

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_prioritizer = None


def get_prioritizer() -> LearningPrioritizer:
    """Get singleton prioritizer instance."""
    global _prioritizer
    if _prioritizer is None:
        _prioritizer = LearningPrioritizer()
    return _prioritizer


def add_learning_topic(topic: str, category: str = "general", source: Optional[str] = None) -> Dict:
    """Add a learning topic."""
    return get_prioritizer().add_topic(topic, category, source)


def get_next_to_learn(context: Optional[Dict] = None) -> Optional[Dict]:
    """Get next topic to learn."""
    return get_prioritizer().get_next_learning(context)


def prioritize_learning(context: Optional[Dict] = None) -> List[Dict]:
    """Prioritize learning topics."""
    return get_prioritizer().prioritize(context)


def get_search_queries(context: Optional[Dict] = None) -> List[str]:
    """Get recommended search queries."""
    return get_prioritizer().get_recommended_queries(context)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Learning Prioritizer")
    print("=" * 50)

    p = LearningPrioritizer()

    # Test topic addition
    print("\nAdding topics...")
    p.add_topic("AI agent architecture", "technical", "web_search")
    p.add_topic("Python async patterns", "coding", "user")
    p.add_topic("Claude Code integration", "tooling", "user")

    # Add knowledge gap
    p.add_knowledge_gap("security best practices", 0.8)

    # Get priorities
    print("\nPrioritizing...")
    context = {
        "project": "automation",
        "user_needs": ["performance", "security"],
        "relevance": ["AI", "automation"],
    }
    priorities = p.prioritize(context, max_topics=5)
    for i, topic in enumerate(priorities, 1):
        print(f"{i}. {topic['topic']} (score: {topic['priority_score']})")

    # Get search queries
    print("\nRecommended queries:")
    queries = p.get_recommended_queries(context)
    for q in queries:
        print(f"- {q}")

    print("\n" + p.generate_report())
