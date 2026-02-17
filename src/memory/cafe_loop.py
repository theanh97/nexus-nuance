"""Confidence-Aware Feedback Ensemble (CAFE) scoring for self-learning."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false")
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return sum((v - avg) ** 2 for v in values) / len(values)


class CAFEScorer:
    """Heuristic multi-channel scorer with uncertainty estimation."""

    def __init__(self) -> None:
        self.enabled = _env_bool("ENABLE_CAFE_LOOP", True)
        self.conf_min = _env_float("CAFE_CONFIDENCE_MIN", 0.6)
        self.helpful_min = _env_float("CAFE_HELPFUL_MIN", 0.5)
        self.harmless_min = _env_float("CAFE_HARMLESS_MIN", 0.55)
        self.weight_helpful = _env_float("CAFE_WEIGHT_HELPFUL", 0.5)
        self.weight_harmless = _env_float("CAFE_WEIGHT_HARMLESS", 0.3)
        self.weight_reliability = _env_float("CAFE_WEIGHT_RELIABILITY", 0.2)
        self.model_conf_bias = self._load_model_conf_bias()

    def _state_path(self) -> Path:
        try:
            project_root = Path(__file__).parent.parent.parent
            return project_root / "data" / "state" / "cafe_state.json"
        except Exception:
            return Path.cwd() / "data" / "state" / "cafe_state.json"

    def _load_model_conf_bias(self) -> Dict[str, float]:
        raw = os.getenv("CAFE_MODEL_CONF_BIAS_JSON", "").strip()
        if not raw:
            path = self._state_path()
            if not path.exists():
                return {}
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("model_bias"), dict):
                    raw_bias = data.get("model_bias", {})
                else:
                    raw_bias = {}
            except Exception:
                return {}
        else:
            try:
                raw_bias = json.loads(raw)
            except Exception:
                return {}
        if not isinstance(raw_bias, dict):
            return {}
        out: Dict[str, float] = {}
        for key, value in raw_bias.items():
            try:
                out[str(key).strip().lower()] = float(value)
            except (TypeError, ValueError):
                continue
        return out

    def set_model_conf_bias(self, bias: Dict[str, float]) -> None:
        if isinstance(bias, dict):
            self.model_conf_bias = {
                str(key).strip().lower(): float(value)
                for key, value in bias.items()
                if str(key).strip()
            }

    def _extract_model_name(self, payload: Dict[str, Any]) -> str:
        model = payload.get("model")
        if model:
            return str(model).strip()
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        for key in ("model", "model_name", "selected_model", "route_model"):
            value = context.get(key)
            if value:
                return str(value).strip()
        return ""

    def _model_bias(self, model_name: str) -> float:
        if not model_name:
            return 0.0
        name = model_name.strip().lower()
        if name in self.model_conf_bias:
            return self.model_conf_bias[name]
        for key, bias in self.model_conf_bias.items():
            if key and key in name:
                return bias
        return 0.0

    def score_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}

        value = _clamp(event.get("value_score", 0.0))
        novelty = _clamp(event.get("novelty_score", 0.0))
        confidence = _clamp(event.get("confidence", 0.0))
        risk = _clamp(event.get("risk_score", 0.0))
        model_name = self._extract_model_name(event)
        model_bias = _clamp(self._model_bias(model_name), lo=-0.2, hi=0.2)

        helpful_candidates = [
            value,
            _clamp((value + novelty) / 2.0),
            _clamp(value * 0.7 + confidence * 0.3),
        ]
        harmless_candidates = [
            _clamp(1.0 - risk),
            _clamp(1.0 - (risk * 1.1)),
            _clamp((1.0 - risk) * 0.8 + 0.2),
        ]
        reliability_candidates = [
            confidence,
            _clamp((confidence + (1.0 - risk)) / 2.0),
            _clamp(1.0 - abs(value - risk)),
        ]

        helpful = _clamp(_mean(helpful_candidates))
        harmless = _clamp(_mean(harmless_candidates))
        reliability = _clamp(_mean(reliability_candidates))

        variance = _mean([
            _variance(helpful_candidates),
            _variance(harmless_candidates),
            _variance(reliability_candidates),
        ])
        ensemble_conf = _clamp(1.0 - (variance * 2.0))
        combined_conf = _clamp((confidence + ensemble_conf) / 2.0)
        if model_bias:
            combined_conf = _clamp(combined_conf + model_bias)

        score = _clamp(
            (self.weight_helpful * helpful)
            + (self.weight_harmless * harmless)
            + (self.weight_reliability * reliability)
        )

        blocked = combined_conf < self.conf_min and harmless < self.harmless_min
        reasons: List[str] = []
        if combined_conf < self.conf_min:
            reasons.append("low_confidence")
        if helpful < self.helpful_min:
            reasons.append("low_helpfulness")
        if harmless < self.harmless_min:
            reasons.append("low_harmlessness")

        return {
            "enabled": True,
            "score": score,
            "confidence": combined_conf,
            "helpful": helpful,
            "harmless": harmless,
            "reliability": reliability,
            "blocked": blocked,
            "reasons": reasons,
            "model": model_name or None,
            "model_conf_bias": model_bias,
            "weights": {
                "helpful": self.weight_helpful,
                "harmless": self.weight_harmless,
                "reliability": self.weight_reliability,
            },
        }

    def score_evidence(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}

        verdict = str(evidence.get("verdict", "")).strip().lower()
        delta = evidence.get("delta", {}) if isinstance(evidence.get("delta"), dict) else {}
        conf = _clamp(evidence.get("confidence", 0.0))

        delta_health = float(delta.get("health_score", 0.0) or 0.0)
        delta_errors = float(delta.get("total_errors", 0.0) or 0.0)
        delta_latency = float(delta.get("avg_duration_ms", 0.0) or 0.0)
        delta_success = float(delta.get("success_rate", 0.0) or 0.0)

        helpful = 0.5
        if verdict == "win":
            helpful = 0.85
        elif verdict == "loss":
            helpful = 0.2
        helpful = _clamp(helpful + _clamp(delta_success * 5.0))

        harmless = 0.7
        if delta_health <= -2.0 or delta_errors >= 2:
            harmless = 0.2
        elif delta_health >= 0.5 and delta_errors <= 0:
            harmless = 0.85

        reliability = _clamp(conf * 0.8 + (0.2 if verdict != "inconclusive" else 0.0))

        model_name = self._extract_model_name(evidence)
        model_bias = _clamp(self._model_bias(model_name), lo=-0.2, hi=0.2)
        if model_bias:
            reliability = _clamp(reliability + model_bias)

        score = _clamp(
            (self.weight_helpful * helpful)
            + (self.weight_harmless * harmless)
            + (self.weight_reliability * reliability)
        )

        blocked = reliability < self.conf_min and harmless < self.harmless_min
        reasons: List[str] = []
        if reliability < self.conf_min:
            reasons.append("low_confidence")
        if harmless < self.harmless_min:
            reasons.append("low_harmlessness")

        return {
            "enabled": True,
            "score": score,
            "confidence": reliability,
            "helpful": helpful,
            "harmless": harmless,
            "reliability": reliability,
            "blocked": blocked,
            "reasons": reasons,
            "model": model_name or None,
            "model_conf_bias": model_bias,
        }


_cafe_scorer: Optional[CAFEScorer] = None


def get_cafe_scorer() -> CAFEScorer:
    global _cafe_scorer
    if _cafe_scorer is None:
        _cafe_scorer = CAFEScorer()
    return _cafe_scorer
