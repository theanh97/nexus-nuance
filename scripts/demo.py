#!/usr/bin/env python3
"""
Demo Script - Test the system without API calls
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


async def demo_browser():
    """Demo browser control"""
    from agents.browser_controller import BrowserController

    print("\n" + "="*50)
    print("  DEMO: Browser Controller")
    print("="*50)

    browser = BrowserController(headless=False, screenshot_dir="../screenshots")

    try:
        await browser.start()
        print("[OK] Browser started")

        # Navigate to example
        await browser.navigate("https://example.com")
        print("[OK] Navigated to example.com")

        # Take screenshot
        path = await browser.screenshot("demo_screenshot.png")
        print(f"[OK] Screenshot saved: {path}")

        # Wait a bit
        await asyncio.sleep(2)

    finally:
        await browser.stop()
        print("[OK] Browser stopped")


def demo_orchestrator():
    """Demo orchestrator without API calls"""
    print("\n" + "="*50)
    print("  DEMO: GLM Orchestrator (Mock)")
    print("="*50)

    # Mock task analysis
    task = {
        "id": f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "description": "A beautiful weather dashboard",
        "status": "analyzed",
        "subtasks": [
            {"id": "s1", "description": "Create HTML structure", "priority": 1},
            {"id": "s2", "description": "Add CSS styling", "priority": 2},
            {"id": "s3", "description": "Implement JavaScript logic", "priority": 3},
        ]
    }

    print(f"[OK] Task created: {task['id']}")
    print(f"     Subtasks: {len(task['subtasks'])}")

    # Mock feedback
    feedback = {
        "source": "claude",
        "score": 7.5,
        "issues": [
            "Color contrast could be improved",
            "Buttons need more padding"
        ],
        "suggestions": [
            "Use darker text for better readability",
            "Add hover effects to buttons"
        ]
    }

    print(f"\n[OK] Mock feedback received")
    print(f"     Score: {feedback['score']}/10")
    print(f"     Issues: {len(feedback['issues'])}")
    print(f"     Suggestions: {len(feedback['suggestions'])}")

    return task, feedback


def demo_vision_analyzer():
    """Demo vision analyzer (mock)"""
    print("\n" + "="*50)
    print("  DEMO: Vision Analyzer (Mock)")
    print("="*50)

    # Mock UI analysis
    analysis = {
        "overall_score": 7.2,
        "layout_score": 8.0,
        "color_score": 6.5,
        "typography_score": 7.5,
        "usability_issues": [
            "Navigation is not clearly visible",
            "Form labels could be more descriptive"
        ],
        "design_suggestions": [
            "Add a sticky navigation bar",
            "Use consistent spacing throughout"
        ],
        "positive_aspects": [
            "Clean and minimal design",
            "Good use of whitespace"
        ]
    }

    print(f"[OK] UI Analysis complete")
    print(f"     Overall: {analysis['overall_score']}/10")
    print(f"     Layout: {analysis['layout_score']}/10")
    print(f"     Color: {analysis['color_score']}/10")
    print(f"     Typography: {analysis['typography_score']}/10")

    return analysis


def demo_full_loop():
    """Demo the full loop without API calls"""
    print("\n" + "="*60)
    print("  DEMO: Full Auto Dev Loop (Mock)")
    print("="*60)

    print("\nApp: 'A modern todo list application'")
    print("Target Score: 8.0/10")
    print("Max Iterations: 5")

    iterations = []

    for i in range(1, 4):
        print(f"\n{'='*40}")
        print(f"  ITERATION #{i}")
        print(f"{'='*40}")

        # Mock scores improving over iterations
        scores = [5.5, 7.0, 8.2]

        print(f"\n[1/6] Analyzing requirements...")
        print(f"[2/6] Generating code...")
        print(f"[3/6] Running app...")
        print(f"[4/6] Capturing screenshot...")
        print(f"[5/6] Analyzing UI/UX...")
        print(f"      Score: {scores[i-1]}/10")
        print(f"[6/6] Creating feedback...")

        iterations.append({
            "iteration": i,
            "score": scores[i-1]
        })

        if scores[i-1] >= 8.0:
            print(f"\n[STOP] Target score achieved!")
            break

    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)
    print(f"  Total Iterations: {len(iterations)}")
    print(f"  Final Score: {iterations[-1]['score']}/10")
    print("="*60)


def check_environment():
    """Check if environment is set up"""
    print("\n" + "="*50)
    print("  Environment Check")
    print("="*50)

    checks = []

    # Python version
    py_version = sys.version_info
    checks.append(("Python 3.8+", py_version >= (3, 8)))

    # API Keys
    checks.append(("GLM_API_KEY", bool(os.getenv("GLM_API_KEY"))))
    checks.append(("ANTHROPIC_API_KEY", bool(os.getenv("ANTHROPIC_API_KEY"))))
    checks.append(("GOOGLE_API_KEY", bool(os.getenv("GOOGLE_API_KEY"))))

    # Dependencies
    try:
        import yaml
        checks.append(("PyYAML", True))
    except ImportError:
        checks.append(("PyYAML", False))

    try:
        import requests
        checks.append(("requests", True))
    except ImportError:
        checks.append(("requests", False))

    # Print results
    for name, status in checks:
        status_str = "✅" if status else "❌"
        print(f"  {status_str} {name}")

    return all(c[1] for c in checks)


def main():
    print("""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║          AUTO DEV LOOP - Demo Mode                        ║
║                                                           ║
║  This demo runs without real API calls                    ║
║  to show how the system works.                            ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """)

    # Check environment
    check_environment()

    # Run demos
    print("\nRunning demos...\n")

    demo_orchestrator()
    demo_vision_analyzer()
    demo_full_loop()

    # Optional: browser demo (requires playwright)
    run_browser = input("\nRun browser demo? (requires playwright) [y/N]: ")
    if run_browser.lower() == 'y':
        asyncio.run(demo_browser())

    print("\n✅ Demo complete!")
    print("\nTo run the real system:")
    print("  1. Set API keys: GLM_API_KEY, ANTHROPIC_API_KEY")
    print("  2. Run: ./run.sh")
    print("  Or: python3 scripts/main_loop.py --app 'Your app description'")


if __name__ == "__main__":
    main()
