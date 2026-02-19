#!/usr/bin/env python3
"""
Acceptance gate checks for NEXUS brain release readiness.

Usage:
    python3 scripts/brain_acceptance_gate.py
"""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = PROJECT_ROOT / "data" / "brain" / "action_history.jsonl"


def load_history():
    if not HISTORY_FILE.exists():
        return []
    rows = []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main():
    history = load_history()
    if not history:
        print("ACCEPTANCE: FAIL (no action history)")
        return 1

    recent = history[-200:]
    total = len(recent)
    success = sum(1 for r in recent if r.get("status") == "success")
    failed = sum(1 for r in recent if r.get("status") in {"failed", "timeout"})
    objective_rows = [r for r in recent if "objective_success" in r]
    objective_base = objective_rows if objective_rows else recent
    objective_success = sum(
        1
        for r in objective_base
        if r.get("objective_success", r.get("status") == "success")
    )
    policy_blocked = sum(1 for r in recent if r.get("policy_blocked", False))

    objective_success_rate = objective_success / len(objective_base)
    failure_rate = failed / total
    policy_block_rate = policy_blocked / total

    print(f"SAMPLE_SIZE={total}")
    print(f"OBJECTIVE_SAMPLE_SIZE={len(objective_base)}")
    print(f"SUCCESS_RATE={success/total:.4f}")
    print(f"OBJECTIVE_SUCCESS_RATE={objective_success_rate:.4f}")
    print(f"FAILURE_RATE={failure_rate:.4f}")
    print(f"POLICY_BLOCK_RATE={policy_block_rate:.4f}")

    checks = [
        ("OBJECTIVE_SUCCESS_RATE>=0.60", objective_success_rate >= 0.60),
        ("FAILURE_RATE<=0.30", failure_rate <= 0.30),
        ("POLICY_BLOCK_RATE<=0.20", policy_block_rate <= 0.20),
    ]

    failed_checks = [name for name, ok in checks if not ok]
    if failed_checks:
        print("ACCEPTANCE: FAIL")
        for name in failed_checks:
            print(f"- {name}")
        return 1

    print("ACCEPTANCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
