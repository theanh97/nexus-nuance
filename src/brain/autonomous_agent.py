"""
AUTONOMOUS AGENT WITH REAL EXECUTION
=====================================
ReAct Agent tích hợp Real Action Executor

CAPABILITIES:
- Thought → Action → Observation với REAL execution
- Có thể: đọc/ghi files, chạy code, control browser, call APIs
- Self-reflection sau mỗi task
- Tự động learn từ kết quả
"""

import json
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Import real action executor
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from brain.action_executor import ActionExecutor, ActionStatus, execute_action
from brain.react_agent import ReActAgent, AgentState, Thought, Action, Observation, Reflection


class AutonomousAgent(ReActAgent):
    """
    ReAct Agent với Real Action Execution
    """

    def __init__(self, brain=None):
        super().__init__(brain)

        # Real action executor
        self.executor = ActionExecutor()

        # Override tools with real implementations
        self.tools = {
            "read_file": self._real_read_file,
            "write_file": self._real_write_file,
            "edit_file": self._real_edit_file,
            "run_code": self._real_run_code,
            "run_shell": self._real_run_shell,
            "analyze": self._real_analyze,
            "search": self._real_search,
            "navigate": self._real_navigate,
            "screenshot": self._real_screenshot,
            "git": self._real_git,
            "learn": self._real_learn,
            "test": self._real_test,
        }

    # ==================== REAL TOOL IMPLEMENTATIONS ====================

    def _real_read_file(self, path: str, **kwargs) -> str:
        """Really read a file"""
        result = self.executor.execute("read_file", {"path": path})
        if result.status == ActionStatus.SUCCESS:
            return f"SUCCESS: Read {result.data.get('lines', 0)} lines from {path}\n\n{result.output[:2000]}"
        return f"Error: {result.error}"

    def _real_write_file(self, path: str, content: str = "", **kwargs) -> str:
        """Really write a file"""
        result = self.executor.execute("write_file", {"path": path, "content": content})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_edit_file(self, path: str, old: str = "", new: str = "", **kwargs) -> str:
        """Really edit a file"""
        result = self.executor.execute("edit_file", {"path": path, "old": old, "new": new})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_run_code(self, code: str = "", file: str = "", **kwargs) -> str:
        """Really execute Python code"""
        result = self.executor.execute("run_python", {"code": code, "file": file})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_run_shell(self, command: str = "", **kwargs) -> str:
        """Really execute shell command"""
        result = self.executor.execute("run_shell", {"command": command})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_analyze(self, path: str = "", **kwargs) -> str:
        """Really analyze code"""
        result = self.executor.execute("analyze_code", {"path": path})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_search(self, query: str = "", **kwargs) -> str:
        """Search knowledge base"""
        result = self.executor.execute("query_knowledge", {"query": query})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_navigate(self, url: str = "", **kwargs) -> str:
        """Navigate to URL"""
        result = self.executor.execute("navigate_url", {"url": url})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_screenshot(self, url: str = "", **kwargs) -> str:
        """Take screenshot"""
        result = self.executor.execute("take_screenshot", {"url": url})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_git(self, operation: str = "status", **kwargs) -> str:
        """Git operations"""
        if operation == "status":
            result = self.executor.execute("git_status", {})
        elif operation == "commit":
            result = self.executor.execute("git_commit", {"message": kwargs.get("message", "Auto commit")})
        else:
            return f"Unknown git operation: {operation}"

        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_learn(self, content: str = "", source: str = "agent", **kwargs) -> str:
        """Learn new knowledge"""
        result = self.executor.execute("learn_knowledge", {"content": content, "source": source})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    def _real_test(self, path: str = "tests/", **kwargs) -> str:
        """Run tests"""
        result = self.executor.execute("run_tests", {"path": path})
        if result.status == ActionStatus.SUCCESS:
            return result.output
        return f"Error: {result.error}"

    # ==================== OBSERVATION OVERRIDE ====================

    def _extract_insights(self, result: str) -> List[str]:
        """Extract insights from observation - improved"""
        insights = []
        result_lower = result.lower()

        if "success" in result_lower:
            insights.append("Action completed successfully")
        if "found" in result_lower:
            insights.append("Information was found")
        if "created" in result_lower or "written" in result_lower:
            insights.append("Something was created")
        if "error" in result_lower and "success" not in result_lower:
            insights.append("An error occurred")

        return insights

    def observe(self, action_result: str) -> Observation:
        """Observe result - with better success detection"""
        # Better success detection
        success = (
            "success" in action_result.lower() or
            ("error" not in action_result.lower() and "failed" not in action_result.lower() and len(action_result) > 10)
        )

        insights = self._extract_insights(action_result)

        observation = Observation(
            content=action_result,
            success=success,
            timestamp=datetime.now().isoformat(),
            insights=insights
        )

        self.observations.append(observation)
        self._save()
        return observation

    def _synthesize_autonomous_result(self, result: Dict) -> str:
        """Synthesize final result from autonomous execution"""
        if result.get("cycles"):
            last_cycle = result["cycles"][-1]
            if last_cycle.get("result"):
                return str(last_cycle["result"])[:500]

        if self.observations:
            return self.observations[-1].content[:500]

        return "Task completed"

    # ==================== SMART ACTION SELECTION ====================

    def _decide_action(self, thought) -> Tuple[str, str, Dict]:
        """Decide what action to take based on thought - with real action mapping"""
        reasoning = thought.reasoning.lower()
        task = (self.current_task or "").lower()

        # Check last observation for errors
        if self.observations and not self.observations[-1].success:
            # Try different action if last one failed
            last_action = self.actions[-1].type if self.actions else ""
            if last_action == "analyze":
                # Fall back to read_file
                path = self._extract_path(task) or "src/brain/nexus_brain.py"
                return "read_file", path, {}

        # File operations - check task directly for better matching
        if "read" in task or "file" in task and "analyze" in task:
            path = self._extract_path(task) or "src/brain/nexus_brain.py"
            return "read_file", path, {}

        if "write" in task or "create" in task:
            path = self._extract_path(task) or "workspace/output.txt"
            return "write_file", path, {"content": f"Generated by NEXUS at {datetime.now().isoformat()}"}

        if "edit" in task or "modify" in task or "fix" in task:
            path = self._extract_path(task) or "src/brain/nexus_brain.py"
            return "edit_file", path, {}

        # Shell/code operations
        if "run" in task or "execute" in task or "shell" in task:
            return "run_shell", "echo 'Ready to execute commands'", {}

        # Knowledge operations
        if "search" in task or "find" in task:
            return "search", task, {}

        if "learn" in task:
            return "learn", task, {"content": task}

        # Git operations
        if "git" in task or "commit" in task:
            return "git", "status", {}

        # Test operations
        if "test" in task:
            return "test", "tests/", {}

        # Default: read a relevant file
        path = self._extract_path(task)
        if path:
            return "read_file", path, {}

        # Ultimate default: read main brain file
        return "read_file", "src/brain/nexus_brain.py", {}

    def _extract_path(self, text: str) -> Optional[str]:
        """Extract file path from text"""
        import re
        # Look for file paths
        patterns = [
            r"['\"]([^'\"]+\.(py|js|json|md|txt|html))['\"]",
            r"file[:\s]+([^\s]+)",
            r"path[:\s]+([^\s]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)

        return None

    # ==================== FULL AUTONOMOUS EXECUTION ====================

    def execute_autonomously(self, task: str, max_cycles: int = 10) -> Dict:
        """
        Execute task fully autonomously with real actions

        This will:
        1. Analyze the task
        2. Plan actions
        3. Execute real actions
        4. Observe results
        5. Learn from outcomes
        6. Self-reflect
        """
        print(f"\n{'=' * 60}")
        print(f"AUTONOMOUS EXECUTION: {task[:50]}...")
        print(f"{'=' * 60}\n")

        self.current_task = task
        result = {
            "task": task,
            "started_at": datetime.now().isoformat(),
            "cycles": [],
            "final_result": None,
            "success": False,
            "actions_taken": []
        }

        try:
            for cycle in range(max_cycles):
                print(f"\n--- Cycle {cycle + 1} ---")

                # THINK
                print("💭 Thinking...")
                thought = self.think(f"Task: {task}" if cycle == 0 else f"Observation: {self.observations[-1].content if self.observations else 'Starting'}")
                print(f"   Reasoning: {thought.reasoning}")

                # DECIDE ACTION
                action_type, target, params = self._decide_action(thought)
                print(f"🎯 Action: {action_type}({target})")

                # ACT (REAL EXECUTION)
                action = self.act(action_type, target, params)
                print(f"   Result: {action.result[:100] if action.result else 'None'}...")

                # OBSERVE
                observation = self.observe(action.result or "No result")
                print(f"👁 Observation: success={observation.success}")

                cycle_data = {
                    "cycle": cycle + 1,
                    "thought": thought.reasoning,
                    "action": action_type,
                    "target": target,
                    "result": action.result[:200] if action.result else None,
                    "success": observation.success
                }
                result["cycles"].append(cycle_data)
                result["actions_taken"].append(action_type)

                # Check if complete
                if self._is_task_complete(task, observation):
                    print("\n✅ Task completed!")
                    break

            # REFLECT
            print("\n🪞 Self-reflecting...")
            reflection = self.reflect(f"Task: {task}")
            print(f"   Quality: {reflection.quality_score:.2f}")
            if reflection.improvements:
                print(f"   Improvements: {reflection.improvements}")

            # LEARN from execution
            try:
                self._real_learn(f"Executed task: {task[:100]}", "autonomous_execution")
            except:
                pass  # Learning failure shouldn't affect result

            result["success"] = reflection.quality_score >= 0.5
            result["final_result"] = self._synthesize_autonomous_result(result)

        except Exception as e:
            import traceback
            result["error"] = str(e)
            result["error_trace"] = traceback.format_exc()
            result["success"] = False

        result["completed_at"] = datetime.now().isoformat()
        self.current_task = None

        print(f"\n{'=' * 60}")
        print(f"RESULT: {'✅ SUCCESS' if result['success'] else '❌ FAILED'}")
        print(f"{'=' * 60}\n")

        return result


# ==================== CONVENIENCE ====================

_autonomous_agent: Optional[AutonomousAgent] = None


def get_autonomous_agent() -> AutonomousAgent:
    """Get singleton autonomous agent"""
    global _autonomous_agent
    if _autonomous_agent is None:
        _autonomous_agent = AutonomousAgent()
    return _autonomous_agent


def run_autonomously(task: str) -> Dict:
    """Run task autonomously"""
    return get_autonomous_agent().execute_autonomously(task)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("AUTONOMOUS AGENT WITH REAL EXECUTION")
    print("=" * 70)

    agent = get_autonomous_agent()

    print("\nAvailable Real Actions:")
    for name in agent.tools.keys():
        print(f"  • {name}")

    # Test autonomous execution
    result = agent.execute_autonomously("Read the nexus_brain.py file and analyze its structure")

    print("\n" + "=" * 70)
    print("EXECUTION SUMMARY")
    print("=" * 70)
    print(f"Task: {result['task'][:50]}...")
    print(f"Success: {result['success']}")
    print(f"Cycles: {len(result['cycles'])}")
    print(f"Actions: {result['actions_taken']}")
