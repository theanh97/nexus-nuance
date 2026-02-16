"""
NEXUS - The Brain of the Dream Team
====================================

Central hub for autonomous software development.

Components:
    - agents: AI agents (ORION, NOVA, PIXEL, etc.)
    - memory: Learning and knowledge storage
    - orchestration: Multi-agent coordination
    - self_improvement: SICA-style self-modification

Quick Start:
    from nexus import NEXUS

    # Initialize
    nexus = NEXUS()

    # Run self-improvement cycle
    nexus.improve()

    # Get status
    print(nexus.status())
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("NEXUS")


class NEXUS:
    """
    The central brain of the Dream Team.

    Capabilities:
    1. Self-improvement through SICA-style cycles
    2. Multi-project orchestration
    3. Cross-project wisdom accumulation
    4. Autonomous learning and adaptation
    """

    VERSION = "2.0.2"
    CODENAME = "Evolution"

    def __init__(self, project_root: str = None):
        if project_root:
            self.root = Path(project_root)
        else:
            self.root = Path.cwd()

        # Initialize components (lazy loading)
        self._improvement_engine = None
        self._wisdom_hub = None
        self._project_orchestrator = None

        logger.info(f"🧠 NEXUS v{self.VERSION} '{self.CODENAME}' initialized")

    @property
    def improvement_engine(self):
        """Lazy load improvement engine."""
        if self._improvement_engine is None:
            from .self_improvement import get_improvement_engine
            self._improvement_engine = get_improvement_engine(str(self.root))
        return self._improvement_engine

    @property
    def wisdom_hub(self):
        """Lazy load wisdom hub."""
        if self._wisdom_hub is None:
            from ..wisdom import get_wisdom_hub
            self._wisdom_hub = get_wisdom_hub(str(self.root))
        return self._wisdom_hub

    @property
    def project_orchestrator(self):
        """Lazy load project orchestrator."""
        if self._project_orchestrator is None:
            from ..projects import get_orchestrator
            self._project_orchestrator = get_orchestrator(str(self.root))
        return self._project_orchestrator

    def improve(self, max_patches: int = 3) -> Dict:
        """
        Run a self-improvement cycle.

        This is the core function that makes NEXUS evolve.
        """
        logger.info("🔄 Starting self-improvement cycle...")

        result = self.improvement_engine.run_cycle(max_patches)

        # Share learnings with wisdom hub
        if result.patches_successful > 0:
            self.wisdom_hub.record_pattern(
                name="self_improvement_success",
                category="meta",
                description=f"Successfully applied {result.patches_successful} improvements",
                project_id="nexus"
            )

        logger.info(f"✅ Cycle complete: {result.patches_successful} improvements applied")

        return {
            "cycle_id": result.cycle_id,
            "improvements": result.patches_successful,
            "score_before": result.benchmark_score_before,
            "score_after": result.benchmark_score_after,
            "delta": result.improvement_delta
        }

    def start_continuous_improvement(self, interval: int = 300, max_cycles: int = 100):
        """Start continuous self-improvement."""
        logger.info(f"🚀 Starting continuous improvement (interval={interval}s)")
        self.improvement_engine.run_continuous(interval, max_cycles)

    def stop(self):
        """Stop all continuous processes."""
        if self._improvement_engine:
            self._improvement_engine.stop()
        logger.info("🛑 NEXUS stopped")

    def create_project(self, name: str, description: str, project_type: str = "automation") -> Dict:
        """Create a new project."""
        project = self.project_orchestrator.create_project(
            name=name,
            description=description,
            project_type=project_type
        )

        # Get recommendations from wisdom hub
        recommendations = self.wisdom_hub.get_recommendations_for_project(project_type)

        return {
            "project": asdict(project) if hasattr(project, '__dict__') else project,
            "recommendations": recommendations
        }

    def status(self) -> Dict:
        """Get comprehensive status."""
        status = {
            "version": self.VERSION,
            "codename": self.CODENAME,
            "timestamp": datetime.now().isoformat(),
            "root": str(self.root),
            "components": {
                "improvement_engine": "loaded" if self._improvement_engine else "not loaded",
                "wisdom_hub": "loaded" if self._wisdom_hub else "not loaded",
                "project_orchestrator": "loaded" if self._project_orchestrator else "not loaded"
            }
        }

        if self._improvement_engine:
            status["improvement"] = self._improvement_engine.get_status()

        if self._wisdom_hub:
            status["wisdom"] = {
                "patterns": len(self._wisdom_hub.patterns),
                "insights": len(self._wisdom_hub.insights),
                "lessons": len(self._wisdom_hub.lessons)
            }

        if self._project_orchestrator:
            status["projects"] = self._project_orchestrator.get_statistics()

        return status

    def report(self) -> str:
        """Generate comprehensive report."""
        lines = [
            "# 🧠 NEXUS STATUS REPORT",
            f"Version: {self.VERSION} '{self.CODENAME}'",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        # Add component reports
        if self._improvement_engine:
            lines.append(self._improvement_engine.get_report())
            lines.append("")

        if self._wisdom_hub:
            lines.append(self._wisdom_hub.get_wisdom_summary())
            lines.append("")

        return "\n".join(lines)


# For easy import
from dataclasses import asdict

# Global instance
_nexus: Optional[NEXUS] = None


def get_nexus(project_root: str = None) -> NEXUS:
    """Get global NEXUS instance."""
    global _nexus
    if _nexus is None:
        _nexus = NEXUS(project_root)
    return _nexus


__all__ = [
    "NEXUS",
    "get_nexus",
]
