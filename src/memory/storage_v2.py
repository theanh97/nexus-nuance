"""File storage helpers for self-learning v2."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class LearningStorageV2:
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, base_path: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data"
            except Exception:
                self.base_path = Path.cwd() / "data"

        self.memory_path = self.base_path / "memory"
        self.experiments_path = self.base_path / "experiments"
        self.state_path = self.base_path / "state"
        self.memory_path.mkdir(parents=True, exist_ok=True)
        self.experiments_path.mkdir(parents=True, exist_ok=True)
        self.state_path.mkdir(parents=True, exist_ok=True)

        self.learning_events_file = self.memory_path / "learning_events.jsonl"
        self.proposals_v2_file = self.memory_path / "improvement_proposals_v2.json"
        self.experiment_runs_file = self.experiments_path / "experiment_runs_v2.json"
        self.outcome_evidence_file = self.memory_path / "outcome_evidence.jsonl"
        self.policy_state_file = self.state_path / "learning_policy_state.json"

        self._lock = threading.RLock()
        self._init_files()
        self._initialized = True

    def _init_files(self) -> None:
        if not self.proposals_v2_file.exists():
            self._save_json(self.proposals_v2_file, {"proposals": [], "pending": [], "updated_at": datetime.now().isoformat()})
        if not self.experiment_runs_file.exists():
            self._save_json(self.experiment_runs_file, {"runs": [], "updated_at": datetime.now().isoformat()})
        if not self.policy_state_file.exists():
            self._save_json(self.policy_state_file, {})

    def _load_json(self, path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        default = default or {}
        try:
            with self._lock:
                if not path.exists():
                    return dict(default)
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            return dict(default)

    def _save_json(self, path: Path, data: Dict[str, Any]) -> bool:
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, path)
            return True
        except Exception:
            return False

    def append_jsonl(self, path: Path, payload: Dict[str, Any]) -> bool:
        try:
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False

    def tail_jsonl(self, path: Path, limit: int = 100) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            with self._lock:
                with open(path, "r", encoding="utf-8") as f:
                    lines = [x.strip() for x in f.readlines() if x.strip()]
            out: List[Dict[str, Any]] = []
            for line in lines[-max(1, limit) :]:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return out
        except Exception:
            return []

    def _is_non_production_source(self, source: str) -> bool:
        src = str(source or "").strip().lower()
        if not src:
            return False
        exact = {
            "unit_test",
            "manual_test",
            "manual_check",
            "manual_boost",
            "demo",
            "debug",
            "local_debug",
        }
        if src in exact:
            return True
        prefixes = ("test_", "unit_", "manual_", "debug_", "demo_")
        return any(src.startswith(p) for p in prefixes)

    def record_learning_event(self, event: Dict[str, Any]) -> str:
        event_id = str(event.get("id") or f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
        payload = dict(event)
        payload["id"] = event_id
        payload.setdefault("ts", datetime.now().isoformat())
        source = payload.get("source", "")
        stream = str(payload.get("stream", "")).strip().lower()
        if stream not in {"production", "non_production"}:
            stream = "non_production" if self._is_non_production_source(source) else "production"
        payload["stream"] = stream
        payload["is_non_production"] = bool(stream == "non_production")
        self.append_jsonl(self.learning_events_file, payload)
        return event_id

    def list_learning_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.tail_jsonl(self.learning_events_file, limit=limit)

    def get_proposals_v2(self) -> Dict[str, Any]:
        return self._load_json(self.proposals_v2_file, {"proposals": [], "pending": []})

    def save_proposals_v2(self, data: Dict[str, Any]) -> bool:
        data = dict(data)
        data["updated_at"] = datetime.now().isoformat()
        return self._save_json(self.proposals_v2_file, data)

    def add_experiment_run(self, run: Dict[str, Any]) -> bool:
        data = self._load_json(self.experiment_runs_file, {"runs": []})
        runs = data.get("runs", []) if isinstance(data.get("runs"), list) else []
        runs.append(run)
        data["runs"] = runs[-3000:]
        data["updated_at"] = datetime.now().isoformat()
        return self._save_json(self.experiment_runs_file, data)

    def update_experiment_run(self, run_id: str, updates: Dict[str, Any]) -> bool:
        data = self._load_json(self.experiment_runs_file, {"runs": []})
        runs = data.get("runs", []) if isinstance(data.get("runs"), list) else []
        changed = False
        for run in runs:
            if run.get("id") == run_id:
                run.update(updates)
                changed = True
                break
        data["runs"] = runs
        data["updated_at"] = datetime.now().isoformat()
        return self._save_json(self.experiment_runs_file, data) if changed else False

    def get_experiment_runs(self, limit: int = 200) -> List[Dict[str, Any]]:
        data = self._load_json(self.experiment_runs_file, {"runs": []})
        runs = data.get("runs", []) if isinstance(data.get("runs"), list) else []
        return runs[-max(1, limit) :]

    def record_outcome_evidence(self, evidence: Dict[str, Any]) -> str:
        evidence_id = str(evidence.get("id") or f"evd_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
        payload = dict(evidence)
        payload["id"] = evidence_id
        payload.setdefault("ts", datetime.now().isoformat())
        self.append_jsonl(self.outcome_evidence_file, payload)
        return evidence_id

    def list_outcome_evidence(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.tail_jsonl(self.outcome_evidence_file, limit=limit)

    def get_policy_state(self) -> Dict[str, Any]:
        return self._load_json(self.policy_state_file, {})

    def save_policy_state(self, data: Dict[str, Any]) -> bool:
        payload = dict(data)
        payload["updated_at"] = datetime.now().isoformat()
        return self._save_json(self.policy_state_file, payload)


_storage_v2: Optional[LearningStorageV2] = None


def get_storage_v2() -> LearningStorageV2:
    global _storage_v2
    if _storage_v2 is None:
        _storage_v2 = LearningStorageV2()
    return _storage_v2


def record_learning_event(event: Dict[str, Any]) -> str:
    return get_storage_v2().record_learning_event(event)


def list_learning_events(limit: int = 100) -> List[Dict[str, Any]]:
    return get_storage_v2().list_learning_events(limit)
