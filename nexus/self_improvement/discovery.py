"""
Improvement Discovery Module
============================

Discovers opportunities for self-improvement by analyzing:
- Code quality issues
- Performance bottlenecks
- User feedback patterns
- Benchmark failures
- Knowledge gaps
"""

import os
import re
import json
import ast
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class ImprovementOpportunity:
    """Represents a discovered improvement opportunity."""

    id: str
    title: str
    description: str
    category: str  # code_quality, performance, bug, feature, learning
    priority: int  # 1-10 (10 = highest)
    expected_value: float  # 0-10
    file_path: Optional[str] = None
    line_range: Optional[Tuple[int, int]] = None
    suggested_fix: Optional[str] = None
    source: str = "discovery"  # discovery, benchmark, feedback, knowledge
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict = field(default_factory=dict)


class ImprovementDiscovery:
    """
    Discovers improvement opportunities in the codebase.

    Strategies:
    1. Static Analysis: Code quality, complexity, patterns
    2. Benchmark Analysis: Failed tests, performance issues
    3. Feedback Analysis: User complaints, corrections
    4. Knowledge Gaps: Missing patterns, outdated info
    5. Dependency Analysis: Outdated packages, security issues
    """

    CATEGORY_CODE_QUALITY = "code_quality"
    CATEGORY_PERFORMANCE = "performance"
    CATEGORY_BUG = "bug"
    CATEGORY_FEATURE = "feature"
    CATEGORY_LEARNING = "learning"
    CATEGORY_SECURITY = "security"

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Storage
        self.discovered: List[ImprovementOpportunity] = []
        self.discovery_file = self.project_root / "data" / "improvements" / "discovered.json"
        self.discovery_file.parent.mkdir(parents=True, exist_ok=True)

        self._load()

    def _load(self):
        """Load previously discovered improvements."""
        if self.discovery_file.exists():
            try:
                with open(self.discovery_file, 'r') as f:
                    data = json.load(f)
                self.discovered = [
                    ImprovementOpportunity(**item)
                    for item in data.get("improvements", [])
                ]
            except Exception as e:
                logger.warning(f"Failed to load discoveries: {e}")
                self.discovered = []

    def _save(self):
        """Save discovered improvements."""
        with self._lock:
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_discovered": len(self.discovered),
                "improvements": [asdict(imp) for imp in self.discovered]
            }
            with open(self.discovery_file, 'w') as f:
                json.dump(data, f, indent=2)

    def discover_all(self) -> List[ImprovementOpportunity]:
        """Run all discovery strategies."""
        opportunities = []

        # Run all discovery strategies
        opportunities.extend(self._discover_code_quality())
        opportunities.extend(self._discover_performance_issues())
        opportunities.extend(self._discover_bugs())
        opportunities.extend(self._discover_learning_gaps())
        opportunities.extend(self._discover_security_issues())

        # Deduplicate and prioritize
        opportunities = self._deduplicate(opportunities)
        opportunities = sorted(opportunities, key=lambda x: x.priority, reverse=True)

        # Add to discovered list
        with self._lock:
            self.discovered.extend(opportunities)
            self._save()

        logger.info(f"Discovered {len(opportunities)} improvement opportunities")
        return opportunities

    def _discover_code_quality(self) -> List[ImprovementOpportunity]:
        """Discover code quality issues through static analysis."""
        opportunities = []

        # Scan Python files
        python_files = list(self.project_root.glob("**/*.py"))
        python_files = [f for f in python_files if "/research/" not in str(f) and "/.venv/" not in str(f)]

        for file_path in python_files[:50]:  # Limit to first 50 files
            try:
                with open(file_path, 'r') as f:
                    content = f.read()

                # Check for long functions
                lines = content.split('\n')
                current_function = None
                function_start = 0

                for i, line in enumerate(lines):
                    if re.match(r'^\s*def\s+(\w+)', line):
                        match = re.match(r'^\s*def\s+(\w+)', line)
                        if current_function and (i - function_start) > 50:
                            opp = ImprovementOpportunity(
                                id=f"cq_{file_path.stem}_{current_function}_{i}",
                                title=f"Long function: {current_function}",
                                description=f"Function '{current_function}' in {file_path.name} is {i - function_start} lines. Consider refactoring.",
                                category=self.CATEGORY_CODE_QUALITY,
                                priority=5,
                                expected_value=6.0,
                                file_path=str(file_path.relative_to(self.project_root)),
                                line_range=(function_start, i),
                                source="static_analysis"
                            )
                            opportunities.append(opp)

                        current_function = match.group(1)
                        function_start = i

                # Check for TODO/FIXME comments
                for i, line in enumerate(lines):
                    if 'TODO' in line or 'FIXME' in line:
                        opp = ImprovementOpportunity(
                            id=f"todo_{file_path.stem}_{i}",
                            title=f"Unresolved TODO/FIXME",
                            description=f"Found unresolved comment at line {i+1}: {line.strip()}",
                            category=self.CATEGORY_CODE_QUALITY,
                            priority=3,
                            expected_value=4.0,
                            file_path=str(file_path.relative_to(self.project_root)),
                            line_range=(i, i+1),
                            source="todo_scan"
                        )
                        opportunities.append(opp)

            except Exception as e:
                logger.debug(f"Error analyzing {file_path}: {e}")

        return opportunities

    def _discover_performance_issues(self) -> List[ImprovementOpportunity]:
        """Discover potential performance issues."""
        opportunities = []

        # Check for common performance anti-patterns
        python_files = list(self.project_root.glob("**/*.py"))
        python_files = [f for f in python_files if "/research/" not in str(f)]

        patterns = [
            (r'for\s+\w+\s+in\s+\w+\.keys\(\):', "Use direct iteration instead of .keys()"),
            (r'if\s+\w+\s+in\s+\w+:\s*\n\s*if\s+\w+\s+in\s+\w+:', "Consider using set intersection"),
            (r'\+\s*=\s*.*\s+in\s+loop', "String concatenation in loop - use list join"),
            (r'sleep\(\d+\)', "Hardcoded sleep - consider adaptive timing"),
        ]

        for file_path in python_files[:30]:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                    lines = content.split('\n')

                for pattern, suggestion in patterns:
                    for i, line in enumerate(lines):
                        if re.search(pattern, line, re.IGNORECASE):
                            opp = ImprovementOpportunity(
                                id=f"perf_{file_path.stem}_{i}",
                                title="Performance pattern detected",
                                description=f"{suggestion} in {file_path.name}:{i+1}",
                                category=self.CATEGORY_PERFORMANCE,
                                priority=4,
                                expected_value=5.0,
                                file_path=str(file_path.relative_to(self.project_root)),
                                line_range=(i, i+1),
                                suggested_fix=suggestion,
                                source="performance_scan"
                            )
                            opportunities.append(opp)

            except Exception as e:
                logger.debug(f"Error scanning {file_path}: {e}")

        return opportunities

    def _discover_bugs(self) -> List[ImprovementOpportunity]:
        """Discover potential bugs through pattern analysis."""
        opportunities = []

        # Check error logs for recent failures
        log_files = list((self.project_root / "data" / "logs").glob("*.log"))

        for log_file in log_files[:5]:
            try:
                with open(log_file, 'r') as f:
                    content = f.read()

                # Find error patterns
                error_pattern = r'ERROR|Exception|Traceback|Failed'
                for match in re.finditer(error_pattern, content):
                    line_num = content[:match.start()].count('\n')
                    context = content[match.start():match.start()+200]

                    opp = ImprovementOpportunity(
                        id=f"bug_{log_file.stem}_{line_num}",
                        title="Error detected in logs",
                        description=f"Found error pattern: {context[:100]}...",
                        category=self.CATEGORY_BUG,
                        priority=8,
                        expected_value=7.0,
                        file_path=str(log_file.relative_to(self.project_root)),
                        source="log_analysis"
                    )
                    opportunities.append(opp)

            except Exception as e:
                logger.debug(f"Error reading log {log_file}: {e}")

        return opportunities[:20]  # Limit to 20

    def _discover_learning_gaps(self) -> List[ImprovementOpportunity]:
        """Discover gaps in the learning system."""
        opportunities = []

        # Check learning state
        learning_state_file = self.project_root / "data" / "state" / "learning_state.json"
        if learning_state_file.exists():
            try:
                with open(learning_state_file, 'r') as f:
                    state = json.load(f)

                # Check for learning streaks without application
                no_improvement_streak = state.get("no_improvement_streak", 0)
                if no_improvement_streak > 50:
                    opp = ImprovementOpportunity(
                        id="learning_stuck",
                        title="Learning system stuck",
                        description=f"Learning has run {no_improvement_streak} iterations without improvements. Need to diversify sources or improve analysis.",
                        category=self.CATEGORY_LEARNING,
                        priority=9,
                        expected_value=9.0,
                        source="learning_analysis"
                    )
                    opportunities.append(opp)

            except Exception as e:
                logger.debug(f"Error reading learning state: {e}")

        return opportunities

    def _discover_security_issues(self) -> List[ImprovementOpportunity]:
        """Discover potential security issues."""
        opportunities = []

        # Check for common security patterns
        python_files = list(self.project_root.glob("**/*.py"))
        python_files = [f for f in python_files if "/research/" not in str(f)]

        security_patterns = [
            (r'eval\s*\(', "Use of eval() - potential code injection"),
            (r'exec\s*\(', "Use of exec() - potential code injection"),
            (r'subprocess\.call.*shell=True', "Shell=True in subprocess - potential injection"),
            (r'password\s*=\s*[\'"]', "Hardcoded password detected"),
            (r'api_key\s*=\s*[\'"][^\'"]+[\'"]', "Hardcoded API key detected"),
        ]

        for file_path in python_files[:30]:
            try:
                with open(file_path, 'r') as f:
                    lines = f.readlines()

                for i, line in enumerate(lines):
                    for pattern, issue in security_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            opp = ImprovementOpportunity(
                                id=f"sec_{file_path.stem}_{i}",
                                title=f"Security issue: {issue}",
                                description=f"Potential security vulnerability in {file_path.name}:{i+1}",
                                category=self.CATEGORY_SECURITY,
                                priority=10,
                                expected_value=10.0,
                                file_path=str(file_path.relative_to(self.project_root)),
                                line_range=(i, i+1),
                                source="security_scan"
                            )
                            opportunities.append(opp)

            except Exception as e:
                logger.debug(f"Error scanning {file_path}: {e}")

        return opportunities

    def _deduplicate(self, opportunities: List[ImprovementOpportunity]) -> List[ImprovementOpportunity]:
        """Remove duplicate opportunities."""
        seen_ids = set()
        unique = []

        for opp in opportunities:
            if opp.id not in seen_ids:
                seen_ids.add(opp.id)
                unique.append(opp)

        return unique

    def get_top_opportunities(self, limit: int = 10) -> List[ImprovementOpportunity]:
        """Get top improvement opportunities by priority and value."""
        return sorted(self.discovered, key=lambda x: (x.priority, x.expected_value), reverse=True)[:limit]

    def mark_addressed(self, opportunity_id: str) -> bool:
        """Mark an opportunity as addressed."""
        with self._lock:
            for i, opp in enumerate(self.discovered):
                if opp.id == opportunity_id:
                    self.discovered.pop(i)
                    self._save()
                    return True
        return False


# Singleton
_discovery: Optional[ImprovementDiscovery] = None


def get_discovery_engine(project_root: str = None) -> ImprovementDiscovery:
    """Get singleton discovery engine."""
    global _discovery
    if _discovery is None:
        _discovery = ImprovementDiscovery(project_root)
    return _discovery


def discover_improvements() -> List[ImprovementOpportunity]:
    """Discover improvement opportunities."""
    return get_discovery_engine().discover_all()
