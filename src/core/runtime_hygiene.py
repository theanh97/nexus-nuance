"""
Runtime hygiene helpers for process/lock observability and conflict remediation.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from .runtime_guard import ProcessSingleton


_SHELL_NAMES = {"zsh", "bash", "fish"}


def _parse_elapsed_seconds(raw: str) -> int:
    value = str(raw or "").strip()
    if not value:
        return 0
    days = 0
    if "-" in value:
        day_part, value = value.split("-", 1)
        try:
            days = int(day_part)
        except Exception:
            days = 0
    parts = value.split(":")
    try:
        nums = [int(p) for p in parts]
    except Exception:
        return 0
    if len(nums) == 3:
        hours, minutes, seconds = nums
    elif len(nums) == 2:
        hours = 0
        minutes, seconds = nums
    else:
        return 0
    return max(0, days * 86400 + hours * 3600 + minutes * 60 + seconds)


def list_processes() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        output = subprocess.check_output(
            ["ps", "-Ao", "pid,ppid,tty,stat,etime,command"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return rows

    for line in output.splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid_raw, ppid_raw, tty, stat, etime, command = parts
        try:
            pid = int(pid_raw)
            ppid = int(ppid_raw)
        except Exception:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "tty": tty,
                "stat": stat,
                "etime": etime,
                "elapsed_seconds": _parse_elapsed_seconds(etime),
                "command": command.strip(),
            }
        )
    return rows


def collect_runtime_processes(workspace_hint: str = "") -> List[Dict[str, Any]]:
    workspace_lc = str(workspace_hint or "").strip().lower()
    results: List[Dict[str, Any]] = []
    current_pid = os.getpid()
    for row in list_processes():
        if int(row.get("pid", 0)) == current_pid:
            continue
        cmd = str(row.get("command", "")).strip()
        cmd_lc = cmd.lower()
        kind = ""
        if "scripts/start.py" in cmd_lc:
            if "--status" in cmd_lc or "--scan" in cmd_lc:
                continue
            kind = "learning_runtime"
        elif "run_system.py" in cmd_lc:
            kind = "autodev_runtime"
        elif "scripts/brain_daemon.py" in cmd_lc:
            kind = "brain_daemon"
        elif "autonomous_loop.py" in cmd_lc:
            kind = "autonomous_loop"
        elif "monitor/app.py" in cmd_lc:
            kind = "monitor_api"
        elif "openclaw-gateway" in cmd_lc:
            kind = "openclaw_gateway"

        if not kind and workspace_lc and workspace_lc in cmd_lc:
            kind = "workspace_process"

        if not kind:
            continue

        item = dict(row)
        item["kind"] = kind
        results.append(item)
    return sorted(results, key=lambda x: (str(x.get("kind")), -int(x.get("elapsed_seconds", 0))))


def collect_terminal_shells() -> List[Dict[str, Any]]:
    rows = list_processes()
    children: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        children.setdefault(int(row.get("ppid", 0)), []).append(row)

    shells: List[Dict[str, Any]] = []
    for row in rows:
        tty = str(row.get("tty", ""))
        if tty == "??":
            continue
        cmd = str(row.get("command", "")).strip()
        binary = Path(cmd.split(" ")[0]).name.lower() if cmd else ""
        if binary not in _SHELL_NAMES:
            continue
        child_rows = children.get(int(row.get("pid", 0)), [])
        child_cmds = [str(c.get("command", "")).strip() for c in child_rows][:4]
        shells.append(
            {
                "pid": int(row.get("pid", 0)),
                "ppid": int(row.get("ppid", 0)),
                "tty": tty,
                "stat": str(row.get("stat", "")),
                "etime": str(row.get("etime", "")),
                "child_count": len(child_rows),
                "children": child_cmds,
                "idle": len(child_rows) == 0,
                "command": cmd,
            }
        )
    return sorted(shells, key=lambda x: str(x.get("tty")))


def detect_runtime_conflicts(runtime_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    singleton_kinds = {"learning_runtime", "autodev_runtime", "brain_daemon", "autonomous_loop", "monitor_api"}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in runtime_rows:
        kind = str(row.get("kind", ""))
        grouped.setdefault(kind, []).append(row)

    conflicts: List[Dict[str, Any]] = []
    for kind, rows in grouped.items():
        if kind not in singleton_kinds:
            continue
        if len(rows) <= 1:
            continue
        ranked = sorted(rows, key=lambda x: int(x.get("elapsed_seconds", 0)), reverse=True)
        keep = ranked[0]
        terminate = ranked[1:]
        conflicts.append(
            {
                "kind": kind,
                "count": len(rows),
                "keep_pid": int(keep.get("pid", 0)),
                "terminate_pids": [int(x.get("pid", 0)) for x in terminate if int(x.get("pid", 0)) > 0],
            }
        )
    return conflicts


def cleanup_stale_locks(lock_paths: List[str]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for raw_path in lock_paths:
        lock_path = str(raw_path or "").strip()
        if not lock_path:
            continue
        snap = ProcessSingleton.inspect(lock_path)
        removed = False
        error = ""
        if bool(snap.get("exists", False)) and bool(snap.get("stale", False)) and snap.get("locked") is False:
            try:
                Path(lock_path).unlink(missing_ok=True)
                removed = True
            except Exception as exc:
                error = str(exc)
        results.append(
            {
                "path": lock_path,
                "exists": bool(snap.get("exists", False)),
                "locked": snap.get("locked"),
                "stale": bool(snap.get("stale", False)),
                "owner_pid": (snap.get("owner", {}) or {}).get("pid"),
                "removed": removed,
                "error": error,
            }
        )
    return results


def terminate_processes(pids: List[int], grace_seconds: float = 2.5) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    current_pid = os.getpid()
    unique_pids = sorted({int(pid) for pid in pids if int(pid) > 0 and int(pid) != current_pid})
    for pid in unique_pids:
        outcome = {"pid": pid, "terminated": False, "signal": "", "error": ""}
        try:
            os.kill(pid, signal.SIGTERM)
            outcome["signal"] = "SIGTERM"
        except ProcessLookupError:
            outcome["terminated"] = True
            outcome["signal"] = "NOT_FOUND"
            results.append(outcome)
            continue
        except Exception as exc:
            outcome["error"] = str(exc)
            results.append(outcome)
            continue

        deadline = time.time() + max(0.5, float(grace_seconds))
        alive = True
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.12)
            except ProcessLookupError:
                alive = False
                break
            except Exception:
                break

        if alive:
            try:
                os.kill(pid, signal.SIGKILL)
                outcome["signal"] = "SIGKILL"
                outcome["terminated"] = True
            except ProcessLookupError:
                outcome["terminated"] = True
            except Exception as exc:
                outcome["error"] = str(exc)
        else:
            outcome["terminated"] = True
        results.append(outcome)
    return results
