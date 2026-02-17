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
    assert agent._classify_error("model does not exist") == "config"


def test_full_auto_enables_subscription_recovery_defaults(monkeypatch):
    monkeypatch.setenv("AUTONOMY_PROFILE", "full_auto")
    monkeypatch.delenv("ENABLE_SUBSCRIPTION_FALLBACK", raising=False)
    monkeypatch.delenv("ENABLE_SUBSCRIPTION_PRIMARY_ROUTING", raising=False)
    monkeypatch.delenv("SUBSCRIPTION_SKIP_WHEN_NO_TTY", raising=False)

    agent = _DummyAgent(
        name="Echo",
        role="Test agent",
        model="glm-5",
        api_key="dummy",
    )
    assert agent.subscription_fallback_enabled is True
    assert agent.subscription_primary_routing_enabled is True
    assert agent.subscription_skip_when_no_tty is False


@pytest.mark.asyncio
async def test_call_api_continues_chain_on_non_retryable_api_error(monkeypatch):
    monkeypatch.setenv("ROUTER_CONTINUE_ON_NON_RETRYABLE_API_ERROR", "true")
    monkeypatch.setenv("ENABLE_SMART_ROUTING", "true")

    agent = _DummyAgent(
        name="Echo",
        role="Test agent",
        model="glm-5",
        api_key="dummy",
    )

    cfg_primary = ModelConfig(
        name="glm-4-flash",
        api_source="glm",
        api_key_env="GLM_API_KEY",
        api_base="https://api.z.ai/api/openai/v1",
        cost_per_1k_tokens=0.001,
        best_for=[TaskType.TEST_GENERATION],
        max_complexity=TaskComplexity.MEDIUM,
    )
    cfg_fallback = ModelConfig(
        name="glm-4.7",
        api_source="glm",
        api_key_env="GLM_API_KEY",
        api_base="https://api.z.ai/api/openai/v1",
        cost_per_1k_tokens=0.001,
        best_for=[TaskType.TEST_GENERATION],
        max_complexity=TaskComplexity.COMPLEX,
    )

    monkeypatch.setattr(
        agent.router,
        "get_fallback_chain",
        lambda **_: [cfg_primary, cfg_fallback],
    )
    monkeypatch.setattr(agent, "_emit_routing_telemetry", lambda **_: None)

    attempts = {"count": 0}

    async def fake_call(messages, tools=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"error": "model does not exist"}
        return {"content": "ok", "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    monkeypatch.setattr(agent, "_call_with_current_model", fake_call)

    response = await agent.call_api(
        messages=[{"role": "user", "content": "ping"}],
        task_type=TaskType.TEST_GENERATION,
        complexity=TaskComplexity.SIMPLE,
        importance="low",
    )
    assert "error" not in response
    assert attempts["count"] == 2
