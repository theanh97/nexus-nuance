#!/usr/bin/env python3
"""
Runtime lock diagnostics and safe stale-lock cleanup.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Dict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.runtime_guard import ProcessSingleton


def _lock_paths() -> Dict[str, str]:
    return {
        "learning_runtime": os.getenv("LEARNING_RUNTIME_LOCK_PATH", "data/state/learning_runtime.lock"),
        "autodev_runtime": os.getenv("AUTODEV_RUNTIME_LOCK_PATH", "data/state/autodev_runtime.lock"),
        "knowledge_scan_op": os.getenv("SCAN_OPERATION_LOCK_PATH", "data/state/knowledge_scan.lock"),
        "improvement_apply_op": os.getenv("IMPROVEMENT_OPERATION_LOCK_PATH", "data/state/improvement_apply.lock"),
        "daily_cycle_op": os.getenv("DAILY_CYCLE_OPERATION_LOCK_PATH", "data/state/daily_self_learning.lock"),
    }


def _print_snapshot(name: str, snapshot: Dict) -> None:
    owner = snapshot.get("owner", {}) if isinstance(snapshot.get("owner"), dict) else {}
    print(
        f"{name:20} "
        f"locked={snapshot.get('locked')} "
        f"owner_pid={owner.get('pid', 'N/A')} "
        f"owner_alive={snapshot.get('owner_alive')} "
        f"owner_age_s={snapshot.get('owner_age_seconds')} "
        f"stale={snapshot.get('stale')} "
        f"path={snapshot.get('path')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Runtime and operation lock diagnostics")
    parser.add_argument(
        "--cleanup-stale",
        action="store_true",
        help="Remove stale lock files only when not actively locked",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("RUNTIME LOCK STATUS")
    print("=" * 72)

    removed = []
    for name, path in _lock_paths().items():
        snapshot = ProcessSingleton.inspect(path)
        _print_snapshot(name, snapshot)

        if not args.cleanup_stale:
            continue
        if not snapshot.get("stale"):
            continue
        if snapshot.get("locked"):
            continue

        lock_file = Path(path)
        try:
            if lock_file.exists():
                lock_file.unlink()
                removed.append(path)
        except Exception as exc:
            print(f"failed_remove {path}: {exc}")

    if args.cleanup_stale:
        if removed:
            print("-" * 72)
            for path in removed:
                print(f"removed_stale_lock {path}")
        else:
            print("-" * 72)
            print("no_stale_locks_removed")


if __name__ == "__main__":
    main()
