"""
Benchmark Runner Module
=======================

Runs benchmarks to measure system performance and validate improvements.
"""

import json
import time
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""
    benchmark_id: str
    name: str
    passed: bool
    score: float
    duration_seconds: float
    details: Dict = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class BenchmarkRunner:
    """
    Runs various benchmarks to measure system performance.

    Benchmark types:
    1. Unit tests - pytest
    2. Integration tests
    3. Performance benchmarks
    4. Learning benchmarks
    5. Custom benchmarks
    """

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Storage
        self.results: List[BenchmarkResult] = []
        self.results_dir = self.project_root / "data" / "benchmarks"
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self._load()

    def _load(self):
        """Load previous results."""
        results_file = self.results_dir / "results.json"
        if results_file.exists():
            try:
                with open(results_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.results = [BenchmarkResult(**r) for r in data.get("results", [])]
            except Exception as e:
                logger.warning(f"Failed to load benchmark results: {e}")

    def _save(self):
        """Save results."""
        with self._lock:
            results_file = self.results_dir / "results.json"
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_results": len(self.results),
                "results": [asdict(r) for r in self.results[-100:]]  # Keep last 100
            }
            with open(results_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

    def run_all_benchmarks(self) -> List[BenchmarkResult]:
        """Run all available benchmarks."""
        results = []

        # Run pytest
        results.append(self._run_pytest())

        # Run learning benchmark
        results.append(self._run_learning_benchmark())

        # Run performance benchmark
        results.append(self._run_performance_benchmark())

        # Run memory benchmark
        results.append(self._run_memory_benchmark())

        # Store results
        with self._lock:
            self.results.extend(results)
            self._save()

        return results

    def _run_pytest(self) -> BenchmarkResult:
        """Run pytest unit tests."""
        start_time = time.time()

        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "-q"],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=120
            )

            duration = time.time() - start_time
            passed = result.returncode == 0

            # Parse output for score
            output = result.stdout + result.stderr
            passed_tests = output.count(" PASSED")
            failed_tests = output.count(" FAILED")
            total = passed_tests + failed_tests

            score = (passed_tests / total * 100) if total > 0 else 0

            return BenchmarkResult(
                benchmark_id="pytest",
                name="Unit Tests",
                passed=passed,
                score=score,
                duration_seconds=round(duration, 2),
                details={
                    "passed": passed_tests,
                    "failed": failed_tests,
                    "total": total
                },
                error=None if passed else result.stderr[:500]
            )

        except subprocess.TimeoutExpired:
            return BenchmarkResult(
                benchmark_id="pytest",
                name="Unit Tests",
                passed=False,
                score=0,
                duration_seconds=120,
                error="Benchmark timed out"
            )
        except Exception as e:
            return BenchmarkResult(
                benchmark_id="pytest",
                name="Unit Tests",
                passed=False,
                score=0,
                duration_seconds=time.time() - start_time,
                error=str(e)
            )

    def _run_learning_benchmark(self) -> BenchmarkResult:
        """Benchmark the learning system."""
        start_time = time.time()

        try:
            # Check learning stats
            state_file = self.project_root / "data" / "state" / "learning_state.json"
            if state_file.exists():
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)

                stats = state.get("stats", {})
                total_learned = stats.get("knowledge_items_learned", 0)
                streak = state.get("no_improvement_streak", 0)

                # Score based on learning efficiency
                score = min(100, total_learned / 2)  # Max 100 at 200 items
                if streak > 100:
                    score = max(0, score - 50)  # Penalty for stuck learning

                return BenchmarkResult(
                    benchmark_id="learning",
                    name="Learning System",
                    passed=streak < 100 and total_learned > 10,
                    score=round(score, 1),
                    duration_seconds=round(time.time() - start_time, 2),
                    details={
                        "total_learned": total_learned,
                        "no_improvement_streak": streak
                    }
                )

            return BenchmarkResult(
                benchmark_id="learning",
                name="Learning System",
                passed=False,
                score=0,
                duration_seconds=round(time.time() - start_time, 2),
                error="Learning state file not found"
            )

        except Exception as e:
            return BenchmarkResult(
                benchmark_id="learning",
                name="Learning System",
                passed=False,
                score=0,
                duration_seconds=round(time.time() - start_time, 2),
                error=str(e)
            )

    def _run_performance_benchmark(self) -> BenchmarkResult:
        """Benchmark system performance."""
        start_time = time.time()

        try:
            # Simple performance test - how fast can we scan the codebase?
            python_files = list(self.project_root.glob("**/*.py"))
            python_files = [f for f in python_files if "/research/" not in str(f)]

            scan_time = time.time() - start_time
            files_per_second = len(python_files) / max(scan_time, 0.001)

            # Score based on scan speed
            score = min(100, files_per_second * 10)

            return BenchmarkResult(
                benchmark_id="performance",
                name="Performance Scan",
                passed=files_per_second > 10,
                score=round(score, 1),
                duration_seconds=round(scan_time, 2),
                details={
                    "files_scanned": len(python_files),
                    "files_per_second": round(files_per_second, 1)
                }
            )

        except Exception as e:
            return BenchmarkResult(
                benchmark_id="performance",
                name="Performance Scan",
                passed=False,
                score=0,
                duration_seconds=round(time.time() - start_time, 2),
                error=str(e)
            )

    def _run_memory_benchmark(self) -> BenchmarkResult:
        """Benchmark memory system."""
        start_time = time.time()

        try:
            # Check memory storage
            memory_dir = self.project_root / "data" / "memory"
            if memory_dir.exists():
                knowledge_files = list(memory_dir.glob("**/*.json"))

                # Check knowledge quality
                total_items = 0
                high_quality = 0

                for kf in knowledge_files[:10]:
                    try:
                        with open(kf, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        items = data if isinstance(data, list) else [data]
                        total_items += len(items)
                        high_quality += len([i for i in items if isinstance(i, dict) and i.get("quality", 0) > 0.7])
                    except:
                        pass

                quality_ratio = high_quality / max(total_items, 1)
                score = quality_ratio * 100

                return BenchmarkResult(
                    benchmark_id="memory",
                    name="Memory System",
                    passed=total_items > 0,
                    score=round(score, 1),
                    duration_seconds=round(time.time() - start_time, 2),
                    details={
                        "total_items": total_items,
                        "high_quality_items": high_quality,
                        "quality_ratio": round(quality_ratio, 2)
                    }
                )

            return BenchmarkResult(
                benchmark_id="memory",
                name="Memory System",
                passed=False,
                score=0,
                duration_seconds=round(time.time() - start_time, 2),
                error="Memory directory not found"
            )

        except Exception as e:
            return BenchmarkResult(
                benchmark_id="memory",
                name="Memory System",
                passed=False,
                score=0,
                duration_seconds=round(time.time() - start_time, 2),
                error=str(e)
            )

    def get_latest_results(self) -> Dict[str, BenchmarkResult]:
        """Get latest results by benchmark type."""
        latest = {}
        for result in reversed(self.results):
            if result.benchmark_id not in latest:
                latest[result.benchmark_id] = result
        return latest

    def get_score_trend(self, benchmark_id: str, limit: int = 10) -> List[float]:
        """Get score trend for a benchmark."""
        scores = []
        for result in reversed(self.results):
            if result.benchmark_id == benchmark_id:
                scores.append(result.score)
                if len(scores) >= limit:
                    break
        return list(reversed(scores))


# Singleton
_runner: Optional[BenchmarkRunner] = None


def get_benchmark_runner(project_root: str = None) -> BenchmarkRunner:
    """Get singleton benchmark runner."""
    global _runner
    if _runner is None:
        _runner = BenchmarkRunner(project_root)
    return _runner


def run_benchmarks() -> List[BenchmarkResult]:
    """Run all benchmarks."""
    return get_benchmark_runner().run_all_benchmarks()
