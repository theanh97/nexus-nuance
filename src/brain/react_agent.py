"""
REACT AGENT SYSTEM WITH SELF-REFLECTION
========================================
ReAct Pattern + Self-Reflection Loop - Cutting-edge Agent Architecture

PATTERN:
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  THOUGHT → ACTION → OBSERVATION → THOUGHT → ACTION → ...       │
│                                                                 │
│  + SELF-REFLECTION: Review output before responding             │
│  + PLAN-AND-EXECUTE: Plan first for complex tasks               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

SOURCES:
- ReAct: Yao et al., "ReAct: Synergizing Reasoning and Acting"
- Reflexion: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning"
"""

import json
import os
import sys
import time
import threading
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
import random

from core.nexus_logger import get_logger

logger = get_logger(__name__)

try:
    from core.llm_caller import call_llm
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"


class AgentState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Thought:
    """A thought in the ReAct cycle"""
    content: str
    timestamp: str
    reasoning: str


@dataclass
class Action:
    """An action taken by the agent"""
    type: str  # search, execute, query, create, modify
    target: str
    params: Dict
    timestamp: str
    result: Optional[str] = None


@dataclass
class Observation:
    """An observation from an action"""
    content: str
    success: bool
    timestamp: str
    insights: List[str] = field(default_factory=list)


@dataclass
class Reflection:
    """A self-reflection on the agent's work"""
    content: str
    quality_score: float  # 0-1
    improvements: List[str]
    timestamp: str


class ReActAgent:
    """
    ReAct Agent with Self-Reflection
    Implements: Thought → Action → Observation → Reflection
    """

    def __init__(self, brain=None):
        self.brain = brain
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.history_file = self.data_dir / "react_history.json"
        self.state = AgentState.IDLE
        self.current_task: Optional[str] = None

        # ReAct cycle components
        self.thoughts: List[Thought] = []
        self.actions: List[Action] = []
        self.observations: List[Observation] = []
        self.reflections: List[Reflection] = []

        # Available tools
        self.tools: Dict[str, Callable] = {
            "search": self._tool_search,
            "execute": self._tool_execute,
            "query": self._tool_query,
            "create": self._tool_create,
            "modify": self._tool_modify,
            "analyze": self._tool_analyze,
            "learn": self._tool_learn,
        }

        self._load()

    def _load(self):
        """Load history"""
        if self.history_file.exists():
            with open(self.history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.thoughts = [Thought(**t) for t in data.get("thoughts", [])]
                self.actions = [Action(**a) for a in data.get("actions", [])]
                self.observations = [Observation(**o) for o in data.get("observations", [])]
                self.reflections = [Reflection(**r) for r in data.get("reflections", [])]

    def _save(self):
        """Save history"""
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump({
                "thoughts": [vars(t) for t in self.thoughts[-100:]],
                "actions": [vars(a) for a in self.actions[-100:]],
                "observations": [vars(o) for o in self.observations[-100:]],
                "reflections": [vars(r) for r in self.reflections[-50:]],
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    # ==================== TOOLS ====================

    def _tool_search(self, query: str, **kwargs) -> str:
        """Search for information"""
        if self.brain:
            try:
                results = self.brain.search(query)
                return f"Found {len(results)} results for: {query}"
            except Exception as e:
                logger.warning(f"_tool_search failed for query={query!r}: {e}")
                pass
        return f"Searched for: {query}"

    def _tool_execute(self, command: str, **kwargs) -> str:
        """Execute a command"""
        return f"Executed: {command}"

    def _tool_query(self, target: str, **kwargs) -> str:
        """Query knowledge base"""
        return f"Queried: {target}"

    def _tool_create(self, what: str, **kwargs) -> str:
        """Create something"""
        return f"Created: {what}"

    def _tool_modify(self, target: str, changes: str, **kwargs) -> str:
        """Modify something"""
        return f"Modified {target}: {changes}"

    def _tool_analyze(self, target: str, **kwargs) -> str:
        """Analyze something"""
        return f"Analyzed: {target}"

    def _tool_learn(self, content: str, source: str = "self", **kwargs) -> str:
        """Learn new knowledge"""
        if self.brain:
            try:
                self.brain.learn_knowledge(
                    source=source,
                    type="self_learned",
                    title=f"Self-learned: {content[:50]}",
                    content=content
                )
            except Exception as e:
                logger.warning(f"_tool_learn failed for source={source!r}: {e}")
                pass
        return f"Learned: {content[:100]}"

    # ==================== REACT CYCLE ====================

    def think(self, context: str) -> Thought:
        """Generate a thought"""
        self.state = AgentState.THINKING

        # Analyze context and determine next action
        reasoning = self._generate_reasoning(context)

        thought = Thought(
            content=f"Analyzing: {context[:200]}",
            timestamp=datetime.now().isoformat(),
            reasoning=reasoning
        )

        self.thoughts.append(thought)
        self._save()
        return thought

    def _generate_reasoning(self, context: str) -> str:
        """Generate reasoning for the thought"""
        if _LLM_AVAILABLE:
            try:
                llm_reasoning = call_llm(
                    prompt=f"Task: {context}\n\nAnalyze this task. What kind of action is needed? Respond in 1-2 sentences.",
                    task_type="planning",
                    max_tokens=200,
                    temperature=0.3,
                )
                if isinstance(llm_reasoning, str) and llm_reasoning.strip():
                    return llm_reasoning.strip()
            except Exception as e:
                logger.warning(f"_generate_reasoning LLM call failed: {e}")
                pass

        context_lower = context.lower()

        # Determine what kind of action is needed
        if "search" in context_lower or "find" in context_lower or "look for" in context_lower:
            return "Need to search for information"
        elif "create" in context_lower or "make" in context_lower or "build" in context_lower:
            return "Need to create something new"
        elif "fix" in context_lower or "solve" in context_lower or "debug" in context_lower:
            return "Need to analyze and fix an issue"
        elif "learn" in context_lower or "understand" in context_lower:
            return "Need to learn and acquire knowledge"
        else:
            return "Analyzing task to determine best approach"

    def act(self, action_type: str, target: str, params: Dict = None) -> Action:
        """Take an action"""
        self.state = AgentState.ACTING

        action = Action(
            type=action_type,
            target=target,
            params=params or {},
            timestamp=datetime.now().isoformat()
        )

        # Execute the action
        if action_type in self.tools:
            action.result = self.tools[action_type](target, **(params or {}))
        else:
            action.result = f"Unknown action type: {action_type}"

        self.actions.append(action)
        self._save()
        return action

    def observe(self, action_result: str) -> Observation:
        """Observe the result of an action"""
        self.state = AgentState.OBSERVING

        success = "error" not in action_result.lower() and "failed" not in action_result.lower()

        # Extract insights
        insights = self._extract_insights(action_result)

        observation = Observation(
            content=action_result,
            success=success,
            timestamp=datetime.now().isoformat(),
            insights=insights
        )

        self.observations.append(observation)
        self._save()
        return observation

    def _extract_insights(self, result: str) -> List[str]:
        """Extract insights from observation"""
        insights = []

        if "found" in result.lower():
            insights.append("Information was found successfully")
        if "created" in result.lower():
            insights.append("Something was created")
        if "error" in result.lower():
            insights.append("An error occurred - needs attention")

        return insights

    def reflect(self, work_done: str) -> Reflection:
        """Self-reflection on the work done"""
        self.state = AgentState.REFLECTING

        # Evaluate quality
        quality_score = self._evaluate_quality(work_done)

        # Suggest improvements
        improvements = self._suggest_improvements(work_done, quality_score)

        reflection = Reflection(
            content=f"Reflection on: {work_done[:200]}",
            quality_score=quality_score,
            improvements=improvements,
            timestamp=datetime.now().isoformat()
        )

        self.reflections.append(reflection)
        self._save()
        return reflection

    def _evaluate_quality(self, work: str) -> float:
        """Evaluate the quality of work done"""
        if _LLM_AVAILABLE:
            try:
                llm_score_text = call_llm(
                    prompt=(
                        "Evaluate the quality of the work described below. "
                        "Return only a single number between 0 and 1 (inclusive).\n\n"
                        f"Work:\n{work}"
                    ),
                    task_type="code_review",
                    max_tokens=50,
                    temperature=0.1,
                )

                score_text = (llm_score_text or "").strip()
                parsed_score: Optional[float] = None

                try:
                    parsed_score = float(score_text)
                except (TypeError, ValueError):
                    for raw_token in score_text.replace("\n", " ").replace("\t", " ").split():
                        token = raw_token.strip().strip(" ,;:()[]{}<>\"'`")
                        if not token:
                            continue
                        if "/" in token:
                            token = token.split("/", 1)[0].strip()
                        if token.endswith("%"):
                            try:
                                parsed_score = float(token[:-1]) / 100.0
                                break
                            except ValueError:
                                continue
                        try:
                            parsed_score = float(token)
                            break
                        except ValueError:
                            continue

                if parsed_score is not None:
                    return min(1.0, max(0.0, parsed_score))
            except Exception as e:
                logger.warning(f"_evaluate_quality LLM call failed: {e}")
                pass

        score = 0.5  # Base score

        # Positive indicators
        if "success" in work.lower() or "completed" in work.lower():
            score += 0.2
        if len(self.observations) > 0 and self.observations[-1].success:
            score += 0.2
        if len(self.thoughts) > 1:
            score += 0.1  # Had multiple thoughts

        return min(1.0, score)

    def _suggest_improvements(self, work: str, quality: float) -> List[str]:
        """Suggest improvements based on reflection"""
        if _LLM_AVAILABLE:
            try:
                llm_improvements_text = call_llm(
                    prompt=(
                        "Suggest concrete improvements for the work described below, given its quality score. "
                        "Return ONLY a JSON array of strings.\n\n"
                        f"Work:\n{work}\n\n"
                        f"Quality score (0-1): {quality}"
                    ),
                    task_type="code_review",
                    max_tokens=300,
                    temperature=0.3,
                )

                improvements_text = (llm_improvements_text or "").strip()
                if improvements_text.startswith("```"):
                    fence_lines = improvements_text.splitlines()
                    if (
                        len(fence_lines) >= 3
                        and fence_lines[0].startswith("```")
                        and fence_lines[-1].startswith("```")
                    ):
                        improvements_text = "\n".join(fence_lines[1:-1]).strip()
                parsed_improvements: List[str] = []

                try:
                    parsed = json.loads(improvements_text)
                    if isinstance(parsed, list):
                        parsed_improvements = [
                            str(item).strip() for item in parsed if str(item).strip()
                        ]
                except json.JSONDecodeError:
                    for line in improvements_text.splitlines():
                        cleaned = line.strip()
                        if not cleaned:
                            continue
                        if cleaned.startswith("```"):
                            continue
                        cleaned = cleaned.lstrip("-*•").strip()
                        if len(cleaned) >= 2 and cleaned[0].isdigit() and cleaned[1] in {".", ")"}:
                            cleaned = cleaned[2:].strip()
                        if cleaned:
                            parsed_improvements.append(cleaned)

                if parsed_improvements:
                    return parsed_improvements
            except Exception as e:
                logger.warning(f"_suggest_improvements LLM call failed: {e}")
                pass

        improvements = []

        if quality < 0.7:
            improvements.append("Consider more thorough analysis before acting")
        if len(self.actions) < 2:
            improvements.append("Could explore alternative approaches")
        if not any(o.success for o in self.observations[-3:]):
            improvements.append("Recent observations show issues - review approach")

        return improvements

    # ==================== FULL CYCLE ====================

    def execute_task(self, task: str, max_cycles: int = 10) -> Dict:
        """
        Execute a task using ReAct pattern with self-reflection

        Flow:
        1. Plan approach
        2. Think → Act → Observe (repeat as needed)
        3. Reflect on result
        4. Return or retry
        """
        self.current_task = task
        result = {
            "task": task,
            "started_at": datetime.now().isoformat(),
            "cycles": 0,
            "thoughts": [],
            "actions": [],
            "observations": [],
            "reflections": [],
            "final_result": None,
            "success": False
        }

        try:
            # Phase 1: Initial thinking
            thought = self.think(task)
            result["thoughts"].append(vars(thought))

            # Phase 2: ReAct cycles
            for i in range(max_cycles):
                result["cycles"] = i + 1

                # Decide action based on thought
                action_type, target, params = self._decide_action(thought)

                # Act
                action = self.act(action_type, target, params)
                result["actions"].append(vars(action))

                # Observe
                observation = self.observe(action.result or "")
                result["observations"].append(vars(observation))

                # Check if complete
                if self._is_task_complete(task, observation):
                    break

                # Think again
                thought = self.think(f"Based on observation: {observation.content}")
                result["thoughts"].append(vars(thought))

            # Phase 3: Self-reflection
            reflection = self.reflect(f"Completed task: {task}")
            result["reflections"].append(vars(reflection))

            # Determine success
            result["success"] = reflection.quality_score >= 0.7
            result["final_result"] = self._synthesize_result(result)

            self.state = AgentState.COMPLETED if result["success"] else AgentState.FAILED

        except Exception as e:
            result["error"] = str(e)
            result["success"] = False
            self.state = AgentState.FAILED

        result["completed_at"] = datetime.now().isoformat()
        self.current_task = None
        self.state = AgentState.IDLE

        return result

    def _decide_action(self, thought: Thought) -> Tuple[str, str, Dict]:
        """Decide what action to take based on thought"""
        reasoning = thought.reasoning.lower()

        if "search" in reasoning or "find" in reasoning:
            return "search", self.current_task or "", {}
        elif "create" in reasoning:
            return "create", "component", {"name": "new_component"}
        elif "fix" in reasoning or "solve" in reasoning:
            return "analyze", self.current_task or "", {}
        elif "learn" in reasoning:
            return "learn", self.current_task or "", {}
        else:
            return "query", self.current_task or "", {}

    def _is_task_complete(self, task: str, observation: Observation) -> bool:
        """Check if task is complete"""
        if observation.success:
            # Check if result is meaningful
            if len(observation.content) > 50:
                return True
        return False

    def _synthesize_result(self, result: Dict) -> str:
        """Synthesize final result from all observations"""
        if result["observations"]:
            last_obs = result["observations"][-1]
            return last_obs["content"]
        return "Task processing completed"

    # ==================== STATS ====================

    def get_stats(self) -> Dict:
        """Get agent statistics"""
        successful = sum(1 for o in self.observations if o.success)
        total = len(self.observations)

        return {
            "state": self.state.value,
            "total_thoughts": len(self.thoughts),
            "total_actions": len(self.actions),
            "total_observations": len(self.observations),
            "total_reflections": len(self.reflections),
            "success_rate": successful / total if total > 0 else 0,
            "avg_quality": sum(r.quality_score for r in self.reflections) / max(len(self.reflections), 1)
        }


class MultiAgentOrchestrator:
    """
    Multi-Agent Orchestration System
    Coordinates multiple specialized agents
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.agents: Dict[str, ReActAgent] = {}
        self.task_history: List[Dict] = []

    def create_agent(self, name: str, specialization: str, brain=None) -> ReActAgent:
        """Create a specialized agent"""
        agent = ReActAgent(brain=brain)
        self.agents[name] = agent
        return agent

    def delegate_task(self, task: str, agent_name: str = None) -> Dict:
        """Delegate task to appropriate agent"""
        if agent_name and agent_name in self.agents:
            agent = self.agents[agent_name]
        else:
            # Auto-select based on task type
            agent = self._select_agent(task)

        result = agent.execute_task(task)
        self.task_history.append(result)
        return result

    def _select_agent(self, task: str) -> ReActAgent:
        """Select best agent for task"""
        if not self.agents:
            return ReActAgent()
        return list(self.agents.values())[0]

    def parallel_execute(self, tasks: List[str]) -> List[Dict]:
        """Execute multiple tasks in parallel"""
        results = []
        for task in tasks:
            result = self.delegate_task(task)
            results.append(result)
        return results


# ==================== CONVENIENCE ====================

_agent: Optional[ReActAgent] = None
_orchestrator: Optional[MultiAgentOrchestrator] = None


def get_agent() -> ReActAgent:
    """Get singleton agent"""
    global _agent
    if _agent is None:
        _agent = ReActAgent()
    return _agent


def get_orchestrator() -> MultiAgentOrchestrator:
    """Get singleton orchestrator"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MultiAgentOrchestrator()
    return _orchestrator


def execute_with_react(task: str) -> Dict:
    """Execute task using ReAct pattern"""
    return get_agent().execute_task(task)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("ReAct AGENT SYSTEM WITH SELF-REFLECTION")
    print("=" * 70)

    agent = get_agent()

    print("\nExecuting task with ReAct pattern...")
    print("-" * 70)

    result = agent.execute_task("Research and learn about the latest AI agent architectures")

    print(f"\nTask: {result['task']}")
    print(f"Cycles: {result['cycles']}")
    print(f"Success: {result['success']}")
    print(f"\nFinal Result: {result['final_result']}")

    if result['reflections']:
        print(f"\nReflection (quality: {result['reflections'][0]['quality_score']:.2f}):")
        for imp in result['reflections'][0]['improvements']:
            print(f"  - {imp}")

    print(f"\nAgent Stats: {agent.get_stats()}")
