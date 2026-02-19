"""
UNIFIED LLM CALLER - Nexus Multi-Model Interface
=================================================
Gọi GLM-5, MiniMax-M2.5, Codex API với fallback tự động.
Tiết kiệm tokens bằng cách chọn model rẻ nhất phù hợp.

PRIORITY ORDER (by cost):
1. GLM-5       ($0.02/1k tokens)  - OpenAI-compatible API
2. MiniMax-M2.5 ($0.008/1k tokens) - Anthropic-compatible API
3. Codex API   ($0.015/1k tokens) - OpenAI-compatible API
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ============================================
# API CONFIGURATIONS
# ============================================

_GLM_API_KEY = os.getenv("GLM_API_KEY", "")
_GLM_API_BASE = os.getenv("GLM_API_BASE") or os.getenv(
    "ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1"
)
_GLM_DEFAULT_MODEL = os.getenv("GLM_MODEL", "glm-5")

_MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
_MINIMAX_API_BASE = os.getenv(
    "MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic"
)
_MINIMAX_DEFAULT_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.5")

_CODEX_API_KEY = os.getenv("CODAXER_API_KEY") or os.getenv("CLAUDE_CODE_API_KEY", "")
_CODEX_API_BASE = os.getenv("CODAXER_BASE_URL") or os.getenv(
    "CLAUDE_CODE_BASE_URL", "https://api.codaxer.com/v1"
)
_CODEX_DEFAULT_MODEL = os.getenv("CODAXER_MODEL", "gpt-5.1-codex-max")

_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))


# ============================================
# CIRCUIT BREAKER
# ============================================

class _CircuitBreaker:
    """Per-model circuit breaker to avoid hammering a failing backend.

    States:
      CLOSED  – normal operation, requests pass through
      OPEN    – backend is down, fail fast for ``recovery_timeout`` seconds
      HALF    – allow one probe request after timeout expires
    """

    CLOSED, OPEN, HALF = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 60):
        self._lock = Lock()
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._state = self.CLOSED
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN:
                if time.monotonic() - self._opened_at >= self.recovery_timeout:
                    self._state = self.HALF
            return self._state

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = time.monotonic()

    def allow_request(self) -> bool:
        return self.state != self.OPEN


_CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("LLM_CB_THRESHOLD", "3"))
_CIRCUIT_BREAKER_TIMEOUT = float(os.getenv("LLM_CB_TIMEOUT", "60"))
_breakers: Dict[str, _CircuitBreaker] = {}


# ============================================
# INDIVIDUAL MODEL CALLERS
# ============================================


def call_glm(
    prompt: str,
    model: str = "",
    max_tokens: int = 2000,
    temperature: float = 0.7,
    system_prompt: str = "",
) -> str:
    """Call GLM via OpenAI-compatible API."""
    model = model or _GLM_DEFAULT_MODEL
    if not _GLM_API_KEY:
        raise ValueError("GLM_API_KEY not set")

    url = f"{_GLM_API_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_GLM_API_KEY}",
        "Content-Type": "application/json",
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError(f"GLM returned empty choices: {data}")

    return choices[0].get("message", {}).get("content", "")


def call_minimax(
    prompt: str,
    model: str = "",
    max_tokens: int = 2000,
    temperature: float = 0.7,
    system_prompt: str = "",
) -> str:
    """Call MiniMax via Anthropic-compatible API."""
    model = model or _MINIMAX_DEFAULT_MODEL
    if not _MINIMAX_API_KEY:
        raise ValueError("MINIMAX_API_KEY not set")

    url = f"{_MINIMAX_API_BASE.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": _MINIMAX_API_KEY,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        payload["system"] = system_prompt

    resp = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    content_blocks = data.get("content", [])
    if not content_blocks:
        raise ValueError(f"MiniMax returned empty content: {data}")

    return "".join(
        block.get("text", "") for block in content_blocks if block.get("type") == "text"
    )


def call_codex_api(
    prompt: str,
    model: str = "",
    max_tokens: int = 2000,
    temperature: float = 0.7,
    system_prompt: str = "",
) -> str:
    """Call Codex/OpenAI-compatible API."""
    model = model or _CODEX_DEFAULT_MODEL
    api_key = _CODEX_API_KEY
    if not api_key:
        raise ValueError("CODAXER_API_KEY / CLAUDE_CODE_API_KEY not set")

    url = f"{_CODEX_API_BASE.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError(f"Codex API returned empty choices: {data}")

    return choices[0].get("message", {}).get("content", "")


# ============================================
# UNIFIED CALLER WITH FALLBACK
# ============================================

# Map task_type to preferred model order for cost optimization
_TASK_MODEL_PRIORITY = {
    "code_generation": ["codex_api", "glm", "minimax"],
    "code_review": ["minimax", "glm", "codex_api"],
    "planning": ["glm", "codex_api", "minimax"],
    "security": ["glm", "codex_api", "minimax"],
    "test": ["minimax", "glm", "codex_api"],
    "monitoring": ["minimax", "glm", "codex_api"],
    "general": ["glm", "minimax", "codex_api"],
}

_MODEL_CALLERS = {
    "glm": call_glm,
    "minimax": call_minimax,
    "codex_api": call_codex_api,
}


def call_llm(
    prompt: str,
    task_type: str = "general",
    max_tokens: int = 2000,
    temperature: float = 0.7,
    system_prompt: str = "",
    preferred_model: Optional[str] = None,
) -> str:
    """
    Call LLM with automatic fallback chain.

    Tries models in priority order based on task_type.
    Falls back to next model if current one fails.

    Args:
        prompt: The prompt to send
        task_type: One of code_generation, code_review, planning, security, test, monitoring, general
        max_tokens: Max tokens to generate
        temperature: Sampling temperature
        system_prompt: Optional system prompt
        preferred_model: Force a specific model (glm, minimax, codex_api)

    Returns:
        Generated text from the first successful model call
    """
    if preferred_model and preferred_model in _MODEL_CALLERS:
        order = [preferred_model] + [m for m in _TASK_MODEL_PRIORITY.get(task_type, ["glm", "minimax", "codex_api"]) if m != preferred_model]
    else:
        order = _TASK_MODEL_PRIORITY.get(task_type, ["glm", "minimax", "codex_api"])

    last_error = None
    for model_key in order:
        caller = _MODEL_CALLERS.get(model_key)
        if not caller:
            continue

        # Circuit breaker check
        cb = _breakers.setdefault(
            model_key,
            _CircuitBreaker(_CIRCUIT_BREAKER_THRESHOLD, _CIRCUIT_BREAKER_TIMEOUT),
        )
        if not cb.allow_request():
            logger.debug(f"[LLM] {model_key} circuit open, skipping")
            continue

        try:
            start = time.monotonic()
            result = caller(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            cb.record_success()
            logger.info(
                f"[LLM] {model_key} responded in {elapsed_ms:.0f}ms "
                f"({len(result)} chars)"
            )
            return result

        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            cb.record_failure()
            last_error = e
            logger.warning(f"[LLM] {model_key} failed: {e}, trying next...")
            continue

    raise RuntimeError(
        f"All LLM backends failed for task_type={task_type}. Last error: {last_error}"
    )


def call_llm_safe(
    prompt: str,
    fallback: str = "",
    **kwargs,
) -> str:
    """Call LLM with graceful degradation - returns *fallback* instead of raising."""
    try:
        return call_llm(prompt, **kwargs)
    except RuntimeError:
        logger.warning("[LLM] All backends unavailable, returning fallback")
        return fallback


def quick_generate(prompt: str, max_tokens: int = 500) -> str:
    """Quick generation with cheapest model for short tasks."""
    return call_llm(
        prompt=prompt,
        task_type="monitoring",
        max_tokens=max_tokens,
        temperature=0.3,
    )


def code_generate(prompt: str, max_tokens: int = 4000) -> str:
    """Generate code with best code model."""
    return call_llm(
        prompt=prompt,
        task_type="code_generation",
        max_tokens=max_tokens,
        temperature=0.2,
        system_prompt="You are an expert programmer. Write clean, production-ready code.",
    )


def plan_reasoning(prompt: str) -> str:
    """Plan next actions and return a JSON-only reasoning payload."""
    return call_llm(
        prompt=prompt,
        task_type="planning",
        max_tokens=1500,
        temperature=0.4,
        system_prompt=(
            "You are a ReAct reasoning agent. Analyze the task and determine the best action. "
            "Return a JSON object with keys: reasoning (str), action_type (str - one of: search, "
            "execute, query, create, modify, analyze, learn, read_file, write_file, edit_file, "
            "run_code, run_shell, navigate, screenshot, git, test), target (str), params (dict). "
            "Only return valid JSON."
        ),
    )


def reflect_on_work(work_description: str) -> str:
    """Evaluate completed work and return a JSON-only review payload."""
    return call_llm(
        prompt=work_description,
        task_type="code_review",
        max_tokens=1000,
        temperature=0.3,
        system_prompt=(
            "Evaluate the quality of work done. Return JSON with: quality_score (float 0-1), "
            "improvements (list of strings), summary (str)."
        ),
    )


# ============================================
# AVAILABILITY CHECK
# ============================================


def get_available_models() -> Dict[str, bool]:
    """Check which models have API keys configured."""
    return {
        "glm": bool(_GLM_API_KEY),
        "minimax": bool(_MINIMAX_API_KEY),
        "codex_api": bool(_CODEX_API_KEY),
    }


_SYSTEM_HEALTH_CACHE_TTL_SECONDS = 60
_system_health_cache_lock = Lock()
_system_health_cache_expires_at = 0.0
_system_health_cache_value: Optional[Dict[str, Any]] = None


def get_system_health_summary() -> Dict[str, Any]:
    """
    Return a cached health snapshot of LLM model availability.

    Returns:
        Dict with keys:
            total_models (int)
            available_models (int)
            degraded (bool)
            last_check (ISO-8601 timestamp, UTC)
    """
    global _system_health_cache_expires_at, _system_health_cache_value

    now = time.monotonic()
    with _system_health_cache_lock:
        if _system_health_cache_value is not None and now < _system_health_cache_expires_at:
            return dict(_system_health_cache_value)

        available = get_available_models()
        total_models = len(available)
        available_models = sum(1 for is_ready in available.values() if is_ready)
        degraded = available_models < total_models
        last_check = datetime.now(timezone.utc).isoformat()

        summary = {
            "total_models": int(total_models),
            "available_models": int(available_models),
            "degraded": bool(degraded),
            "last_check": last_check,
        }

        _system_health_cache_value = summary
        _system_health_cache_expires_at = now + _SYSTEM_HEALTH_CACHE_TTL_SECONDS
        return dict(summary)


if __name__ == "__main__":
    print("=" * 60)
    print("NEXUS LLM CALLER - Multi-Model Interface")
    print("=" * 60)
    print()

    available = get_available_models()
    for name, ready in available.items():
        status = "READY" if ready else "NO KEY"
        print(f"  {name}: {status}")

    print()
    print("Task-type routing:")
    for task, order in _TASK_MODEL_PRIORITY.items():
        print(f"  {task}: {' -> '.join(order)}")
