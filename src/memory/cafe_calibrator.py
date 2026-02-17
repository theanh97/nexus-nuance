"""Automated calibration for CAFE model confidence bias."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .storage_v2 import get_storage_v2
from .cafe_loop import get_cafe_scorer


def _clamp(value: float, lo: float = -0.2, hi: float = 0.2) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _env_list(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [token.strip().lower() for token in str(raw).split(",") if token.strip()]


DEFAULT_VERIFICATION_THRESHOLDS: Dict[str, float] = {
    "health_improve": 1.0,
    "health_decline": -1.0,
    "latency_improve_ms": -100.0,
    "latency_regress_ms": 200.0,
    "success_improve": 0.02,
    "success_regress": -0.02,
    "weak_signal_abs_health": 0.5,
    "weak_signal_abs_latency_ms": 50.0,
    "weak_signal_abs_success_rate": 0.01,
    "pending_recheck_confidence_max": 0.58,
}

VERIFICATION_THRESHOLD_BOUNDS: Dict[str, Tuple[float, float]] = {
    "health_improve": (0.6, 1.2),
    "health_decline": (-1.2, -0.6),
    "latency_improve_ms": (-180.0, -60.0),
    "latency_regress_ms": (120.0, 280.0),
    "success_improve": (0.01, 0.03),
    "success_regress": (-0.03, -0.01),
    "weak_signal_abs_health": (0.35, 0.8),
    "weak_signal_abs_latency_ms": (30.0, 120.0),
    "weak_signal_abs_success_rate": (0.006, 0.03),
    "pending_recheck_confidence_max": (0.52, 0.68),
}


def _percentile(values: List[float], quantile: float) -> float:
    cleaned: List[float] = []
    for value in values:
        try:
            cleaned.append(float(value))
        except (TypeError, ValueError):
            continue
    if not cleaned:
        return 0.0
    cleaned.sort()
    quantile = max(0.0, min(1.0, float(quantile)))
    idx = int(round((len(cleaned) - 1) * quantile))
    return float(cleaned[idx])


class CafeCalibrator:
    def __init__(self):
        self.storage = get_storage_v2()
        self.state_path = self.storage.state_path / "cafe_state.json"
        self.min_samples = max(2, _env_int("CAFE_CALIBRATION_MIN_SAMPLES", 6))
        self.bias_scale = _env_float("CAFE_CALIBRATION_BIAS_SCALE", 0.12)
        self.bias_cap = _env_float("CAFE_CALIBRATION_BIAS_CAP", 0.15)
        self.smoothing = _env_float("CAFE_CALIBRATION_SMOOTHING", 0.3)
        self.family_tokens = _env_list("CAFE_MODEL_FAMILY_KEYS", "codex,gpt,chatgpt,claude,sonnet,opus,haiku,gemini")
        self.threshold_smoothing = _env_float("VERIFICATION_THRESHOLD_SMOOTHING", self.smoothing)
        self.threshold_min_samples = max(2, _env_int("VERIFICATION_THRESHOLD_MIN_SAMPLES", self.min_samples))

    def _parse_model(self, evidence: Dict[str, Any]) -> str:
        model = evidence.get("model")
        if model:
            return str(model).strip()
        cafe = evidence.get("cafe") if isinstance(evidence.get("cafe"), dict) else {}
        model = cafe.get("model")
        if model:
            return str(model).strip()
        execution = evidence.get("execution") if isinstance(evidence.get("execution"), dict) else {}
        model = execution.get("model")
        if model:
            return str(model).strip()
        return ""

    def _family_key(self, model_name: str) -> str:
        name = model_name.lower()
        for token in self.family_tokens:
            if token and token in name:
                return token
        return name if name else "unknown"

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_state(self, data: Dict[str, Any]) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(f"{self.state_path.name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except Exception:
            return

    def _aggregate(self, evidences: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
        stats: Dict[str, Dict[str, int]] = {}
        for evd in evidences:
            if not isinstance(evd, dict):
                continue
            verdict = str(evd.get("verdict", "")).strip().lower()
            if verdict not in {"win", "loss", "inconclusive"}:
                continue
            model_name = self._parse_model(evd)
            if not model_name:
                continue
            key = self._family_key(model_name)
            row = stats.setdefault(key, {"win": 0, "loss": 0, "inconclusive": 0, "total": 0})
            row[verdict] += 1
            row["total"] += 1
        return stats

    def _resolve_thresholds(self, raw: Any) -> Dict[str, float]:
        source = raw if isinstance(raw, dict) else {}
        resolved: Dict[str, float] = {}
        for key, default in DEFAULT_VERIFICATION_THRESHOLDS.items():
            try:
                value = float(source.get(key, default))
            except (TypeError, ValueError):
                value = float(default)
            lo, hi = VERIFICATION_THRESHOLD_BOUNDS.get(key, (float(default), float(default)))
            resolved[key] = _clamp(value, lo, hi)
        return resolved

    def _calibrate_verification_thresholds(
        self,
        evidences: List[Dict[str, Any]],
        previous_thresholds: Dict[str, float],
    ) -> Dict[str, Any]:
        usable = []
        for evd in evidences:
            if not isinstance(evd, dict):
                continue
            if bool(evd.get("holdout_pending", False)):
                continue
            delta = evd.get("delta") if isinstance(evd.get("delta"), dict) else {}
            verdict = str(evd.get("verdict", "")).strip().lower()
            if verdict not in {"win", "loss", "inconclusive"}:
                continue
            usable.append((delta, verdict))

        sample_count = len(usable)
        if sample_count < self.threshold_min_samples:
            return {
                "thresholds": previous_thresholds,
                "summary": {
                    "samples": sample_count,
                    "applied": False,
                    "reason": "insufficient_samples",
                },
            }

        health_abs: List[float] = []
        latency_abs: List[float] = []
        success_abs: List[float] = []
        inconclusive_count = 0

        for delta, verdict in usable:
            if verdict == "inconclusive":
                inconclusive_count += 1
            try:
                health_abs.append(abs(float(delta.get("health_score", 0.0) or 0.0)))
            except (TypeError, ValueError):
                pass
            try:
                latency_abs.append(abs(float(delta.get("avg_duration_ms", 0.0) or 0.0)))
            except (TypeError, ValueError):
                pass
            try:
                success_abs.append(abs(float(delta.get("success_rate", 0.0) or 0.0)))
            except (TypeError, ValueError):
                pass

        noise_health = _percentile(health_abs, 0.75)
        noise_latency = _percentile(latency_abs, 0.75)
        noise_success = _percentile(success_abs, 0.75)
        inconclusive_rate = float(inconclusive_count) / float(sample_count) if sample_count else 0.0

        decisiveness_boost = max(0.0, min(0.2, (inconclusive_rate - 0.4) * 0.8))

        health_mag = max(noise_health * 1.4, abs(DEFAULT_VERIFICATION_THRESHOLDS["health_improve"]))
        latency_mag = max(noise_latency * 1.5, abs(DEFAULT_VERIFICATION_THRESHOLDS["latency_improve_ms"]))
        success_mag = max(noise_success * 1.8, abs(DEFAULT_VERIFICATION_THRESHOLDS["success_improve"]))
        weak_health_mag = max(noise_health * 1.1, DEFAULT_VERIFICATION_THRESHOLDS["weak_signal_abs_health"])
        weak_latency_mag = max(noise_latency * 1.1, DEFAULT_VERIFICATION_THRESHOLDS["weak_signal_abs_latency_ms"])
        weak_success_mag = max(noise_success * 1.1, DEFAULT_VERIFICATION_THRESHOLDS["weak_signal_abs_success_rate"])

        reduce_factor = max(0.8, 1.0 - decisiveness_boost)

        target = {
            "health_improve": health_mag * reduce_factor,
            "health_decline": -(health_mag * reduce_factor),
            "latency_improve_ms": -(latency_mag * reduce_factor),
            "latency_regress_ms": latency_mag * reduce_factor,
            "success_improve": success_mag * reduce_factor,
            "success_regress": -(success_mag * reduce_factor),
            "weak_signal_abs_health": weak_health_mag,
            "weak_signal_abs_latency_ms": weak_latency_mag,
            "weak_signal_abs_success_rate": weak_success_mag,
            "pending_recheck_confidence_max": (
                DEFAULT_VERIFICATION_THRESHOLDS["pending_recheck_confidence_max"] - (decisiveness_boost * 0.08)
            ),
        }

        calibrated_target: Dict[str, float] = {}
        for key, default in DEFAULT_VERIFICATION_THRESHOLDS.items():
            lo, hi = VERIFICATION_THRESHOLD_BOUNDS.get(key, (float(default), float(default)))
            calibrated_target[key] = _clamp(float(target.get(key, default)), lo, hi)

        smoothed: Dict[str, float] = {}
        for key, default in DEFAULT_VERIFICATION_THRESHOLDS.items():
            previous_value = float(previous_thresholds.get(key, default))
            target_value = float(calibrated_target.get(key, default))
            blended = (1.0 - self.threshold_smoothing) * previous_value + self.threshold_smoothing * target_value
            lo, hi = VERIFICATION_THRESHOLD_BOUNDS.get(key, (float(default), float(default)))
            smoothed[key] = _clamp(blended, lo, hi)

        return {
            "thresholds": smoothed,
            "summary": {
                "samples": sample_count,
                "applied": True,
                "inconclusive_rate": round(inconclusive_rate, 4),
                "noise_p75": {
                    "health_score": round(noise_health, 4),
                    "avg_duration_ms": round(noise_latency, 4),
                    "success_rate": round(noise_success, 4),
                },
                "decisiveness_boost": round(decisiveness_boost, 4),
            },
        }

    def calibrate(self, evidences: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        evidences = evidences if evidences is not None else self.storage.list_outcome_evidence(limit=1200)
        stats = self._aggregate(evidences)
        previous = self._load_state()
        existing_bias = previous.get("model_bias", {}) if isinstance(previous.get("model_bias"), dict) else {}

        updated_bias: Dict[str, float] = {}
        updated_stats: Dict[str, Dict[str, float]] = {}
        for model_key, row in stats.items():
            total = int(row.get("total", 0) or 0)
            if total < self.min_samples:
                continue
            win_rate = row["win"] / total
            loss_rate = row["loss"] / total
            inconclusive_rate = row["inconclusive"] / total
            score = win_rate - loss_rate - (0.5 * inconclusive_rate)
            target = _clamp(score * self.bias_scale, -self.bias_cap, self.bias_cap)
            previous_bias = float(existing_bias.get(model_key, 0.0) or 0.0)
            blended = (1.0 - self.smoothing) * previous_bias + self.smoothing * target
            updated_bias[model_key] = _clamp(blended, -self.bias_cap, self.bias_cap)
            updated_stats[model_key] = {
                "win_rate": round(win_rate, 4),
                "loss_rate": round(loss_rate, 4),
                "inconclusive_rate": round(inconclusive_rate, 4),
                "score": round(score, 4),
                "samples": total,
            }

        previous_thresholds = self._resolve_thresholds(previous.get("verification_thresholds"))
        threshold_calibration = self._calibrate_verification_thresholds(evidences, previous_thresholds)
        verification_thresholds = self._resolve_thresholds(threshold_calibration.get("thresholds"))

        payload = {
            "model_bias": updated_bias,
            "model_stats": updated_stats,
            "verification_thresholds": verification_thresholds,
            "verification_threshold_summary": threshold_calibration.get("summary", {}),
            "min_samples": self.min_samples,
            "bias_scale": self.bias_scale,
            "bias_cap": self.bias_cap,
            "smoothing": self.smoothing,
            "threshold_smoothing": self.threshold_smoothing,
            "threshold_min_samples": self.threshold_min_samples,
            "family_tokens": self.family_tokens,
            "last_updated": datetime.now().isoformat(),
        }
        self._save_state(payload)
        get_cafe_scorer().set_model_conf_bias(updated_bias)

        return {
            "updated": len(updated_bias),
            "min_samples": self.min_samples,
            "last_updated": payload["last_updated"],
            "model_bias": updated_bias,
            "model_stats": updated_stats,
            "verification_thresholds": verification_thresholds,
            "verification_threshold_summary": threshold_calibration.get("summary", {}),
        }

    def get_state(self) -> Dict[str, Any]:
        return self._load_state()


_cafe_calibrator: Optional[CafeCalibrator] = None


def get_cafe_calibrator() -> CafeCalibrator:
    global _cafe_calibrator
    if _cafe_calibrator is None:
        _cafe_calibrator = CafeCalibrator()
    return _cafe_calibrator
