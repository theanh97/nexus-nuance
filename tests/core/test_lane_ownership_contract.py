import os
import subprocess

import pytest


DISALLOWED_PATH_PREFIXES = (
    "src/brain/",
    "scripts/brain_daemon.py",
    "src/api/brain_api.py",
    "src/loop/autonomous_loop.py",
)


def _changed_files(base_ref: str):
    cmd = ["git", "diff", "--name-only", f"{base_ref}...HEAD"]
    output = subprocess.check_output(cmd, text=True).strip()
    return [line.strip() for line in output.splitlines() if line.strip()]


def test_lane_b_no_overlap_diff_contract():
    """Optional CI gate: ensure Lane B branch does not modify Lane A files."""
    base_ref = os.getenv("LANE_B_DIFF_BASE", "").strip()
    if not base_ref:
        pytest.skip("LANE_B_DIFF_BASE is not set; skipping diff ownership contract check")

    changed = _changed_files(base_ref)
    violations = []
    for path in changed:
        if path.startswith(DISALLOWED_PATH_PREFIXES):
            violations.append(path)

    assert not violations, f"Lane B ownership violation files: {violations}"
