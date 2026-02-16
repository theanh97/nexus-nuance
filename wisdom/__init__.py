"""
NEXUS Wisdom Hub
================

Cross-project learning system that accumulates wisdom from ALL projects.

Philosophy:
    "From many projects, one wisdom. From one wisdom, better projects."

Features:
    - Pattern extraction from successful projects
    - Failure analysis to avoid repeating mistakes
    - Cross-project knowledge transfer
    - Insight generation
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
import threading
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class Pattern:
    """A learned pattern."""
    pattern_id: str
    name: str
    category: str  # code, architecture, workflow, optimization
    description: str
    code_example: Optional[str]
    success_count: int
    failure_count: int
    confidence: float
    projects_used_in: List[str]
    first_seen: str
    last_seen: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class Insight:
    """An AI-generated insight."""
    insight_id: str
    title: str
    description: str
    category: str
    evidence: List[str]
    actionable: bool
    action_items: List[str]
    confidence: float
    created_at: str
    projects_involved: List[str]


@dataclass
class Lesson:
    """A lesson learned from failure."""
    lesson_id: str
    what_failed: str
    why_it_failed: str
    how_to_avoid: str
    severity: str  # critical, major, minor
    project: str
    timestamp: str


class WisdomHub:
    """
    Central hub for cross-project wisdom.

    Responsibilities:
    1. Collect patterns from all projects
    2. Analyze successes and failures
    3. Generate insights
    4. Distribute wisdom to projects
    """

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Storage paths
        self.wisdom_dir = self.project_root / "wisdom"
        self.patterns_file = self.wisdom_dir / "patterns" / "patterns.json"
        self.insights_file = self.wisdom_dir / "insights" / "insights.json"
        self.lessons_file = self.wisdom_dir / "patterns" / "lessons.json"

        # Ensure directories exist
        for f in [self.patterns_file, self.insights_file, self.lessons_file]:
            f.parent.mkdir(parents=True, exist_ok=True)

        # Data storage
        self.patterns: Dict[str, Pattern] = {}
        self.insights: List[Insight] = []
        self.lessons: List[Lesson] = []

        self._load()

    def _load(self):
        """Load wisdom from files."""
        # Load patterns
        if self.patterns_file.exists():
            try:
                with open(self.patterns_file, 'r') as f:
                    data = json.load(f)
                self.patterns = {k: Pattern(**v) for k, v in data.get("patterns", {}).items()}
            except Exception as e:
                logger.warning(f"Failed to load patterns: {e}")

        # Load insights
        if self.insights_file.exists():
            try:
                with open(self.insights_file, 'r') as f:
                    data = json.load(f)
                self.insights = [Insight(**i) for i in data.get("insights", [])]
            except Exception as e:
                logger.warning(f"Failed to load insights: {e}")

        # Load lessons
        if self.lessons_file.exists():
            try:
                with open(self.lessons_file, 'r') as f:
                    data = json.load(f)
                self.lessons = [Lesson(**l) for l in data.get("lessons", [])]
            except Exception as e:
                logger.warning(f"Failed to load lessons: {e}")

    def _save(self):
        """Save wisdom to files."""
        with self._lock:
            # Save patterns
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_patterns": len(self.patterns),
                "patterns": {k: asdict(v) for k, v in self.patterns.items()}
            }
            with open(self.patterns_file, 'w') as f:
                json.dump(data, f, indent=2)

            # Save insights
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_insights": len(self.insights),
                "insights": [asdict(i) for i in self.insights]
            }
            with open(self.insights_file, 'w') as f:
                json.dump(data, f, indent=2)

            # Save lessons
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_lessons": len(self.lessons),
                "lessons": [asdict(l) for l in self.lessons]
            }
            with open(self.lessons_file, 'w') as f:
                json.dump(data, f, indent=2)

    # ==================== PATTERN MANAGEMENT ====================

    def record_pattern(
        self,
        name: str,
        category: str,
        description: str,
        code_example: str = None,
        project_id: str = "nexus",
        success: bool = True
    ) -> Pattern:
        """Record a pattern (success or failure)."""
        pattern_id = f"PAT-{name.replace(' ', '_').lower()}_{len(self.patterns)}"

        if pattern_id in self.patterns:
            # Update existing pattern
            pattern = self.patterns[pattern_id]
            if success:
                pattern.success_count += 1
            else:
                pattern.failure_count += 1
            pattern.confidence = pattern.success_count / (pattern.success_count + pattern.failure_count)
            pattern.last_seen = datetime.now().isoformat()
            if project_id not in pattern.projects_used_in:
                pattern.projects_used_in.append(project_id)
        else:
            # Create new pattern
            now = datetime.now().isoformat()
            pattern = Pattern(
                pattern_id=pattern_id,
                name=name,
                category=category,
                description=description,
                code_example=code_example,
                success_count=1 if success else 0,
                failure_count=0 if success else 1,
                confidence=1.0 if success else 0.0,
                projects_used_in=[project_id],
                first_seen=now,
                last_seen=now
            )
            self.patterns[pattern_id] = pattern

        self._save()
        return pattern

    def get_patterns_by_category(self, category: str) -> List[Pattern]:
        """Get patterns by category."""
        return [p for p in self.patterns.values() if p.category == category]

    def get_best_patterns(self, min_confidence: float = 0.7, limit: int = 10) -> List[Pattern]:
        """Get best patterns sorted by confidence and usage."""
        patterns = [
            p for p in self.patterns.values()
            if p.confidence >= min_confidence and p.success_count >= 2
        ]
        return sorted(patterns, key=lambda x: (x.confidence, x.success_count), reverse=True)[:limit]

    # ==================== INSIGHT MANAGEMENT ====================

    def create_insight(
        self,
        title: str,
        description: str,
        category: str,
        evidence: List[str],
        action_items: List[str],
        projects_involved: List[str],
        confidence: float = 0.7
    ) -> Insight:
        """Create a new insight."""
        insight_num = len(self.insights) + 1
        insight_id = f"INS-{insight_num:04d}"

        insight = Insight(
            insight_id=insight_id,
            title=title,
            description=description,
            category=category,
            evidence=evidence,
            actionable=len(action_items) > 0,
            action_items=action_items,
            confidence=confidence,
            created_at=datetime.now().isoformat(),
            projects_involved=projects_involved
        )

        self.insights.append(insight)
        self._save()

        return insight

    def get_actionable_insights(self) -> List[Insight]:
        """Get insights that have action items."""
        return [i for i in self.insights if i.actionable]

    # ==================== LESSON MANAGEMENT ====================

    def record_lesson(
        self,
        what_failed: str,
        why_it_failed: str,
        how_to_avoid: str,
        severity: str,
        project: str
    ) -> Lesson:
        """Record a lesson learned from failure."""
        lesson_num = len(self.lessons) + 1
        lesson_id = f"LES-{lesson_num:04d}"

        lesson = Lesson(
            lesson_id=lesson_id,
            what_failed=what_failed,
            why_it_failed=why_it_failed,
            how_to_avoid=how_to_avoid,
            severity=severity,
            project=project,
            timestamp=datetime.now().isoformat()
        )

        self.lessons.append(lesson)
        self._save()

        return lesson

    def get_critical_lessons(self) -> List[Lesson]:
        """Get critical lessons (must avoid)."""
        return [l for l in self.lessons if l.severity == "critical"]

    # ==================== CROSS-PROJECT LEARNING ====================

    def learn_from_project(self, project_id: str, project_data: Dict):
        """
        Extract wisdom from a project's data.

        Args:
            project_id: Unique project identifier
            project_data: Dict containing:
                - patterns_used: List of patterns used
                - improvements_made: List of improvements
                - failures: List of failures
                - benchmarks: Benchmark results
        """
        # Extract successful patterns
        for pattern_data in project_data.get("patterns_used", []):
            self.record_pattern(
                name=pattern_data.get("name", "unknown"),
                category=pattern_data.get("category", "code"),
                description=pattern_data.get("description", ""),
                code_example=pattern_data.get("code_example"),
                project_id=project_id,
                success=pattern_data.get("success", True)
            )

        # Extract lessons from failures
        for failure in project_data.get("failures", []):
            self.record_lesson(
                what_failed=failure.get("what", "unknown"),
                why_it_failed=failure.get("why", "unknown"),
                how_to_avoid=failure.get("avoidance", "unknown"),
                severity=failure.get("severity", "major"),
                project=project_id
            )

        # Generate insights from improvements
        improvements = project_data.get("improvements_made", [])
        if len(improvements) >= 3:
            self._generate_insights_from_improvements(project_id, improvements)

        logger.info(f"Learned from project {project_id}: {len(project_data.get('patterns_used', []))} patterns, "
                   f"{len(project_data.get('failures', []))} lessons")

    def _generate_insights_from_improvements(self, project_id: str, improvements: List[Dict]):
        """Generate insights from a series of improvements."""
        # Group improvements by category
        by_category = defaultdict(list)
        for imp in improvements:
            by_category[imp.get("category", "general")].append(imp)

        # Generate insights for categories with multiple improvements
        for category, imps in by_category.items():
            if len(imps) >= 3:
                # Extract common patterns
                common_elements = self._extract_common_elements(imps)

                if common_elements:
                    self.create_insight(
                        title=f"Pattern detected in {category}",
                        description=f"Multiple successful improvements in {category} share common elements: {', '.join(common_elements)}",
                        category=category,
                        evidence=[i.get("description", "")[:100] for i in imps[:3]],
                        action_items=[f"Apply {elem} pattern to similar {category} issues" for elem in common_elements],
                        projects_involved=[project_id],
                        confidence=0.6
                    )

    def _extract_common_elements(self, items: List[Dict]) -> List[str]:
        """Extract common elements from a list of items."""
        # Simple keyword extraction
        all_words = []
        for item in items:
            desc = item.get("description", "").lower()
            all_words.extend(desc.split())

        # Find words that appear frequently
        word_counts = defaultdict(int)
        for word in all_words:
            if len(word) > 4:  # Skip short words
                word_counts[word] += 1

        common = [word for word, count in word_counts.items() if count >= 2]
        return common[:3]

    def get_recommendations_for_project(self, project_type: str = "general") -> Dict:
        """
        Get recommendations for a new project based on accumulated wisdom.

        Returns:
            Dict with:
                - recommended_patterns: List of patterns to use
                - lessons_to_avoid: List of mistakes to avoid
                - insights_to_consider: Relevant insights
        """
        # Get best patterns
        recommended = self.get_best_patterns(min_confidence=0.6, limit=5)

        # Get relevant lessons
        lessons_to_avoid = [l for l in self.lessons if l.severity in ["critical", "major"]]

        # Get actionable insights
        insights = self.get_actionable_insights()[:5]

        return {
            "recommended_patterns": [
                {"name": p.name, "description": p.description, "confidence": p.confidence}
                for p in recommended
            ],
            "lessons_to_avoid": [
                {"what": l.what_failed, "avoidance": l.how_to_avoid}
                for l in lessons_to_avoid[:10]
            ],
            "insights_to_consider": [
                {"title": i.title, "actions": i.action_items}
                for i in insights
            ]
        }

    def get_wisdom_summary(self) -> str:
        """Generate a wisdom summary."""
        lines = [
            "# 💎 WISDOM HUB SUMMARY",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 📊 Statistics",
            f"- Total patterns: {len(self.patterns)}",
            f"- Total insights: {len(self.insights)}",
            f"- Total lessons: {len(self.lessons)}",
            "",
            "## ✅ Best Patterns",
        ]

        for pattern in self.get_best_patterns(limit=5):
            lines.append(f"- **{pattern.name}** ({pattern.confidence:.0%} confidence, used in {len(pattern.projects_used_in)} projects)")

        lines.extend([
            "",
            "## 🎓 Recent Lessons",
        ])

        for lesson in self.lessons[-5:]:
            severity_icon = {"critical": "🔴", "major": "🟠", "minor": "🟡"}.get(lesson.severity, "⚪")
            lines.append(f"- {severity_icon} **{lesson.what_failed}**: {lesson.how_to_avoid}")

        lines.extend([
            "",
            "## 💡 Recent Insights",
        ])

        for insight in self.insights[-3:]:
            lines.append(f"- **{insight.title}**: {insight.description[:100]}...")

        return "\n".join(lines)


# Singleton
_hub: Optional[WisdomHub] = None


def get_wisdom_hub(project_root: str = None) -> WisdomHub:
    """Get singleton wisdom hub."""
    global _hub
    if _hub is None:
        _hub = WisdomHub(project_root)
    return _hub


# Convenience functions
def record_pattern(name: str, category: str, description: str, **kwargs) -> Pattern:
    """Record a pattern."""
    return get_wisdom_hub().record_pattern(name, category, description, **kwargs)


def record_lesson(what_failed: str, why_it_failed: str, how_to_avoid: str, **kwargs) -> Lesson:
    """Record a lesson."""
    return get_wisdom_hub().record_lesson(what_failed, why_it_failed, how_to_avoid, **kwargs)


def get_recommendations(project_type: str = "general") -> Dict:
    """Get recommendations for a project."""
    return get_wisdom_hub().get_recommendations_for_project(project_type)


def wisdom_summary() -> str:
    """Get wisdom summary."""
    return get_wisdom_hub().get_wisdom_summary()
