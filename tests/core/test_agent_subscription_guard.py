from types import SimpleNamespace

import pytest

import src.core.agent as agent_module
from src.core.agent import AsyncAgent
from src.core.model_router import ModelConfig, TaskComplexity, TaskType


class _DummyAgent(AsyncAgent):
    async def process(self, message):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_subscription_cli_skips_when_non_tty(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_SKIP_WHEN_NO_TTY", "true")
    monkeypatch.setenv("CODEX_CLI_CMD", "echo")
    monkeypatch.setenv("CODEX_CLI_ARGS", "-n {prompt}")
    monkeypatch.setattr(agent_module.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    agent = _DummyAgent(
        name="Echo",
        role="Test agent",
        model="glm-5",
        api_key="dummy",
    )
    cfg = ModelConfig(
        name="gpt-5",
        api_source="subscription_codex",
        api_key_env="",
        api_base="",
        cost_per_1k_tokens=0.0,
        best_for=[TaskType.TEST_GENERATION],
        max_complexity=TaskComplexity.SIMPLE,
        supports_api=False,
        is_subscription=True,
    )

    result = await agent._call_single_subscription_model(
        cfg=cfg,
        messages=[{"role": "user", "content": "ping"}],
        estimated_tokens=32,
    )

    assert "error" in result
    assert "subscription_cli_skipped_non_tty" in str(result["error"])


def test_error_classification_samples():
    agent = _DummyAgent(
        name="Echo",
        role="Test agent",
        model="glm-5",
        api_key="dummy",
    )
    assert agent._classify_error("429 rate limit") == "transient"
    assert agent._classify_error("invalid api key provided") == "auth"
    assert agent._classify_error("not a tty device") == "subscription_non_tty"
    assert agent._classify_error("yaml parse error in config") == "config"
