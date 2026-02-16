"""Typed contracts for the self-learning v2 pipeline."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class LearningEvent:
    id: str
    ts: str
    source: str
    event_type: str
    content: str
    context: Dict[str, Any] = field(default_factory=dict)
    novelty_score: float = 0.0
    value_score: float = 0.0
    risk_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImprovementProposalV2:
    id: str
    created_at: str
    origin_event_ids: List[str]
    title: str
    hypothesis: str
    plan_steps: List[str]
    expected_impact: str
    risk_level: str
    status: str
    confidence: float
    priority: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentRun:
    id: str
    proposal_id: str
    mode: str
    started_at: str
    finished_at: Optional[str]
    actions: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    execution_status: str = "running"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OutcomeEvidence:
    id: str
    experiment_id: str
    metrics_before: Dict[str, Any]
    metrics_after: Dict[str, Any]
    delta: Dict[str, Any]
    verdict: str
    confidence: float
    notes: str
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
