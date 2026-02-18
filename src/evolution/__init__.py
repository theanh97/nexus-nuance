"""
Self-Evolving Background Engine
"""

from .evolution_engine import (
    EvolutionEngine,
    LearningEvent,
    Pattern,
    SkillLevel,
    get_engine,
    start_evolution,
    capture_input,
    capture_output,
    capture_error,
    capture_feedback,
    capture_task,
    get_evolution_stats
)

__all__ = [
    "EvolutionEngine",
    "LearningEvent",
    "Pattern",
    "SkillLevel",
    "get_engine",
    "start_evolution",
    "capture_input",
    "capture_output",
    "capture_error",
    "capture_feedback",
    "capture_task",
    "get_evolution_stats"
]
