"""
Runtime guard utilities to prevent conflicting concurrent process instances.
"""

import atexit
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

try:
    import fcntl  # POSIX only (macOS/Linux)
except Exception:  # pragma: no cover - fallback for non-POSIX runtime
    fcntl = None


class ProcessSingleton:
    """
    Process-level singleton lock using a filesystem lock file.
    Prevents multiple conflicting runtime instances from running at once.
    """

    def __init__(self, name: str, lock_path: str):
        self.name = str(name or "runtime")
        self.lock_path = Path(lock_path)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._acquired = False
        self._metadata: Dict[str, Any] = {}
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_interval = 0
        self._heartbeat_extra_provider: Optional[Callable[[], Dict[str, Any]]] = None

    @staticmethod
    def _pid_exists(pid: Any) -> Optional[bool]:
        try:
            pid_int = int(pid)
            if pid_int <= 0:
                return None
        except Exception:
            return None
        try:
            os.kill(pid_int, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but belongs to another user.
            return True
        except Exception:
            return None

    def _build_metadata(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
            "cwd": str(Path.cwd()),
        }
        if isinstance(extra, dict):
            payload.update(extra)
        return payload

    def _read_existing_metadata(self) -> Dict[str, Any]:
        try:
            if not self.lock_path.exists():
                return {}
            with open(self.lock_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if not raw:
                return {}
            return json.loads(raw)
        except Exception:
            return {}

    @classmethod
    def inspect(cls, lock_path: str) -> Dict[str, Any]:
        """
        Inspect lock state without holding it.

        Returns:
            Dict containing existence, ownership metadata, and lock status.
        """
        path = Path(lock_path)
        if not path.exists():
            return {
                "path": str(path),
                "exists": False,
                "locked": False,
                "owner": {},
                "owner_alive": None,
                "owner_age_seconds": None,
                "stale": False,
                "checked_at": datetime.now().isoformat(),
            }

        owner: Dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            owner = json.loads(raw) if raw else {}
        except Exception:
            owner = {}

        locked: Optional[bool] = None
        if fcntl is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a+", encoding="utf-8") as fh:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = False
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except BlockingIOError:
                        locked = True
            except Exception:
                locked = None

        owner_pid = owner.get("pid") if isinstance(owner, dict) else None
        owner_alive = cls._pid_exists(owner_pid)
        stale = owner_alive is False and locked is False
        owner_time = None
        owner_age_seconds: Optional[int] = None
        if isinstance(owner, dict):
            owner_time = owner.get("heartbeat_at") or owner.get("started_at")
        if owner_time:
            try:
                owner_age_seconds = max(
                    0,
                    int((datetime.now() - datetime.fromisoformat(str(owner_time))).total_seconds()),
                )
            except Exception:
                owner_age_seconds = None

        return {
            "path": str(path),
            "exists": True,
            "locked": locked,
            "owner": owner if isinstance(owner, dict) else {},
            "owner_alive": owner_alive,
            "owner_age_seconds": owner_age_seconds,
            "stale": stale,
            "checked_at": datetime.now().isoformat(),
        }

    def acquire(self, extra: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Acquire singleton lock.

        Returns:
            (acquired, details). If acquired=False, details describe existing lock owner.
        """
        if self._acquired:
            return True, dict(self._metadata)

        if fcntl is None:
            # Non-POSIX fallback: best-effort "always acquired".
            self._metadata = self._build_metadata(extra)
            return True, dict(self._metadata)

        self._fh = open(self.lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._metadata = self._build_metadata(extra)
            self._fh.seek(0)
            self._fh.truncate(0)
            self._fh.write(json.dumps(self._metadata, ensure_ascii=False, indent=2))
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._acquired = True
            atexit.register(self.release)
            return True, dict(self._metadata)
        except BlockingIOError:
            details = self._read_existing_metadata()
            return False, details
        except Exception:
            # Unknown lock failure: fail closed with details if any.
            details = self._read_existing_metadata()
            return False, details

    def refresh(self, extra: Optional[Dict[str, Any]] = None) -> None:
        """Refresh lock metadata while lock is held."""
        if not self._acquired or not self._fh:
            return
        payload = dict(self._metadata)
        payload.update({"heartbeat_at": datetime.now().isoformat()})
        if isinstance(extra, dict):
            payload.update(extra)
        try:
            self._fh.seek(0)
            self._fh.truncate(0)
            self._fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._metadata = payload
        except Exception:
            return

    def start_heartbeat(
        self,
        interval_seconds: int = 15,
        extra_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> bool:
        """
        Start background heartbeat refresh while lock is held.
        """
        if not self._acquired:
            return False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return True

        self._heartbeat_interval = max(5, int(interval_seconds or 15))
        self._heartbeat_extra_provider = extra_provider
        self._heartbeat_stop.clear()

        def _worker() -> None:
            while not self._heartbeat_stop.is_set():
                extra = None
                if callable(self._heartbeat_extra_provider):
                    try:
                        extra_candidate = self._heartbeat_extra_provider()
                        if isinstance(extra_candidate, dict):
                            extra = extra_candidate
                    except Exception:
                        extra = None
                self.refresh(extra=extra)
                time.sleep(self._heartbeat_interval)

        self._heartbeat_thread = threading.Thread(
            target=_worker,
            name=f"lock_heartbeat_{self.name}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return True

    def stop_heartbeat(self) -> None:
        if not self._heartbeat_thread:
            return
        self._heartbeat_stop.set()
        try:
            self._heartbeat_thread.join(timeout=1.5)
        except Exception:
            pass
        self._heartbeat_thread = None

    def release(self) -> None:
        """Release singleton lock."""
        self.stop_heartbeat()
        if not self._fh:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None
        self._acquired = False
