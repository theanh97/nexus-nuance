"""
GLM 5.0 Orchestrator Agent
Central controller for the autonomous development loop
"""

import os
import json
import yaml
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import requests
try:
    from src.core.prompt_system import get_prompt_system
except ImportError:
    from core.prompt_system import get_prompt_system


@dataclass
class Task:
    """Represents a development task"""
    id: str
    description: str
    status: str = "pending"
    priority: int = 1
    dependencies: List[str] = field(default_factory=list)
    result: Optional[str] = None
    feedback: List[Dict] = field(default_factory=list)


@dataclass
class Feedback:
    """UI/UX feedback from vision models"""
    source: str
    score: float
    issues: List[str]
    suggestions: List[str]
    screenshot_path: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class GLMOrchestrator:
    """
    GLM 5.0 as the central orchestrator
    Coordinates all agents and manages the development loop
    """

    def __init__(self, config_path: str = "../config/settings.yaml"):
        self.config = self._load_config(config_path)
        self.api_key = os.getenv(self.config['orchestrator']['api_key_env'])
        self.api_base = self.config['orchestrator']['api_base']
        self.model = self.config['orchestrator']['model']
        self.prompt_system = get_prompt_system()
        self.prompt_system.ensure_core_directives()

        self.tasks: List[Task] = []
        self.feedback_history: List[Feedback] = []
        self.iteration = 0
        self.context: Dict[str, Any] = {}

    def _load_config(self, path: str) -> Dict:
        """Load configuration from YAML"""
        with open(path, 'r') as f:
            return yaml.safe_load(f)

    def _call_glm(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """Call GLM API (OpenAI-compatible)"""
        messages = self.prompt_system.inject_messages(
            messages=messages,
            agent_name="GLMOrchestrator",
            role="Central Orchestrator",
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096
        }

        if tools:
            payload["tools"] = tools

        response = requests.post(
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=payload
        )

        return response.json()

    def analyze_task(self, task_description: str) -> Task:
        """Analyze and break down a task into subtasks"""
        messages = [
            {
                "role": "system",
                "content": """You are GLM 5.0, an orchestrator for autonomous app development.
                Your job is to:
                1. Analyze user requirements
                2. Break down into actionable subtasks
                3. Coordinate with vision agents for UI/UX feedback
                4. Iterate until the app is perfect

                Respond in JSON format with:
                {
                    "analysis": "brief analysis",
                    "subtasks": [{"id": "...", "description": "...", "priority": 1-5}],
                    "approach": "recommended approach"
                }
                """
            },
            {
                "role": "user",
                "content": f"Analyze this app development task: {task_description}"
            }
        ]

        result = self._call_glm(messages)
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '{}')

        try:
            parsed = json.loads(content)
            task = Task(
                id=f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                description=task_description,
                status="analyzed"
            )
            self.tasks.append(task)
            return task
        except json.JSONDecodeError:
            return Task(id="error", description="Failed to parse", status="error")

    def generate_code(self, task: Task, context: Dict = None) -> str:
        """Generate code for a task"""
        messages = [
            {
                "role": "system",
                "content": """You are an expert full-stack developer.
                Generate clean, production-ready code.
                Include all necessary files for a complete app.
                Use modern best practices.

                Structure your response as:
                {
                    "files": [{"path": "...", "content": "..."}],
                    "instructions": "how to run",
                    "dependencies": ["..."]
                }
                """
            },
            {
                "role": "user",
                "content": f"""Generate code for: {task.description}

                Context: {json.dumps(context or self.context, indent=2)}

                Previous feedback to address:
                {json.dumps(task.feedback, indent=2) if task.feedback else 'None - first iteration'}
                """
            }
        ]

        result = self._call_glm(messages)
        return result.get('choices', [{}])[0].get('message', {}).get('content', '')

    def incorporate_feedback(self, feedback: Feedback) -> None:
        """Incorporate UI/UX feedback into the development plan"""
        self.feedback_history.append(feedback)

        messages = [
            {
                "role": "system",
                "content": "You are improving an app based on UI/UX feedback."
            },
            {
                "role": "user",
                "content": f"""
                Feedback from {feedback.source}:
                Score: {feedback.score}/10
                Issues: {feedback.issues}
                Suggestions: {feedback.suggestions}

                Create an improvement plan in JSON:
                {{
                    "priority_issues": [...],
                    "code_changes": [...],
                    "estimated_improvement": "x%"
                }}
                """
            }
        ]

        result = self._call_glm(messages)
        # Update current task with new feedback
        if self.tasks:
            self.tasks[-1].feedback.append({
                "source": feedback.source,
                "score": feedback.score,
                "issues": feedback.issues,
                "suggestions": feedback.suggestions
            })

    def should_continue(self) -> bool:
        """Decide if the loop should continue"""
        if self.iteration >= self.config['loop']['max_iterations']:
            return False

        if self.feedback_history:
            latest_score = self.feedback_history[-1].score
            if latest_score >= self.config['loop']['improvement_threshold'] * 10:
                return False

        return True

    def get_status(self) -> Dict:
        """Get current orchestrator status"""
        return {
            "iteration": self.iteration,
            "tasks_count": len(self.tasks),
            "feedback_count": len(self.feedback_history),
            "latest_score": self.feedback_history[-1].score if self.feedback_history else None,
            "status": "running" if self.should_continue() else "completed"
        }


if __name__ == "__main__":
    # Test the orchestrator
    orchestrator = GLMOrchestrator()
    print("GLM 5.0 Orchestrator initialized")
    print(f"Config: {orchestrator.config['orchestrator']}")
