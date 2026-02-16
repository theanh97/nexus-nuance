"""
Auto Dev Loop - Main Entry Point
Infinite autonomous development loop

Usage:
    python main.py                    # Start with default goal
    python main.py --goal "..."       # Start with custom goal
    python main.py --status           # Check status
    python main.py --shutdown         # Shutdown running instance
"""

import os
import sys
import asyncio
import argparse
import signal
from pathlib import Path
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.agents.orion import Orion
from src.agents.nova import Nova
from src.agents.pixel import Pixel
from src.agents.cipher import Cipher
from src.agents.echo import Echo
from src.agents.flux import Flux
from src.core.message import AgentMessage, MessageType
from src.core.runtime_sync import run_full_sync
from src.core.runtime_guard import ProcessSingleton


class AutoDevLoop:
    """
    Main autonomous development loop

    The system runs 24/7, continuously improving the project
    Only stops on SHUTDOWN command
    """

    def __init__(self):
        self.orion = None
        self.running = False

    async def initialize(self):
        """Initialize all agents"""
        run_full_sync(write_env=False)

        print("=" * 70)
        print("  AUTO DEV LOOP - Initializing The Dream Team")
        print("=" * 70)
        print()

        # Create agents
        nova = Nova()
        pixel = Pixel()
        cipher = Cipher()
        echo = Echo()
        flux = Flux()

        # Create Orion (the orchestrator)
        self.orion = Orion()

        # Register agents with Orion
        self.orion.register_agent(nova)
        self.orion.register_agent(pixel)
        self.orion.register_agent(cipher)
        self.orion.register_agent(echo)
        self.orion.register_agent(flux)

        print()
        print("┌─────────────────────────────────────────────────────────────────┐")
        print("│  THE DREAM TEAM - Ready for Action                              │")
        print("├─────────────────────────────────────────────────────────────────┤")
        print("│  🌟 ORION  - Supreme PM (GLM-5)                                 │")
        print("│  💻 NOVA   - Code Architect (GLM-5)                             │")
        print("│  🎨 PIXEL  - UI/UX Visionary (GLM-4.6V)                         │")
        print("│  🔐 CIPHER - Security Master (GLM-5)                            │")
        print("│  🧪 ECHO   - QA Engineer (Gemini Flash)                         │")
        print("│  🚀 FLUX   - DevOps (GLM-5)                                     │")
        print("└─────────────────────────────────────────────────────────────────┘")
        print()

        # Check API keys
        self._check_api_keys()

        # Start all agents
        await self.orion.start()

        print("✅ All systems ready!")
        print()

    def _check_api_keys(self):
        """Check if API keys are configured"""

        keys = {
            "GLM_API_KEY": os.getenv("GLM_API_KEY"),
            "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY")
        }

        print("📋 API Configuration:")
        for key, value in keys.items():
            status = "✅ Configured" if value else "❌ Missing"
            print(f"   {key}: {status}")

        if not keys["GLM_API_KEY"]:
            print()
            print("⚠️ WARNING: GLM_API_KEY is required for Orion, Nova, Cipher, Flux")
            print("   Get your key from: https://open.bigmodel.cn/")

        print()

    async def start(self, goal: str = None):
        """Start the infinite improvement loop"""

        if not self.orion:
            await self.initialize()

        # Default goal if not provided
        if not goal:
            goal = "Build a modern, beautiful, and functional web application with dark mode, responsive design, and smooth animations"

        print("=" * 70)
        print("  STARTING INFINITE IMPROVEMENT LOOP")
        print("=" * 70)
        print()
        print(f"  Project Goal: {goal}")
        print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        print("  The system will run autonomously until you press Ctrl+C or send SHUTDOWN")
        print()
        print("-" * 70)

        self.running = True

        # Set up signal handlers
        def handle_shutdown(signum, frame):
            print("\n\n🛑 Shutdown signal received...")
            self.running = False

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        # Initialize project context
        from src.core.message import ProjectContext
        self.orion.context = ProjectContext(
            project_name="AutoDevProject",
            project_goal=goal
        )

        # Run the infinite loop
        try:
            await self.orion.run_infinite_loop()
        except Exception as e:
            print(f"\n❌ Error in main loop: {str(e)}")

        # Cleanup
        await self.shutdown()

    async def shutdown(self):
        """Shutdown the system"""

        print()
        print("=" * 70)
        print("  SHUTDOWN")
        print("=" * 70)

        if self.orion:
            await self.orion.stop()

        print()
        print("📊 Summary:")

        if self.orion and self.orion.history:
            scores = [h.get("score", 0) for h in self.orion.history]
            print(f"   Total Iterations: {len(self.orion.history)}")
            print(f"   Best Score: {max(scores):.1f}/10")
            print(f"   Final Score: {scores[-1]:.1f}/10")

        print()
        print("✅ Shutdown complete. Goodbye!")
        print()

    async def status(self):
        """Check system status"""

        print("=" * 70)
        print("  AUTO DEV LOOP - Status")
        print("=" * 70)

        if not self.orion:
            print("   Status: Not initialized")
            return

        status = self.orion._get_status()

        print(f"   Running: {status['running']}")
        print(f"   Paused: {status['paused']}")
        print(f"   Iteration: {status['iteration']}")
        print(f"   Target Score: {status['target_score']}")

        if status.get('context'):
            print(f"   Project: {status['context'].get('project_name', 'Unknown')}")
            print(f"   Goal: {status['context'].get('project_goal', 'Not set')}")

        print()


async def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(
        description="Auto Dev Loop - Autonomous Development System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                              # Start with default goal
    python main.py --goal "Build a todo app"    # Start with custom goal
    python main.py --max-iterations 100         # Limit iterations
    python main.py --target-score 9.0           # Set target score
"""
    )

    parser.add_argument(
        "--goal", "-g",
        type=str,
        default=None,
        help="Project goal/description"
    )

    parser.add_argument(
        "--max-iterations", "-m",
        type=int,
        default=10000,
        help="Maximum iterations (default: 10000 for near-infinite)"
    )

    parser.add_argument(
        "--target-score", "-t",
        type=float,
        default=9.0,
        help="Target score to achieve (0-10, default: 9.0)"
    )

    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Check system status"
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode (single iteration)"
    )

    args = parser.parse_args()

    # Create the loop
    loop = AutoDevLoop()

    if args.status:
        await loop.status()
        return

    lock_path = os.getenv("AUTODEV_RUNTIME_LOCK_PATH", "data/state/autodev_runtime.lock")
    runtime_guard = ProcessSingleton(name="autodev_runtime", lock_path=lock_path)
    acquired, owner = runtime_guard.acquire(extra={"entrypoint": "main.py"})
    if not acquired:
        owner_pid = owner.get("pid", "unknown") if isinstance(owner, dict) else "unknown"
        owner_started = owner.get("started_at", "unknown") if isinstance(owner, dict) else "unknown"
        print("⚠️ Another AutoDev runtime is already running.")
        print(f"   Lock: {lock_path}")
        print(f"   Owner PID: {owner_pid}")
        print(f"   Started: {owner_started}")
        raise SystemExit(1)
    runtime_guard.start_heartbeat(interval_seconds=15)

    # Initialize
    await loop.initialize()

    # Configure
    loop.orion.max_iterations = args.max_iterations
    loop.orion.target_score = args.target_score

    if args.demo:
        loop.orion.max_iterations = 1

    # Start
    await loop.start(args.goal)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user. Goodbye!")
