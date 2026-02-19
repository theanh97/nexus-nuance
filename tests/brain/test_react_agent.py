"""Tests for ReAct Agent system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.brain.react_agent import (
    ReActAgent,
    AgentState,
    Thought,
    Action,
    Observation,
    Reflection,
)


@pytest.fixture
def agent(tmp_path):
    """Create an agent with isolated data dir (LLM disabled for deterministic tests)."""
    import src.brain.react_agent as _mod
    orig = _mod._LLM_AVAILABLE
    _mod._LLM_AVAILABLE = False
    with patch("src.brain.react_agent.DATA_DIR", tmp_path):
        a = ReActAgent(brain=None)
        a.data_dir = tmp_path
        a.history_file = tmp_path / "react_history.json"
    yield a
    _mod._LLM_AVAILABLE = orig


class TestAgentInit:
    def test_initial_state(self, agent):
        assert agent.state == AgentState.IDLE
        assert agent.current_task is None
        assert len(agent.thoughts) == 0
        assert len(agent.actions) == 0

    def test_tools_registered(self, agent):
        expected = {"search", "execute", "query", "create", "modify", "analyze", "learn"}
        assert set(agent.tools.keys()) == expected


class TestThink:
    def test_think_creates_thought(self, agent):
        thought = agent.think("search for quantum papers")
        assert isinstance(thought, Thought)
        assert "search" in thought.content.lower() or "quantum" in thought.content.lower()
        assert thought.timestamp
        assert agent.state == AgentState.THINKING

    def test_think_appends_to_history(self, agent):
        agent.think("task 1")
        agent.think("task 2")
        assert len(agent.thoughts) == 2

    def test_reasoning_search_keyword(self, agent):
        thought = agent.think("find relevant papers")
        assert "search" in thought.reasoning.lower()

    def test_reasoning_create_keyword(self, agent):
        thought = agent.think("create a new module")
        assert "create" in thought.reasoning.lower()

    def test_reasoning_fix_keyword(self, agent):
        thought = agent.think("fix the broken test")
        assert "fix" in thought.reasoning.lower() or "analyze" in thought.reasoning.lower()

    def test_reasoning_learn_keyword(self, agent):
        thought = agent.think("learn about neural networks")
        assert "learn" in thought.reasoning.lower()

    def test_reasoning_generic(self, agent):
        thought = agent.think("do something")
        assert "analyz" in thought.reasoning.lower()


class TestAct:
    def test_act_known_tool(self, agent):
        action = agent.act("search", "quantum computing")
        assert isinstance(action, Action)
        assert action.type == "search"
        assert action.target == "quantum computing"
        assert action.result is not None
        assert agent.state == AgentState.ACTING

    def test_act_unknown_tool(self, agent):
        action = agent.act("unknown_tool", "target")
        assert "Unknown action type" in action.result

    def test_act_with_params(self, agent):
        action = agent.act("modify", "config.json", {"changes": "update timeout"})
        assert action.params == {"changes": "update timeout"}

    def test_each_tool_returns_string(self, agent):
        for tool_name in agent.tools:
            if tool_name == "modify":
                action = agent.act(tool_name, "target", {"changes": "test"})
            else:
                action = agent.act(tool_name, "target")
            assert isinstance(action.result, str)


class TestObserve:
    def test_observe_success(self, agent):
        obs = agent.observe("Found 5 results")
        assert isinstance(obs, Observation)
        assert obs.success is True
        assert agent.state == AgentState.OBSERVING

    def test_observe_failure(self, agent):
        obs = agent.observe("Error: connection failed")
        assert obs.success is False

    def test_observe_extracts_insights(self, agent):
        obs = agent.observe("Found matching documents")
        assert any("found" in i.lower() for i in obs.insights)

    def test_observe_error_insight(self, agent):
        obs = agent.observe("An error occurred during processing")
        assert any("error" in i.lower() for i in obs.insights)


class TestReflect:
    def test_reflect_returns_reflection(self, agent):
        ref = agent.reflect("Completed the search task successfully")
        assert isinstance(ref, Reflection)
        assert 0 <= ref.quality_score <= 1
        assert isinstance(ref.improvements, list)
        assert agent.state == AgentState.REFLECTING

    def test_reflect_success_boosts_score(self, agent):
        ref1 = agent.reflect("random work was done")
        ref2 = agent.reflect("completed successfully")
        assert ref2.quality_score >= ref1.quality_score


class TestPersistence:
    def test_save_and_load(self, agent):
        agent.think("test thought")
        agent.act("search", "test query")
        agent.observe("Found results")
        agent.reflect("Good work")
        agent._save()

        # Create new agent loading from same file
        with patch("src.brain.react_agent.DATA_DIR", agent.data_dir):
            agent2 = ReActAgent(brain=None)
            agent2.data_dir = agent.data_dir
            agent2.history_file = agent.history_file
            agent2._load()
            assert len(agent2.thoughts) == 1
            assert len(agent2.actions) == 1
            assert len(agent2.observations) == 1
            assert len(agent2.reflections) == 1

    def test_load_empty_file(self, agent):
        agent.history_file.write_text("{}", encoding="utf-8")
        agent._load()
        assert len(agent.thoughts) == 0

    def test_load_nonexistent_file(self, agent):
        agent.history_file = agent.data_dir / "nonexistent.json"
        agent._load()  # Should not crash
        assert len(agent.thoughts) == 0


class TestDataclasses:
    def test_thought_fields(self):
        t = Thought(content="test", timestamp="2026-01-01", reasoning="because")
        assert t.content == "test"
        assert t.reasoning == "because"

    def test_action_fields(self):
        a = Action(type="search", target="query", params={}, timestamp="2026-01-01")
        assert a.result is None

    def test_observation_fields(self):
        o = Observation(content="result", success=True, timestamp="2026-01-01")
        assert o.insights == []

    def test_agent_state_values(self):
        assert AgentState.IDLE.value == "idle"
        assert AgentState.COMPLETED.value == "completed"
        assert AgentState.FAILED.value == "failed"
