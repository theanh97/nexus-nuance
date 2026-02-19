"""
Auto Dev Loop - Main Orchestrator
Infinite improvement loop for autonomous app development
"""

import os
import sys
import json
import asyncio
import argparse
from typing import Dict
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.orchestrator import GLMOrchestrator, Task, Feedback
from agents.vision_analyzer import VisionAnalyzer, UIAnalysis
from agents.browser_controller import BrowserController, LocalAppServer, AppRunner


class AutoDevLoop:
    """
    The main autonomous development loop

    Flow:
    1. GLM 5.0 analyzes requirements
    2. GLM generates code
    3. App runs locally
    4. Browser captures screenshot
    5. Vision models analyze UI/UX
    6. Feedback sent to GLM
    7. GLM improves code
    8. Loop continues until perfect
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.orchestrator = GLMOrchestrator(config_path)
        self.vision_analyzer = VisionAnalyzer(primary="claude")
        self.browser = BrowserController(
            headless=False,
            screenshot_dir="screenshots"
        )
        self.server = LocalAppServer(port=3000, app_dir="app-output")
        self.runner = None

        self.iteration = 0
        self.max_iterations = 50
        self.target_score = 8.5
        self.history = []

    async def initialize(self):
        """Initialize all components"""
        print("=" * 60)
        print("  AUTO DEV LOOP - Initializing")
        print("=" * 60)

        await self.browser.start()
        self.runner = AppRunner(self.browser, self.server)

        print("\n[OK] GLM 5.0 Orchestrator ready")
        print("[OK] Vision Analyzer ready (Claude + Gemini)")
        print("[OK] Browser Controller ready")
        print()

    async def shutdown(self):
        """Cleanup"""
        await self.browser.stop()
        self.server.stop()
        print("\n[OK] Shutdown complete")

    async def run_development_cycle(self, app_description: str) -> Dict:
        """
        Run one complete development cycle
        """
        self.iteration += 1
        print(f"\n{'='*60}")
        print(f"  ITERATION #{self.iteration}")
        print(f"{'='*60}")

        cycle_result = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "steps": []
        }

        # Step 1: GLM Analyzes
        print("\n[1/6] GLM 5.0 analyzing requirements...")
        task = self.orchestrator.analyze_task(app_description)
        cycle_result["steps"].append({"step": "analyze", "status": "done"})
        print(f"      Task ID: {task.id}")

        # Step 2: GLM Generates Code
        print("\n[2/6] GLM 5.0 generating code...")
        code = self.orchestrator.generate_code(task, self.orchestrator.context)
        self._save_code(code)
        cycle_result["steps"].append({"step": "generate", "status": "done"})

        # Step 3: Run App
        print("\n[3/6] Starting app...")
        await asyncio.sleep(1)  # Wait for files to be saved
        try:
            url = await self.runner.start_app()
            print(f"      App running at: {url}")
            cycle_result["steps"].append({"step": "run", "status": "done", "url": url})
        except Exception as e:
            print(f"      Error: {e}")
            cycle_result["steps"].append({"step": "run", "status": "error", "error": str(e)})

        # Step 4: Capture Screenshot
        print("\n[4/6] Capturing screenshot...")
        await asyncio.sleep(2)  # Wait for app to load
        screenshot_path = await self.browser.screenshot(f"iteration_{self.iteration}.png")
        cycle_result["steps"].append({"step": "screenshot", "status": "done", "path": screenshot_path})
        print(f"      Saved: {screenshot_path}")

        # Step 5: Vision Analysis
        print("\n[5/6] Vision models analyzing UI/UX...")
        try:
            analysis = self.vision_analyzer.analyze(
                screenshot_path,
                context=app_description,
                use_all=True  # Use both Claude and Gemini
            )
            cycle_result["steps"].append({
                "step": "analyze_ui",
                "status": "done",
                "analysis": {
                    k: v.__dict__ if isinstance(v, UIAnalysis) else v
                    for k, v in analysis.items()
                }
            })

            # Get combined score
            combined = analysis.get("combined", {})
            score = combined.get("score", 0)
            issues = combined.get("all_issues", [])
            suggestions = combined.get("all_suggestions", [])

            print(f"      Score: {score:.1f}/10")
            print(f"      Issues found: {len(issues)}")
            print(f"      Suggestions: {len(suggestions)}")

        except Exception as e:
            print(f"      Error: {e}")
            score = 0
            issues = []
            suggestions = []

        # Step 6: Create Feedback for Next Iteration
        print("\n[6/6] Creating improvement feedback...")
        if score > 0:
            feedback = Feedback(
                source="vision_models",
                score=score,
                issues=issues,
                suggestions=suggestions,
                screenshot_path=screenshot_path
            )
            self.orchestrator.incorporate_feedback(feedback)

        cycle_result["score"] = score
        cycle_result["should_continue"] = score < self.target_score

        self.history.append(cycle_result)
        self._save_history()

        return cycle_result

    def _save_code(self, code_response: str):
        """Save generated code to files"""
        try:
            # Try to parse as JSON
            if code_response.startswith("{"):
                code_data = json.loads(code_response)

                for file_info in code_data.get("files", []):
                    path = Path("app-output") / file_info["path"]
                    path.parent.mkdir(parents=True, exist_ok=True)

                    with open(path, "w", encoding="utf-8") as f:
                        f.write(file_info["content"])

                    print(f"      Created: {path}")
            else:
                # Save as single HTML file
                path = Path("app-output/index.html")
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code_response)
                print(f"      Created: {path}")

        except json.JSONDecodeError:
            # Save raw response
            path = Path("app-output/index.html")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(code_response)
            print(f"      Created: {path}")

    def _save_history(self):
        """Save iteration history"""
        path = Path("logs/history.json")
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, default=str)

    def should_continue(self) -> bool:
        """Check if loop should continue"""
        if self.iteration >= self.max_iterations:
            print(f"\n[STOP] Max iterations ({self.max_iterations}) reached")
            return False

        if self.history:
            latest_score = self.history[-1].get("score", 0)
            if latest_score >= self.target_score:
                print(f"\n[STOP] Target score ({self.target_score}) achieved!")
                return False

        return True

    async def run(self, app_description: str):
        """
        Run the infinite improvement loop
        """
        print(f"\nApp Description: {app_description}")
        print(f"Target Score: {self.target_score}/10")
        print(f"Max Iterations: {self.max_iterations}")

        await self.initialize()

        try:
            while self.should_continue():
                result = await self.run_development_cycle(app_description)

                if not result.get("should_continue", True):
                    break

                # Wait before next iteration
                print(f"\n[INFO] Waiting 3 seconds before next iteration...")
                await asyncio.sleep(3)

        except KeyboardInterrupt:
            print("\n[INTERRUPT] User stopped the loop")

        finally:
            await self.shutdown()

        # Print summary
        self._print_summary()

    def _print_summary(self):
        """Print final summary"""
        print("\n" + "=" * 60)
        print("  SUMMARY")
        print("=" * 60)

        if self.history:
            scores = [h.get("score", 0) for h in self.history]
            print(f"  Total Iterations: {len(self.history)}")
            print(f"  Best Score: {max(scores):.1f}/10")
            print(f"  Final Score: {scores[-1]:.1f}/10")
            print(f"  Improvement: {scores[-1] - scores[0]:.1f}" if len(scores) > 1 else "")
        else:
            print("  No iterations completed")

        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Auto Dev Loop")
    parser.add_argument(
        "--app",
        type=str,
        default="A modern todo list app with dark mode",
        help="App description"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=50,
        help="Maximum iterations"
    )
    parser.add_argument(
        "--target-score",
        type=float,
        default=8.5,
        help="Target score (0-10)"
    )

    args = parser.parse_args()

    loop = AutoDevLoop()
    loop.max_iterations = args.max_iterations
    loop.target_score = args.target_score

    await loop.run(args.app)


if __name__ == "__main__":
    asyncio.run(main())
