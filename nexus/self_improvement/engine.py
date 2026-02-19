"""
Self-Improvement Engine
=======================

Main orchestrator for the self-improvement cycle.

Loop:
    DISCOVER → ANALYZE → GENERATE → APPLY → MEASURE → LEARN

Based on SICA (Self-Improving Coding Agent) research.
"""

import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import threading

from .discovery import ImprovementDiscovery, discover_improvements, get_discovery_engine
from .benchmark import BenchmarkRunner, run_benchmarks, get_benchmark_runner
from .patch_generator import PatchGenerator, generate_patch, get_patch_generator
from .safe_apply import SafeApplier, apply_patch_safely, get_safe_applier
from .archive import ArchiveAnalyzer, analyze_archive, get_archive_analyzer

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """Result of a self-improvement cycle."""
    cycle_id: str
    iteration: int
    opportunities_found: int
    patches_generated: int
    patches_applied: int
    patches_successful: int
    benchmark_score_before: float
    benchmark_score_after: float
    improvement_delta: float
    duration_seconds: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    details: Dict = field(default_factory=dict)


class SelfImprovementEngine:
    """
    Main self-improvement engine.

    Orchestrates the complete self-improvement cycle:
    1. DISCOVER: Find improvement opportunities
    2. ANALYZE: Prioritize by expected value
    3. GENERATE: Create code patches
    4. APPLY: Safely apply changes
    5. MEASURE: Run benchmarks
    6. LEARN: Update wisdom/archive
    """

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()
        self._running = False

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Initialize components
        self.discovery = get_discovery_engine(str(self.project_root))
        self.benchmark = get_benchmark_runner(str(self.project_root))
        self.generator = get_patch_generator(str(self.project_root))
        self.applier = get_safe_applier(str(self.project_root))
        self.archive = get_archive_analyzer(str(self.project_root))

        # Storage
        self.cycle_history: List[CycleResult] = []
        self.history_file = self.project_root / "data" / "self_improvement" / "history.json"
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        self.state_file = self.project_root / "data" / "state" / "self_improvement_state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self._load()

    def _load(self):
        """Load history and state."""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.cycle_history = [CycleResult(**c) for c in data.get("cycles", [])]
            except Exception as e:
                logger.warning(f"Failed to load cycle history: {e}")

    def _save(self):
        """Save history and state."""
        with self._lock:
            # Save history
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_cycles": len(self.cycle_history),
                "cycles": [asdict(c) for c in self.cycle_history[-100:]]
            }
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            # Save state
            state = {
                "running": self._running,
                "last_cycle": self.cycle_history[-1].timestamp if self.cycle_history else None,
                "total_cycles": len(self.cycle_history),
                "total_improvements": sum(c.patches_successful for c in self.cycle_history),
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)

    def run_cycle(self, max_patches: int = 3) -> CycleResult:
        """
        Run a single self-improvement cycle.

        Args:
            max_patches: Maximum number of patches to apply per cycle

        Returns:
            CycleResult with details of the cycle
        """
        cycle_start = time.time()
        iteration = len(self.cycle_history) + 1
        cycle_id = f"CYCLE-{iteration:04d}"

        logger.info(f"🔄 Starting self-improvement cycle {iteration}")

        # Step 1: DISCOVER
        logger.info("  📡 DISCOVER: Finding improvement opportunities...")
        opportunities = discover_improvements()
        opportunities_found = len(opportunities)

        # Step 2: ANALYZE - Get top opportunities
        logger.info(f"  🔍 ANALYZE: Prioritizing {opportunities_found} opportunities...")
        top_opportunities = self.discovery.get_top_opportunities(limit=max_patches)

        # Step 3: Run benchmarks BEFORE
        logger.info("  📊 MEASURE (before): Running benchmarks...")
        before_results = run_benchmarks()
        score_before = sum(r.score for r in before_results) / len(before_results) if before_results else 0

        # Step 4: GENERATE patches
        logger.info("  🛠️ GENERATE: Creating patches...")
        patches = []
        for opp in top_opportunities:
            patch = generate_patch(asdict(opp))
            if patch:
                patches.append(patch)
        patches_generated = len(patches)

        # Step 5: APPLY patches
        logger.info(f"  ⚡ APPLY: Applying {len(patches)} patches...")
        apply_results = []
        successful_patches = 0

        for patch in patches:
            result = apply_patch_safely(asdict(patch))
            apply_results.append(result)
            if result.success:
                successful_patches += 1

                # Record in archive
                self.archive.record_improvement(
                    improvement_type=patch.metadata.get("category", "unknown"),
                    success=True,
                    score_before=score_before,
                    score_after=score_before + 1,  # Will be updated
                    patch_id=patch.patch_id,
                    file_changed=patch.file_path,
                    notes=patch.description
                )

        # Step 6: MEASURE - Run benchmarks AFTER
        logger.info("  📊 MEASURE (after): Running benchmarks...")
        after_results = run_benchmarks()
        score_after = sum(r.score for r in after_results) / len(after_results) if after_results else 0

        # Step 7: LEARN - Update wisdom
        improvement_delta = score_after - score_before
        logger.info(f"  🧠 LEARN: Improvement delta: {improvement_delta:+.2f}")

        # Create cycle result
        duration = time.time() - cycle_start
        result = CycleResult(
            cycle_id=cycle_id,
            iteration=iteration,
            opportunities_found=opportunities_found,
            patches_generated=patches_generated,
            patches_applied=len(apply_results),
            patches_successful=successful_patches,
            benchmark_score_before=round(score_before, 2),
            benchmark_score_after=round(score_after, 2),
            improvement_delta=round(improvement_delta, 2),
            duration_seconds=round(duration, 2),
            details={
                "top_opportunities": [{"id": o.id, "title": o.title} for o in top_opportunities],
                "patch_results": [{"patch_id": r.patch_id, "success": r.success} for r in apply_results]
            }
        )

        with self._lock:
            self.cycle_history.append(result)
            self._save()

        logger.info(f"✅ Cycle {iteration} complete: {successful_patches}/{patches_generated} patches applied, "
                   f"score: {score_before:.1f} → {score_after:.1f}")

        return result

    def run_continuous(self, interval_seconds: int = 300, max_cycles: int = 100):
        """
        Run continuous self-improvement cycles.

        Args:
            interval_seconds: Time between cycles
            max_cycles: Maximum cycles to run
        """
        self._running = True
        cycles_run = 0

        logger.info(f"🚀 Starting continuous self-improvement (interval={interval_seconds}s, max={max_cycles})")

        while self._running and cycles_run < max_cycles:
            try:
                self.run_cycle()
                cycles_run += 1

                if cycles_run < max_cycles:
                    logger.info(f"⏳ Waiting {interval_seconds}s until next cycle...")
                    time.sleep(interval_seconds)

            except Exception as e:
                logger.error(f"❌ Cycle failed: {e}")
                time.sleep(60)  # Wait before retrying

        logger.info(f"🛑 Continuous improvement stopped after {cycles_run} cycles")

    def stop(self):
        """Stop continuous improvement."""
        self._running = False
        self._save()

    def get_status(self) -> Dict:
        """Get current status of the engine."""
        return {
            "running": self._running,
            "total_cycles": len(self.cycle_history),
            "total_improvements": sum(c.patches_successful for c in self.cycle_history),
            "last_cycle": self.cycle_history[-1].timestamp if self.cycle_history else None,
            "average_improvement": sum(c.improvement_delta for c in self.cycle_history) / max(len(self.cycle_history), 1),
            "archive_stats": self.archive.get_statistics(),
        }

    def get_report(self) -> str:
        """Generate a comprehensive report."""
        stats = self.get_status()
        archive_summary = self.archive.get_improvement_summary()

        lines = [
            "# 🔄 SELF-IMPROVEMENT ENGINE REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 📊 Overall Status",
            f"- Status: {'🟢 Running' if self._running else '🔴 Stopped'}",
            f"- Total cycles: {stats['total_cycles']}",
            f"- Total improvements: {stats['total_improvements']}",
            f"- Average improvement per cycle: {stats['average_improvement']:.2f}",
            "",
            "## 📈 Recent Cycles",
        ]

        for cycle in self.cycle_history[-5:]:
            status = "✅" if cycle.patches_successful > 0 else "○"
            lines.append(
                f"- {status} {cycle.cycle_id}: {cycle.patches_successful}/{cycle.patches_generated} patches, "
                f"score {cycle.benchmark_score_before} → {cycle.benchmark_score_after} ({cycle.improvement_delta:+.1f})"
            )

        lines.extend([
            "",
            "## 📁 Archive Summary",
            archive_summary,
        ])

        return "\n".join(lines)


# Singleton
_engine: Optional[SelfImprovementEngine] = None


def get_improvement_engine(project_root: str = None) -> SelfImprovementEngine:
    """Get singleton improvement engine."""
    global _engine
    if _engine is None:
        _engine = SelfImprovementEngine(project_root)
    return _engine


def run_self_improvement_cycle(max_patches: int = 3) -> CycleResult:
    """Run a single self-improvement cycle."""
    return get_improvement_engine().run_cycle(max_patches)
