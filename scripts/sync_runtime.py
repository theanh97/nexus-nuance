#!/usr/bin/env python3
"""
Synchronize provider/runtime routing configuration and print a concise report.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.runtime_sync import run_full_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Runtime provider/model sync")
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Append missing synchronized values to .env",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/state/runtime_sync.json",
        help="Path to write sync snapshot JSON",
    )
    args = parser.parse_args()

    load_dotenv()
    report = run_full_sync(write_env=args.write_env, output_path=args.output)

    status = report["provider_status"]
    print("=" * 68)
    print("RUNTIME SYNC")
    print("=" * 68)
    print(
        "providers:",
        f"GLM={'on' if status['glm_api'] else 'off'}",
        f"Gemini={'on' if status['gemini_api'] else 'off'}",
        f"MiniMax={'on' if status['minimax_api'] else 'off'}",
    )
    print(
        "subscription:",
        f"Codex={'on' if status['codex_subscription'] else 'off'}",
        f"Claude={'on' if status['claude_subscription'] else 'off'}",
        f"GeminiCLI={'on' if status.get('gemini_subscription') else 'off'}",
    )

    sync_actions = report["provider_sync"]["actions"]
    if sync_actions:
        print("synced:")
        for action in sync_actions:
            print(f"  - {action}")

    print("routing:")
    for row in report["routing_matrix"]:
        runtime_chain = " -> ".join(row["runtime_chain"]) if row["runtime_chain"] else "none"
        full_chain = " -> ".join(row["chain"]) if row["chain"] else "none"
        if full_chain != runtime_chain:
            print(f"  - {row['label']}: runtime={runtime_chain} | policy={full_chain}")
        else:
            print(f"  - {row['label']}: {runtime_chain}")

    budget = report["usage_summary"]
    print(
        "budget:",
        f"cost=${budget['total_cost_usd']}",
        f"limit=${budget['daily_budget_usd']}",
        f"ratio={budget['budget_ratio']:.2f}",
    )
    print(f"snapshot: {args.output}")


if __name__ == "__main__":
    main()
