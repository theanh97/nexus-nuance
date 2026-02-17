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


class CafeCalibrator:
    def __init__(self):
        self.storage = get_storage_v2()
        self.state_path = self.storage.state_path / "cafe_state.json"
        self.min_samples = max(2, _env_int("CAFE_CALIBRATION_MIN_SAMPLES", 6))
        self.bias_scale = _env_float("CAFE_CALIBRATION_BIAS_SCALE", 0.12)
        self.bias_cap = _env_float("CAFE_CALIBRATION_BIAS_CAP", 0.15)
        self.smoothing = _env_float("CAFE_CALIBRATION_SMOOTHING", 0.3)
        self.family_tokens = _env_list("CAFE_MODEL_FAMILY_KEYS", "codex,gpt,chatgpt,claude,sonnet,opus,haiku,gemini")

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

        payload = {
            "model_bias": updated_bias,
            "model_stats": updated_stats,
            "min_samples": self.min_samples,
            "bias_scale": self.bias_scale,
            "bias_cap": self.bias_cap,
            "smoothing": self.smoothing,
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
        }

    def get_state(self) -> Dict[str, Any]:
        return self._load_state()


_cafe_calibrator: Optional[CafeCalibrator] = None


def get_cafe_calibrator() -> CafeCalibrator:
    global _cafe_calibrator
    if _cafe_calibrator is None:
        _cafe_calibrator = CafeCalibrator()
    return _cafe_calibrator
