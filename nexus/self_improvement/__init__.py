"""
NEXUS Self-Improvement Engine
==============================

SICA-inspired self-modification system that enables NEXUS to improve itself.

Core Loop:
    DISCOVER → ANALYZE → GENERATE → APPLY → MEASURE → LEARN

Based on research:
    - SICA (Self-Improving Coding Agent) - 212% improvement on SWE-Bench
    - arXiv: https://openreview.net/pdf?id=rShJCyLsOr

Usage:
    from nexus.self_improvement import (
        SelfImprovementEngine,
        discover_improvements,
        run_self_improvement_cycle,
    )

    engine = SelfImprovementEngine()
    engine.run_cycle()
"""

from .discovery import (
    ImprovementDiscovery,
    discover_improvements,
    get_discovery_engine,
)

from .benchmark import (
    BenchmarkRunner,
    run_benchmarks,
    get_benchmark_runner,
)

from .patch_generator import (
    PatchGenerator,
    generate_patch,
    get_patch_generator,
)

from .safe_apply import (
    SafeApplier,
    apply_patch_safely,
    get_safe_applier,
)

from .archive import (
    ArchiveAnalyzer,
    analyze_archive,
    get_archive_analyzer,
)

from .engine import (
    SelfImprovementEngine,
    run_self_improvement_cycle,
    get_improvement_engine,
)

__all__ = [
    # Discovery
    "ImprovementDiscovery", "discover_improvements", "get_discovery_engine",

    # Benchmark
    "BenchmarkRunner", "run_benchmarks", "get_benchmark_runner",

    # Patch Generation
    "PatchGenerator", "generate_patch", "get_patch_generator",

    # Safe Apply
    "SafeApplier", "apply_patch_safely", "get_safe_applier",

    # Archive Analysis
    "ArchiveAnalyzer", "analyze_archive", "get_archive_analyzer",

    # Main Engine
    "SelfImprovementEngine", "run_self_improvement_cycle", "get_improvement_engine",
]

__version__ = "1.0.0"
