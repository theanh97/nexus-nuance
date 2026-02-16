"""
Core Message Module
Defines message types and communication protocol
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum


class MessageType(Enum):
    """Message types for agent communication"""
    TASK = "task"           # Assign a task
    PROGRESS = "progress"   # Update progress
    RESULT = "result"       # Task result
    REVIEW = "review"       # Request/give review
    FEEDBACK = "feedback"   # Feedback on work
    QUESTION = "question"   # Ask for clarification
    VETO = "veto"           # Block a decision
    APPROVE = "approve"     # Approve work
    ESCALATE = "escalate"   # Escalate to Orion
    DEPLOY = "deploy"       # Deploy signal
    STATUS = "status"       # Status check
    SHUTDOWN = "shutdown"   # Shutdown signal


class Priority(Enum):
    """Message priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AgentMessage:
    """Standard message format for agent communication"""
    from_agent: str
    to_agent: str
    type: MessageType
    content: Any
    priority: Priority = Priority.MEDIUM
    timestamp: datetime = field(default_factory=datetime.now)
    requires_response: bool = False
    deadline: Optional[datetime] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "type": self.type.value,
            "content": self.content,
            "priority": self.priority.value,
            "timestamp": self.timestamp.isoformat(),
            "requires_response": self.requires_response,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AgentMessage":
        return cls(
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            type=MessageType(data["type"]),
            content=data["content"],
            priority=Priority(data.get("priority", "medium")),
            timestamp=datetime.fromisoformat(data["timestamp"]) if isinstance(data["timestamp"], str) else data["timestamp"],
            requires_response=data.get("requires_response", False),
            deadline=datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None,
            metadata=data.get("metadata", {})
        )


@dataclass
class TaskResult:
    """Standard result format"""
    success: bool
    agent: str
    output: Any
    score: Optional[float] = None
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    veto: bool = False
    veto_reason: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "agent": self.agent,
            "output": self.output,
            "score": self.score,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "veto": self.veto,
            "veto_reason": self.veto_reason,
            "metadata": self.metadata
        }


@dataclass
class ProjectContext:
    """Shared project context"""
    project_name: str
    project_goal: str
    current_iteration: int = 0
    files: Dict[str, str] = field(default_factory=dict)  # path -> content
    screenshots: List[str] = field(default_factory=list)  # paths
    feedback_history: List[Dict] = field(default_factory=list)
    decisions: List[Dict] = field(default_factory=list)
    issues: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "project_name": self.project_name,
            "project_goal": self.project_goal,
            "current_iteration": self.current_iteration,
            "files": self.files,
            "screenshots": self.screenshots,
            "feedback_history": self.feedback_history,
            "decisions": self.decisions,
            "issues": self.issues
        }
