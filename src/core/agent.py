"""
Async Agent Base Class
Base class for all autonomous agents
"""

import os
import asyncio
import json
import base64
import shlex
import shutil
import sys
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
import requests

from src.core.message import AgentMessage, MessageType, Priority, TaskResult
from src.core.model_router import ModelRouter, TaskType, TaskComplexity, MODELS, ModelConfig
from src.core.routing_telemetry import record_routing_event
from src.core.prompt_system import get_prompt_system


class AsyncAgent(ABC):
    """
    Base class for all async agents
    Each agent is an expert in their domain
    """

    def __init__(
        self,
        name: str,
        role: str,
        model: str,
        api_key: str,
        api_base: str = None
    ):
        self.name = name
        self.role = role
        self.model = model
        self.api_key = api_key
        self.api_base = api_base or os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        self.router = ModelRouter()
        self.smart_routing_enabled = os.getenv("ENABLE_SMART_ROUTING", "true").lower() == "true"
        self.subscription_fallback_enabled = os.getenv("ENABLE_SUBSCRIPTION_FALLBACK", "false").lower() == "true"
        self.subscription_critical_only = os.getenv("SUBSCRIPTION_FALLBACK_CRITICAL_ONLY", "false").lower() == "true"
        self.subscription_first_for_critical = os.getenv("SUBSCRIPTION_FIRST_FOR_CRITICAL", "false").lower() == "true"
        self.subscription_primary_routing_enabled = os.getenv("ENABLE_SUBSCRIPTION_PRIMARY_ROUTING", "false").lower() == "true"
        self.subscription_primary_cost_only = os.getenv("SUBSCRIPTION_PRIMARY_COST_ONLY", "true").lower() == "true"
        self.auto_subscription_cost_routing = os.getenv("ENABLE_AUTO_SUBSCRIPTION_COST_ROUTING", "true").lower() == "true"
        self.subscription_failure_threshold = max(1, int(os.getenv("SUBSCRIPTION_FAILURE_THRESHOLD", "2")))
        self.subscription_failure_cooldown_sec = max(30, int(os.getenv("SUBSCRIPTION_FAILURE_COOLDOWN_SEC", "180")))
        self.subscription_skip_when_no_tty = os.getenv("SUBSCRIPTION_SKIP_WHEN_NO_TTY", "true").lower() == "true"
        self.keep_current_model_first = os.getenv("ROUTER_KEEP_CURRENT_MODEL_FIRST", "false").lower() == "true"
        self.prompt_system = get_prompt_system()
        self.prompt_system.ensure_core_directives()

        # State
        self.running = False
        self.current_task: Optional[AgentMessage] = None
        self.context: Dict[str, Any] = {}
        self._subscription_failure_counts: Dict[str, int] = {}
        self._subscription_cooldown_until: Dict[str, datetime] = {}

        # Logging
        self.log_dir = Path("logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """Start the agent"""
        self.running = True
        self._log(f"🚀 {self.name} ({self.role}) started with model {self.model}")

    async def stop(self):
        """Stop the agent"""
        self.running = False
        self._log(f"🛑 {self.name} stopped")

    @abstractmethod
    async def process(self, message: AgentMessage) -> TaskResult:
        """
        Process incoming message
        Must be implemented by each agent
        """
        pass

    async def call_api(
        self,
        messages: List[Dict],
        tools: List[Dict] = None,
        task_type: Optional[TaskType] = None,
        complexity: Optional[TaskComplexity] = None,
        importance: str = "normal",
        prefer_speed: bool = False,
        prefer_cost: bool = False
    ) -> Dict:
        """
        Call model API with adaptive fallback chain.
        Falls back on retryable errors (timeout, rate limit, quota, transient API errors).
        """
        messages = self.prompt_system.inject_messages(
            messages=messages,
            agent_name=self.name,
            role=self.role,
            importance=importance,
        )

        call_started = datetime.now()
        importance = (importance or "normal").lower()
        task_type = task_type or self._infer_task_type()
        complexity = complexity or self._infer_complexity(messages)
        estimated_tokens = self._estimate_tokens(messages)

        if not self.smart_routing_enabled:
            result = await self._call_with_current_model(messages, tools)
            duration_ms = int((datetime.now() - call_started).total_seconds() * 1000)
            self._emit_routing_telemetry(
                task_type=task_type,
                complexity=complexity,
                importance=importance,
                prefer_speed=prefer_speed,
                prefer_cost=prefer_cost,
                estimated_tokens=estimated_tokens,
                chain=[self.model],
                attempts=[{"model": self.model, "success": "error" not in result, "latency_ms": duration_ms}],
                result=result,
                duration_ms=duration_ms,
                selected_model=self.model,
            )
            return result

        allow_subscription_primary = (
            self.subscription_primary_routing_enabled
            or (self.auto_subscription_cost_routing and prefer_cost)
        ) and (
            not self.subscription_primary_cost_only or prefer_cost or importance == "low"
        )

        chain = self.router.get_fallback_chain(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
            prefer_speed=prefer_speed,
            prefer_cost=prefer_cost,
            estimated_tokens=estimated_tokens,
            runtime_only=not allow_subscription_primary,
        )

        # Ensure current model remains a first attempt when available.
        current_config = MODELS.get(self.model) or next(
            (cfg for cfg in MODELS.values() if cfg.name == self.model),
            None,
        )
        current_available = (
            self.router.is_runtime_callable(current_config)
            if current_config and not allow_subscription_primary
            else self.router.is_model_available(current_config) if current_config else False
        )
        if self.keep_current_model_first and current_config and current_available:
            current_identity = (current_config.api_source, current_config.name, current_config.api_base)
            chain = [
                current_config,
                *[
                    cfg for cfg in chain
                    if (cfg.api_source, cfg.name, cfg.api_base) != current_identity
                ],
            ]

        original = (self.model, self.api_key, self.api_base)
        last_error: Dict[str, Any] = {"error": "No model attempt executed"}
        attempts: List[Dict[str, Any]] = []
        chain_names = [f"{cfg.api_source}:{cfg.name}" for cfg in chain] if chain else [self.model]
        can_try_subscription = self.subscription_fallback_enabled and (
            not self.subscription_critical_only or importance in {"high", "critical"}
        ) and not allow_subscription_primary

        if not chain:
            result = await self._call_with_current_model(messages, tools)
            duration_ms = int((datetime.now() - call_started).total_seconds() * 1000)
            self._emit_routing_telemetry(
                task_type=task_type,
                complexity=complexity,
                importance=importance,
                prefer_speed=prefer_speed,
                prefer_cost=prefer_cost,
                estimated_tokens=estimated_tokens,
                chain=chain_names,
                attempts=[{"model": self.model, "success": "error" not in result, "latency_ms": duration_ms}],
                result=result,
                duration_ms=duration_ms,
                selected_model=self.model,
            )
            return result

        if can_try_subscription and self.subscription_first_for_critical and importance in {"high", "critical"}:
            subscription_result = await self._call_subscription_chain(
                messages=messages,
                task_type=task_type,
                complexity=complexity,
                importance=importance,
            )
            if "error" not in subscription_result:
                attempts.append({
                    "model": subscription_result.get("model", "subscription_cli"),
                    "success": True,
                    "latency_ms": None,
                    "source": subscription_result.get("source", "subscription_cli"),
                })
                duration_ms = int((datetime.now() - call_started).total_seconds() * 1000)
                self._emit_routing_telemetry(
                    task_type=task_type,
                    complexity=complexity,
                    importance=importance,
                    prefer_speed=prefer_speed,
                    prefer_cost=prefer_cost,
                    estimated_tokens=estimated_tokens,
                    chain=chain_names,
                    attempts=attempts,
                    result=subscription_result,
                    duration_ms=duration_ms,
                    selected_model=subscription_result.get("model", "subscription_cli"),
                    source=subscription_result.get("source", "subscription_cli"),
                )
                self.model, self.api_key, self.api_base = original
                return subscription_result

        for index, cfg in enumerate(chain):
            attempt_started = datetime.now()
            if cfg.supports_api:
                self._apply_model_config(cfg)
                self._log(f"🧭 Routing attempt {index + 1}/{len(chain)} -> {self.model}")
                result = await self._call_with_current_model(messages, tools)
            else:
                remaining = self._subscription_cooldown_remaining_sec(cfg)
                if remaining > 0:
                    attempts.append({
                        "model": cfg.name,
                        "success": False,
                        "latency_ms": 0,
                        "error": f"subscription_cooldown:{remaining}s",
                        "retryable": False,
                    })
                    continue
                self._log(f"🧭 Routing attempt {index + 1}/{len(chain)} -> subscription:{cfg.name}")
                result = await self._call_single_subscription_model(
                    cfg=cfg,
                    messages=messages,
                    estimated_tokens=estimated_tokens,
                )
            attempt_latency_ms = int((datetime.now() - attempt_started).total_seconds() * 1000)

            if "error" not in result:
                if cfg.supports_api:
                    usage = result.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or estimated_tokens // 2
                    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or estimated_tokens // 2
                    self.router.record_usage(cfg.name, int(prompt_tokens), int(completion_tokens))
                attempts.append({
                    "model": cfg.name,
                    "success": True,
                    "latency_ms": attempt_latency_ms,
                    "source": result.get("source", "api"),
                })
                duration_ms = int((datetime.now() - call_started).total_seconds() * 1000)
                self._emit_routing_telemetry(
                    task_type=task_type,
                    complexity=complexity,
                    importance=importance,
                    prefer_speed=prefer_speed,
                    prefer_cost=prefer_cost,
                    estimated_tokens=estimated_tokens,
                    chain=chain_names,
                    attempts=attempts,
                    result=result,
                    duration_ms=duration_ms,
                    selected_model=cfg.name,
                    source=result.get("source", "api"),
                )
                self.model, self.api_key, self.api_base = original
                return result

            last_error = result
            error_text = str(result.get("error", "")).lower()
            error_class = self._classify_error(error_text)
            retryable = self._is_retryable_error(error_text)
            attempts.append({
                "model": cfg.name,
                "success": False,
                "latency_ms": attempt_latency_ms,
                "error": str(result.get("error", ""))[:300],
                "retryable": retryable,
                "error_class": error_class,
            })
            # Do not abort the whole routing chain on subscription/config errors.
            if not retryable and cfg.supports_api:
                break

        if can_try_subscription:
            subscription_result = await self._call_subscription_chain(
                messages=messages,
                task_type=task_type,
                complexity=complexity,
                importance=importance,
            )
            if "error" not in subscription_result:
                attempts.append({
                    "model": subscription_result.get("model", "subscription_cli"),
                    "success": True,
                    "latency_ms": None,
                    "source": subscription_result.get("source", "subscription_cli"),
                })
                duration_ms = int((datetime.now() - call_started).total_seconds() * 1000)
                self._emit_routing_telemetry(
                    task_type=task_type,
                    complexity=complexity,
                    importance=importance,
                    prefer_speed=prefer_speed,
                    prefer_cost=prefer_cost,
                    estimated_tokens=estimated_tokens,
                    chain=chain_names,
                    attempts=attempts,
                    result=subscription_result,
                    duration_ms=duration_ms,
                    selected_model=subscription_result.get("model", "subscription_cli"),
                    source=subscription_result.get("source", "subscription_cli"),
                )
                self.model, self.api_key, self.api_base = original
                return subscription_result

        duration_ms = int((datetime.now() - call_started).total_seconds() * 1000)
        self._emit_routing_telemetry(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
            prefer_speed=prefer_speed,
            prefer_cost=prefer_cost,
            estimated_tokens=estimated_tokens,
            chain=chain_names,
            attempts=attempts,
            result=last_error,
            duration_ms=duration_ms,
            selected_model=None,
        )

        self.model, self.api_key, self.api_base = original
        return last_error

    def _call_provider_type(self) -> str:
        """Infer provider type from current model/runtime config."""
        model_lower = self.model.lower()
        base_lower = (self.api_base or "").lower()

        if "gemini" in model_lower or "googleapis" in base_lower:
            return "gemini"
        if "anthropic" in base_lower or "claude" in model_lower or "minimax" in model_lower:
            return "anthropic"
        return "openai_compatible"

    async def _call_with_current_model(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """Dispatch call by inferred provider type."""
        provider = self._call_provider_type()
        if provider == "gemini":
            return await self._call_gemini(messages)
        if provider == "anthropic":
            return await self._call_anthropic(messages)
        return await self._call_glm(messages, tools)

    def _apply_model_config(self, cfg: ModelConfig) -> None:
        """Apply a routed model config onto agent runtime."""
        self.model = cfg.name
        self.api_base = cfg.api_base or self.api_base
        routed_key = self.router.get_api_key(cfg)
        if routed_key:
            self.api_key = routed_key

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """Rough token estimation from prompt length."""
        char_count = 0
        for msg in messages:
            content = msg.get("content", "")
            char_count += len(str(content))
        return max(256, int(char_count / 4))

    def _infer_task_type(self) -> TaskType:
        """Infer task type based on agent identity."""
        name = self.name.lower()
        if name == "nova":
            return TaskType.CODE_GENERATION
        if name == "pixel":
            return TaskType.UI_ANALYSIS
        if name == "echo":
            return TaskType.TEST_GENERATION
        if name == "cipher":
            return TaskType.SECURITY_AUDIT
        if name == "flux":
            return TaskType.DEPLOYMENT
        if name == "guardian":
            return TaskType.PERMISSION_REVIEW
        if name == "orion":
            return TaskType.PLANNING
        return TaskType.CODE_GENERATION

    def _infer_complexity(self, messages: List[Dict]) -> TaskComplexity:
        """Infer complexity from prompt size."""
        estimated_tokens = self._estimate_tokens(messages)
        if estimated_tokens < 600:
            return TaskComplexity.SIMPLE
        if estimated_tokens < 1800:
            return TaskComplexity.MEDIUM
        return TaskComplexity.COMPLEX

    def _is_retryable_error(self, error_text: str) -> bool:
        """Identify transient errors suitable for fallback retry."""
        return self._classify_error(error_text) == "transient"

    def _classify_error(self, error_text: str) -> str:
        """Classify provider/subscription errors for routing analytics and policy."""
        text = str(error_text or "").lower()
        if not text:
            return "unknown"
        if any(k in text for k in ["timeout", "rate limit", "quota", "429", "temporarily unavailable", "overloaded", "connection reset", "network", "service unavailable", "upstream"]):
            return "transient"
        if any(k in text for k in ["unauthorized", "invalid api key", "authentication", "forbidden", "permission denied", "insufficient_quota"]):
            return "auth"
        if "not a tty" in text or "non-interactive" in text:
            return "subscription_non_tty"
        if any(k in text for k in ["no such file", "not found", "unavailable", "missing", "not installed"]):
            return "missing_dependency"
        if any(k in text for k in ["invalid config", "yaml", "json", "parse", "unsupported", "bad option"]):
            return "config"
        return "runtime"

    def _emit_routing_telemetry(
        self,
        task_type: TaskType,
        complexity: Optional[TaskComplexity],
        importance: str,
        prefer_speed: bool,
        prefer_cost: bool,
        estimated_tokens: int,
        chain: List[str],
        attempts: List[Dict[str, Any]],
        result: Dict[str, Any],
        duration_ms: int,
        selected_model: Optional[str] = None,
        source: str = "api",
    ) -> None:
        usage = (result or {}).get("usage", {}) if isinstance(result, dict) else {}
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        total_tokens = int(prompt_tokens) + int(completion_tokens)
        selected = selected_model or self.model

        cost_usd = None
        selected_cfg = MODELS.get(selected) if selected else None
        if not selected_cfg and selected:
            selected_cfg = next((m for m in MODELS.values() if m.name == selected), None)
        if selected_cfg and total_tokens > 0:
            cost_usd = round((total_tokens / 1000.0) * selected_cfg.cost_per_1k_tokens, 6)

        record_routing_event({
            "agent": self.name,
            "task_type": task_type.value if task_type else None,
            "complexity": complexity.value if complexity else None,
            "importance": importance,
            "prefer_speed": bool(prefer_speed),
            "prefer_cost": bool(prefer_cost),
            "estimated_tokens": int(estimated_tokens),
            "chain": chain,
            "attempts": attempts,
            "selected_model": selected,
            "success": "error" not in (result or {}),
            "source": source,
            "duration_ms": int(duration_ms),
            "usage": {
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens),
            },
            "estimated_cost_usd": cost_usd,
            "error": str((result or {}).get("error", ""))[:300] if isinstance(result, dict) and "error" in result else "",
            "error_class": self._classify_error(str((result or {}).get("error", ""))) if isinstance(result, dict) and "error" in result else "",
        })

    def _compose_plain_prompt(self, messages: List[Dict]) -> str:
        """Create a plain prompt for CLI-based subscription models."""
        blocks = []
        for msg in messages:
            role = msg.get("role", "user").upper()
            content = str(msg.get("content", ""))
            blocks.append(f"[{role}]\n{content}")
        return "\n\n".join(blocks).strip()

    def _subscription_key(self, cfg: ModelConfig) -> str:
        return f"{cfg.api_source}:{cfg.name}"

    def _subscription_cooldown_remaining_sec(self, cfg: ModelConfig) -> int:
        key = self._subscription_key(cfg)
        cooldown_until = self._subscription_cooldown_until.get(key)
        if not cooldown_until:
            return 0
        remaining = int((cooldown_until - datetime.now()).total_seconds())
        if remaining <= 0:
            self._subscription_cooldown_until.pop(key, None)
            return 0
        return remaining

    def _mark_subscription_failure(self, cfg: ModelConfig, error: str = "") -> None:
        key = self._subscription_key(cfg)
        count = int(self._subscription_failure_counts.get(key, 0)) + 1
        self._subscription_failure_counts[key] = count
        if count < self.subscription_failure_threshold:
            return

        self._subscription_failure_counts[key] = 0
        cooldown_until = datetime.now() + timedelta(seconds=self.subscription_failure_cooldown_sec)
        self._subscription_cooldown_until[key] = cooldown_until
        error_text = str(error or "").strip()
        error_hint = f" ({error_text[:140]})" if error_text else ""
        self._log(
            f"⏸️ Subscription cooldown enabled for {cfg.name}: "
            f"{self.subscription_failure_cooldown_sec}s{error_hint}"
        )

    def _mark_subscription_success(self, cfg: ModelConfig) -> None:
        key = self._subscription_key(cfg)
        self._subscription_failure_counts.pop(key, None)
        self._subscription_cooldown_until.pop(key, None)

    def _build_subscription_command(self, api_source: str, prompt: str, model_name: Optional[str] = None) -> Optional[List[str]]:
        """Build CLI command for subscription models."""
        if api_source == "subscription_codex":
            cmd = os.getenv("CODEX_CLI_CMD", "codex").strip()
            args_template = os.getenv("CODEX_CLI_ARGS", "-p").strip()
        elif api_source == "subscription_claude":
            cmd = os.getenv("CLAUDE_CLI_CMD", "claude").strip()
            args_template = os.getenv("CLAUDE_CLI_ARGS", "-p").strip()
        elif api_source == "subscription_gemini":
            cmd = os.getenv("GEMINI_CLI_CMD", "gemini").strip()
            args_template = os.getenv("GEMINI_CLI_ARGS", "-m {model} -p").strip()
        else:
            return None

        cmd_path = shutil.which(cmd)
        if not cmd_path:
            return None

        args = shlex.split(args_template) if args_template else []
        has_prompt_placeholder = any("{prompt}" in arg for arg in args)
        model_name = model_name or ""

        resolved_args: List[str] = []
        for arg in args:
            if "{model}" in arg:
                arg = arg.replace("{model}", model_name)
            if "{prompt}" in arg:
                arg = arg.replace("{prompt}", prompt)
            if arg:
                resolved_args.append(arg)

        if has_prompt_placeholder:
            return [cmd_path] + resolved_args
        return [cmd_path] + resolved_args + [prompt]

    async def _call_single_subscription_model(
        self,
        cfg: ModelConfig,
        messages: List[Dict],
        estimated_tokens: Optional[int] = None,
    ) -> Dict:
        """Call one subscription CLI profile."""
        remaining = self._subscription_cooldown_remaining_sec(cfg)
        if remaining > 0:
            return {"error": f"subscription_cooldown_active:{remaining}s"}

        prompt = self._compose_plain_prompt(messages)
        command = self._build_subscription_command(
            api_source=cfg.api_source,
            prompt=prompt,
            model_name=cfg.name,
        )
        if not command:
            self._mark_subscription_failure(cfg, "subscription_cli_unavailable")
            return {"error": f"subscription_cli_unavailable:{cfg.api_source}"}
        if self.subscription_skip_when_no_tty and not sys.stdin.isatty():
            failure = f"subscription_cli_skipped_non_tty:{cfg.name}"
            self._mark_subscription_failure(cfg, failure)
            return {"error": failure}

        timeout_sec = int(os.getenv("SUBSCRIPTION_TIMEOUT_SEC", "120"))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
            output = (stdout or b"").decode("utf-8", errors="ignore").strip()
            error = (stderr or b"").decode("utf-8", errors="ignore").strip()

            if process.returncode == 0 and output:
                prompt_tokens = estimated_tokens or self._estimate_tokens(messages)
                completion_tokens = max(64, int(len(output) / 4))
                self.router.record_usage(cfg.name, prompt_tokens, completion_tokens)
                self._mark_subscription_success(cfg)
                return {
                    "choices": [{"message": {"content": output}}],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    },
                    "model": cfg.name,
                    "source": "subscription_cli",
                }

            failure = error or f"subscription_cli_failed:{cfg.name}"
            self._mark_subscription_failure(cfg, failure)
            return {"error": failure}
        except asyncio.TimeoutError:
            self._mark_subscription_failure(cfg, "subscription_cli_timeout")
            return {"error": f"subscription_cli_timeout:{cfg.name}"}
        except Exception as exc:
            self._mark_subscription_failure(cfg, f"subscription_cli_exception:{exc}")
            return {"error": f"subscription_cli_exception:{cfg.name}:{exc}"}

    async def _call_subscription_chain(
        self,
        messages: List[Dict],
        task_type: TaskType,
        complexity: Optional[TaskComplexity],
        importance: str,
    ) -> Dict:
        """Fallback to subscription CLIs (Codex/Claude/Gemini) when API route fails."""
        chain = self.router.get_fallback_chain(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
            runtime_only=False,
        )
        subscription_models = [
            cfg for cfg in chain
            if cfg.is_subscription and self.router.is_model_available(cfg)
        ]
        if not subscription_models:
            return {"error": "no_subscription_model_available"}

        estimated_tokens = self._estimate_tokens(messages)
        for cfg in subscription_models:
            remaining = self._subscription_cooldown_remaining_sec(cfg)
            if remaining > 0:
                continue
            self._log(f"🧭 Subscription fallback -> {cfg.name}")
            result = await self._call_single_subscription_model(
                cfg=cfg,
                messages=messages,
                estimated_tokens=estimated_tokens,
            )
            if "error" not in result:
                return result
            self._log(f"⚠️ Subscription CLI failed ({cfg.name}): {result.get('error', 'unknown error')}")

        return {"error": "subscription_fallback_failed"}

    async def _call_glm(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """Call GLM API (OpenAI-compatible)"""
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

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    result = await response.json()

                    if "error" in result:
                        self._log(f"❌ API Error: {result['error']}")
                        return {"error": result["error"]}

                    return result

        except asyncio.TimeoutError:
            self._log("⏱️ API timeout")
            return {"error": "timeout"}
        except Exception as e:
            self._log(f"❌ API Exception: {str(e)}")
            return {"error": str(e)}

    async def _call_gemini(self, messages: List[Dict]) -> Dict:
        """Call Gemini API"""
        base = self.api_base or "https://generativelanguage.googleapis.com/v1beta"
        base = base.rstrip("/")
        if not base.endswith("/v1beta"):
            base = "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{self.model}:generateContent"

        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                continue  # Gemini handles system differently
            contents.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": [{"text": msg["content"]}]
            })

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 4096
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{url}?key={self.api_key}",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    result = await response.json()

                    if "error" in result:
                        self._log(f"❌ Gemini Error: {result['error']}")
                        return {"error": result["error"]}

                    # Convert Gemini response to common format
                    text = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    return {"choices": [{"message": {"content": text}}]}

        except asyncio.TimeoutError:
            self._log("⏱️ Gemini timeout")
            return {"error": "timeout"}
        except Exception as e:
            self._log(f"❌ Gemini Exception: {str(e)}")
            return {"error": str(e)}

    async def _call_anthropic(self, messages: List[Dict]) -> Dict:
        """Call Anthropic-compatible API (MiniMax gateway, Claude-compatible)."""
        base = (self.api_base or "https://api.minimax.io/anthropic").rstrip("/")
        if base.endswith("/v1"):
            url = f"{base}/messages"
        else:
            url = f"{base}/v1/messages"

        system_parts = []
        anthropic_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))
            if role == "system":
                system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            anthropic_messages.append({"role": role, "content": content})

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": anthropic_messages or [{"role": "user", "content": "Continue."}],
            "temperature": 0.7,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as response:
                    result = await response.json()

                    if "error" in result:
                        self._log(f"❌ Anthropic-compatible Error: {result['error']}")
                        return {"error": result["error"]}

                    blocks = result.get("content", [])
                    text_parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
                    text = "\n".join(part for part in text_parts if part).strip()
                    usage = result.get("usage", {})
                    return {
                        "choices": [{"message": {"content": text}}],
                        "usage": {
                            "prompt_tokens": usage.get("input_tokens", 0),
                            "completion_tokens": usage.get("output_tokens", 0),
                        },
                    }
        except asyncio.TimeoutError:
            self._log("⏱️ Anthropic-compatible timeout")
            return {"error": "timeout"}
        except Exception as e:
            self._log(f"❌ Anthropic-compatible Exception: {str(e)}")
            return {"error": str(e)}

    async def call_vision_api(self, image_path: str, prompt: str, importance: str = "normal") -> Dict:
        """Call vision API (for image analysis)"""
        prompt = self.prompt_system.inject_text_prompt(
            prompt=prompt,
            agent_name=self.name,
            role=self.role,
            importance=importance,
        )

        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        if "gemini" in self.model.lower():
            return await self._call_gemini_vision(image_data, prompt)
        else:
            return await self._call_glm_vision(image_data, prompt)

    async def _call_glm_vision(self, image_data: str, prompt: str) -> Dict:
        """Call GLM Vision API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_data}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "max_tokens": 4096
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180)
                ) as response:
                    result = await response.json()
                    return result
        except Exception as e:
            self._log(f"❌ Vision API Error: {str(e)}")
            return {"error": str(e)}

    async def _call_gemini_vision(self, image_data: str, prompt: str) -> Dict:
        """Call Gemini Vision API"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_data
                            }
                        },
                        {"text": prompt}
                    ]
                }
            ]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{url}?key={self.api_key}",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180)
                ) as response:
                    result = await response.json()
                    text = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    return {"choices": [{"message": {"content": text}}]}
        except Exception as e:
            self._log(f"❌ Gemini Vision Error: {str(e)}")
            return {"error": str(e)}

    def _log(self, message: str):
        """Log message to file and console"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{self.name}] {message}"

        # Console
        print(log_line)

        # File
        log_file = self.log_dir / f"{self.name.lower()}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")

    def _parse_json_response(self, response: Dict) -> Dict:
        """Safely parse JSON from API response"""
        try:
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")

            # Try to extract JSON from the response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            return json.loads(content.strip())
        except json.JSONDecodeError:
            return {"raw": content, "parse_error": True}

    def create_result(
        self,
        success: bool,
        output: Any,
        score: float = None,
        issues: List[str] = None,
        suggestions: List[str] = None,
        veto: bool = False,
        veto_reason: str = None
    ) -> TaskResult:
        """Create a standardized task result"""
        return TaskResult(
            success=success,
            agent=self.name,
            output=output,
            score=score,
            issues=issues or [],
            suggestions=suggestions or [],
            veto=veto,
            veto_reason=veto_reason
        )
