"""
ORION - Supreme Project Manager
Central orchestrator that coordinates all agents
"""

import os
import asyncio
import json
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from pathlib import Path

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, MessageType, Priority, TaskResult, ProjectContext
from src.core.computer_controller import get_computer_controller
from src.memory.autonomous_improver import get_autonomous_improver
from src.memory.learning_loop import get_learning_loop


class Orion(AsyncAgent):
    """
    ORION - The Supreme Project Manager

    Responsibilities:
    - Coordinate all agents
    - Make strategic decisions
    - Run infinite improvement loop
    - Only stop on SHUTDOWN command
    """

    def __init__(self, agents: Dict[str, AsyncAgent] = None):
        super().__init__(
            name="Orion",
            role="Supreme Project Manager",
            model=os.getenv("ORCHESTRATOR_MODEL", "glm-5"),
            api_key=os.getenv("GLM_API_KEY"),
            api_base=os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        )

        self.agents = agents or {}
        self.context: Optional[ProjectContext] = None
        self.running = False
        self.paused = False
        self.pause_reason = ""
        self.paused_since: Optional[datetime] = None

        # State tracking
        self.iteration = 0
        self.max_iterations = 10000  # Very high for "infinite" loop
        self.target_score = 9.0
        self.history: List[Dict] = []
        self.cycle_timeout_sec = int(os.getenv("ORION_CYCLE_TIMEOUT_SEC", "180"))
        self.last_cycle_started_at: Optional[datetime] = None
        self.last_cycle_finished_at: Optional[datetime] = None
        self.last_progress_at: datetime = datetime.now()
        self.consecutive_fix_cycles = 0
        self.consecutive_no_deploy_cycles = 0
        self.consecutive_nova_no_output = 0
        self.max_fix_streak_before_recovery = int(os.getenv("ORION_FIX_STREAK_TRIGGER", "8"))
        self.max_no_output_before_recovery = int(os.getenv("ORION_NO_OUTPUT_TRIGGER", "4"))
        self.max_backoff_sec = max(2, int(os.getenv("ORION_MAX_BACKOFF_SEC", "20")))
        self.security_audit_interval = max(1, int(os.getenv("SECURITY_AUDIT_INTERVAL", "8")))
        self.security_fallback_audit_interval = max(2, int(os.getenv("SECURITY_FALLBACK_AUDIT_INTERVAL", "20")))
        self.echo_interval = max(1, int(os.getenv("ECHO_TEST_INTERVAL", "4")))
        self.last_echo_iteration = 0
        self.last_tested_fingerprint = ""
        self.last_security_fingerprint = ""
        self.last_full_security_audit_iteration = 0
        self.last_deployed_fingerprint = ""
        self.nova_stable_interval = max(1, int(os.getenv("ORION_NOVA_STABLE_INTERVAL", "2")))
        self.last_nova_iteration = 0
        self.last_nova_result: Optional[TaskResult] = None
        self.agent_controls: Dict[str, Dict[str, Any]] = {}

        # User commands
        self.user_commands: asyncio.Queue = asyncio.Queue()

        # Self-improvement settings
        self.self_improvement_interval = int(os.getenv("SELF_IMPROVEMENT_INTERVAL", "10"))
        self.improver = get_autonomous_improver()
        self.learning_loop = get_learning_loop()

    async def start(self):
        """Start Orion and all agents"""
        await super().start()

        # Start all agents
        for agent in self.agents.values():
            await agent.start()

        self._log("✅ All agents started")

    async def stop(self):
        """Stop Orion and all agents"""
        self.running = False

        # Stop all agents
        for agent in self.agents.values():
            await agent.stop()

        await super().stop()

    def register_agent(self, agent: AsyncAgent):
        """Register an agent"""
        normalized = agent.name.lower()
        self.agents[normalized] = agent
        self._ensure_agent_control(normalized)
        self._log(f"📝 Registered agent: {agent.name}")

    def _ensure_agent_control(self, agent_name: str) -> Dict[str, Any]:
        """Ensure control-plane entry exists for an agent."""
        normalized = str(agent_name or "").strip().lower()
        if not normalized:
            return {}

        existing = self.agent_controls.get(normalized)
        if isinstance(existing, dict):
            existing.setdefault("enabled", True)
            existing.setdefault("reason", "default")
            existing.setdefault("updated_by", "system")
            existing.setdefault("updated_at", datetime.now().isoformat())
            self.agent_controls[normalized] = existing
            return existing

        control = {
            "enabled": True,
            "reason": "default",
            "updated_by": "system",
            "updated_at": datetime.now().isoformat(),
        }
        self.agent_controls[normalized] = control
        return control

    def is_agent_enabled(self, agent_name: str) -> bool:
        """Return whether an agent is enabled in automatic orchestration."""
        normalized = str(agent_name or "").strip().lower()
        if not normalized:
            return False
        control = self._ensure_agent_control(normalized)
        return bool(control.get("enabled", True))

    def set_agent_enabled(
        self,
        agent_name: str,
        enabled: bool,
        reason: str = "",
        updated_by: str = "dashboard",
    ) -> Dict[str, Any]:
        """Toggle one agent in orchestration control plane."""
        normalized = str(agent_name or "").strip().lower()
        if normalized not in self.agents:
            return {"name": normalized, "exists": False}

        control = self._ensure_agent_control(normalized)
        previous = bool(control.get("enabled", True))
        resolved_reason = str(reason or ("manual_enable" if enabled else "manual_disable")).strip()
        control.update(
            {
                "enabled": bool(enabled),
                "reason": resolved_reason,
                "updated_by": str(updated_by or "dashboard"),
                "updated_at": datetime.now().isoformat(),
            }
        )
        self.agent_controls[normalized] = control

        if previous != bool(enabled):
            mode = "ENABLED" if enabled else "DISABLED"
            self._log(f"🎛️ Agent control: {normalized.upper()} -> {mode} ({resolved_reason})")

        return {
            "name": normalized,
            "exists": True,
            "enabled": bool(control.get("enabled", True)),
            "reason": str(control.get("reason", "")),
            "updated_by": str(control.get("updated_by", "")),
            "updated_at": str(control.get("updated_at", "")),
        }

    def get_agent_controls(self) -> Dict[str, Dict[str, Any]]:
        """Return orchestration controls for all registered agents."""
        snapshot: Dict[str, Dict[str, Any]] = {}
        for name in sorted(self.agents.keys()):
            control = self._ensure_agent_control(name)
            snapshot[name] = {
                "enabled": bool(control.get("enabled", True)),
                "reason": str(control.get("reason", "")),
                "updated_by": str(control.get("updated_by", "")),
                "updated_at": str(control.get("updated_at", "")),
            }
        return snapshot

    async def process(self, message: AgentMessage) -> TaskResult:
        """Process incoming message (usually from user)"""

        if message.type == MessageType.SHUTDOWN:
            self._log("🛑 SHUTDOWN command received")
            await self.stop()
            return self.create_result(True, {"status": "shutdown"})

        if message.type == MessageType.STATUS:
            return self.create_result(True, self._get_status())

        if message.type == MessageType.TASK:
            # Start new project
            self.context = ProjectContext(
                project_name=message.content.get("name", "Untitled"),
                project_goal=message.content.get("goal", "Build something amazing")
            )
            self.running = True
            return self.create_result(True, {"status": "project_started", "context": self.context.to_dict()})

        return self.create_result(False, {"error": "Unknown message type"})

    async def run_infinite_loop(self):
        """
        The INFINITE improvement loop
        Runs until SHUTDOWN or max iterations
        """

        self._log("🔄 Starting INFINITE improvement loop")

        while self.running and self.iteration < self.max_iterations:

            # Check for pause
            while self.paused:
                if not self.paused_since:
                    self.paused_since = datetime.now()
                await asyncio.sleep(1)
            self.paused_since = None

            # Check for user commands
            try:
                cmd = self.user_commands.get_nowait()
                if cmd == "shutdown":
                    break
                elif cmd == "pause":
                    self.paused = True
                    self.pause_reason = "user"
                    self.paused_since = datetime.now()
                    continue
                elif cmd == "resume":
                    self.paused = False
                    self.pause_reason = ""
                    self.paused_since = None
            except asyncio.QueueEmpty:
                pass

            self.iteration += 1
            self.last_progress_at = datetime.now()
            self._log(f"\n{'='*60}")
            self._log(f"🔄 ITERATION #{self.iteration}")
            self._log(f"{'='*60}")

            try:
                # Run one parallel cycle
                result = await asyncio.wait_for(
                    self.run_parallel_cycle(),
                    timeout=max(30, self.cycle_timeout_sec),
                )

                # Log result
                self.history.append(result)
                self._save_history()
                self.last_progress_at = datetime.now()
                recovery_needed = self._update_stability_signals(result)

                # Check if target reached
                if result.get("score", 0) >= self.target_score:
                    self._log(f"🎯 TARGET SCORE REACHED: {result['score']}")
                    # Continue anyway - always improving!

                # Self-improvement phase (every N iterations)
                if recovery_needed or self.iteration % self.self_improvement_interval == 0:
                    await self._run_self_improvement()

                # Wait before next iteration
                await asyncio.sleep(self._next_iteration_delay_sec())

            except asyncio.TimeoutError:
                self._log(f"⚠️ Cycle timeout after {self.cycle_timeout_sec}s, Guardian may intervene")
                self.last_progress_at = datetime.now()
                await asyncio.sleep(self._next_iteration_delay_sec())
            except Exception as e:
                self._log(f"❌ Error in iteration: {str(e)}")
                self.last_progress_at = datetime.now()
                await asyncio.sleep(max(5, self._next_iteration_delay_sec()))  # Wait before retry

        self._log("🏁 Infinite loop ended")

    async def run_parallel_cycle(self) -> Dict:
        """
        Run one parallel improvement cycle
        All agents work simultaneously
        """

        cycle_start = datetime.now()
        self.last_cycle_started_at = cycle_start
        cycle_result = {
            "iteration": self.iteration,
            "timestamp": cycle_start.isoformat(),
            "results": {}
        }

        # PHASE 1: Dispatch all agents IN PARALLEL
        self._log("📡 PHASE 1: Dispatching agents in parallel...")

        parallel_tasks = [
            self._dispatch_nova_or_skip(),  # Code generation (adaptive cadence)
            self._dispatch_pixel(),         # UI analysis
            self._dispatch_echo_or_skip(),  # Test generation (adaptive cadence)
        ]

        # Run all in parallel
        results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

        nova_result, pixel_result, echo_result = results

        # Handle exceptions
        if isinstance(nova_result, Exception):
            nova_result = self.create_result(False, {"error": str(nova_result)})
        if isinstance(pixel_result, Exception):
            pixel_result = self.create_result(False, {"error": str(pixel_result)})
        if isinstance(echo_result, Exception):
            echo_result = self.create_result(False, {"error": str(echo_result)})

        cycle_result["results"]["nova"] = nova_result.to_dict() if hasattr(nova_result, 'to_dict') else nova_result
        cycle_result["results"]["pixel"] = pixel_result.to_dict() if hasattr(pixel_result, 'to_dict') else pixel_result
        cycle_result["results"]["echo"] = echo_result.to_dict() if hasattr(echo_result, 'to_dict') else echo_result
        self._sync_context_files_from_nova(nova_result)

        # PHASE 2: Cipher review (needs code from Nova)
        self._log("🔐 PHASE 2: Security review...")
        run_security_audit, audit_reason = self._should_run_security_audit(nova_result)
        if run_security_audit:
            cipher_result = await self._dispatch_cipher(nova_result)
            self.last_full_security_audit_iteration = self.iteration
            self.last_security_fingerprint = self._files_fingerprint()
        else:
            self._log(f"🔎 Security review skipped ({audit_reason})")
            cipher_result = self._build_cached_cipher_result(audit_reason)
        cycle_result["results"]["cipher"] = cipher_result.to_dict() if hasattr(cipher_result, 'to_dict') else cipher_result

        # PHASE 3: Decision
        self._log("⚖️ PHASE 3: Making decision...")
        decision = self._make_decision(nova_result, pixel_result, cipher_result, echo_result)
        cycle_result["decision"] = decision

        # PHASE 4: Deploy or Fix
        if decision["action"] == "deploy":
            current_fingerprint = self._files_fingerprint()
            if current_fingerprint and current_fingerprint == self.last_deployed_fingerprint:
                self._log("🚀 PHASE 4: Deploy skipped (no code changes detected)")
                flux_result = TaskResult(
                    success=True,
                    agent="Flux",
                    output={
                        "skipped": True,
                        "reason": "No code changes since last successful deploy",
                    },
                    score=10.0,
                )
                cycle_result["results"]["flux"] = flux_result.to_dict()
                cycle_result["deployed"] = True
            else:
                self._log("🚀 PHASE 4: Deploying...")
                flux_result = await self._dispatch_flux(nova_result)
                cycle_result["results"]["flux"] = flux_result.to_dict() if hasattr(flux_result, 'to_dict') else flux_result
                flux_output = getattr(flux_result, "output", {}) if hasattr(flux_result, "output") else {}
                skipped = bool(flux_output.get("skipped")) if isinstance(flux_output, dict) else False
                deployed = bool(getattr(flux_result, "success", False)) and not skipped
                cycle_result["deployed"] = deployed
                if deployed:
                    self.last_deployed_fingerprint = current_fingerprint
                if skipped:
                    self._log("🚀 PHASE 4: Deploy skipped by dashboard control")
                elif not deployed:
                    flux_error = ""
                    flux_detail = ""
                    if isinstance(flux_result, dict):
                        output = flux_result.get("output")
                        if isinstance(output, dict):
                            flux_error = str(output.get("error", "")).strip()
                            details = output.get("details")
                            if isinstance(details, dict):
                                issues = details.get("issues")
                                if isinstance(issues, list) and issues:
                                    flux_detail = ", ".join(str(x) for x in issues if x)
                        if not flux_error:
                            flux_error = str(flux_result.get("error", "")).strip()
                    if hasattr(flux_result, "output") and isinstance(flux_result.output, dict):
                        flux_error = str(flux_result.output.get("error", "")).strip()
                        details = flux_result.output.get("details")
                        if isinstance(details, dict):
                            issues = details.get("issues")
                            if isinstance(issues, list) and issues:
                                flux_detail = ", ".join(str(x) for x in issues if x)
                    if hasattr(flux_result, "metadata") and isinstance(flux_result.metadata, dict):
                        flux_error = flux_error or str(flux_result.metadata.get("error", "")).strip()
                    if not flux_error and hasattr(flux_result, "error"):
                        flux_error = str(getattr(flux_result, "error", "")).strip()
                    detail_suffix = f" ({flux_detail})" if flux_detail else ""
                    self._log(f"⚠️ Deploy step failed in Flux: {flux_error or 'unknown error'}{detail_suffix}")
        else:
            self._log(f"🔧 PHASE 4: Needs improvement - {decision['reason']}")
            cycle_result["deployed"] = False

            # Update context with feedback for next iteration
            self._update_context_with_feedback(
                nova_result, pixel_result, cipher_result, echo_result
            )

        # Calculate cycle score
        scores = []
        for r in [nova_result, pixel_result, cipher_result, echo_result]:
            if hasattr(r, 'score') and r.score is not None:
                scores.append(r.score)

        cycle_result["score"] = sum(scores) / len(scores) if scores else 0
        cycle_result["duration"] = (datetime.now() - cycle_start).total_seconds()
        self.last_cycle_finished_at = datetime.now()
        self.last_progress_at = datetime.now()

        self._log(f"📊 Cycle Score: {cycle_result['score']:.1f}/10")
        self._log(f"⏱️ Duration: {cycle_result['duration']:.1f}s")

        return cycle_result

    async def _run_self_improvement(self):
        """
        Self-improvement phase: Analyze current state and apply improvements
        This is the CLOSED LOOP - system improves itself autonomously
        """
        self._log("🧠 PHASE: Self-improvement analysis...")

        try:
            # 1. Get current system stats
            improver_stats = self.improver.get_stats()
            learning_state = self.learning_loop.get_state() if hasattr(self.learning_loop, 'get_state') else {}

            # 2. Learn from recent cycle results
            if self.history:
                last_result = self.history[-1]
                cycle_summary = {
                    "iteration": self.iteration,
                    "score": last_result.get("score", 0),
                    "deployed": last_result.get("deployed", False),
                    "duration": last_result.get("duration", 0)
                }
                self.improver.learn_from_input(
                    "analysis",
                    f"Cycle result: {cycle_summary}",
                    value_score=0.7,
                    context=cycle_summary
                )

            # 3. Run autonomous improvement
            self._log("🔧 Running autonomous improvement...")
            improvement_results = self.improver.run_auto_improvement()

            # 4. Run learning loop for deeper analysis
            if hasattr(self.learning_loop, 'run_iteration'):
                learning_results = self.learning_loop.run_iteration()
                self._log(f"📚 Learning loop: {learning_results.get('actions_taken', 0)} actions")

            # 5. Log decision
            decision = {
                "iteration": self.iteration,
                "timestamp": datetime.now().isoformat(),
                "type": "self_improvement",
                "improver_stats": improver_stats,
                "improvement_results": improvement_results,
                "learning_state": learning_state
            }

            # Save to decision history
            decision_file = Path("data/memory/decision_log.json")
            if decision_file.exists():
                with open(decision_file, 'r') as f:
                    decisions = json.load(f)
            else:
                decisions = {"sessions": []}

            # Add to current session or create new
            if not decisions.get("sessions"):
                decisions["sessions"].append({"decisions": [], "start_time": datetime.now().isoformat()})

            decisions["sessions"][-1]["decisions"].append(decision)

            # Keep last 1000 decisions
            if len(decisions["sessions"][-1]["decisions"]) > 1000:
                decisions["sessions"][-1]["decisions"] = decisions["sessions"][-1]["decisions"][-1000:]

            with open(decision_file, 'w') as f:
                json.dump(decisions, f, indent=2)

            # Log summary
            improvements_applied = len(improvement_results.get("applied", []))
            improvements_pending = len(improvement_results.get("pending", []))

            self._log(f"✅ Self-improvement complete: {improvements_applied} applied, {improvements_pending} pending")

        except Exception as e:
            self._log(f"⚠️ Self-improvement error: {str(e)}")

    async def _dispatch_nova(self) -> TaskResult:
        """Dispatch Nova for code generation"""
        if "nova" not in self.agents:
            return self.create_result(False, {"error": "Nova not registered"})

        message = AgentMessage(
            from_agent="orion",
            to_agent="nova",
            type=MessageType.TASK,
            priority=Priority.MEDIUM,
            content={
                "context": self.context.to_dict() if self.context else {},
                "iteration": self.iteration,
                "runtime_hints": {
                    "nova_no_output_streak": self.consecutive_nova_no_output,
                    "no_deploy_streak": self.consecutive_no_deploy_cycles,
                    "prefer_cost": bool(
                        self.iteration > 3
                        and self.consecutive_fix_cycles == 0
                        and self.consecutive_no_deploy_cycles == 0
                        and self.consecutive_nova_no_output == 0
                    ),
                },
            }
        )

        return await self.agents["nova"].process(message)

    def _should_run_nova(self) -> Tuple[bool, str]:
        """Decide if Nova should run this cycle to reduce unnecessary token burn."""
        def _safe_int(value: Any) -> int:
            try:
                return int(value)
            except Exception:
                return 0

        if self.iteration <= 2:
            return True, "warmup cycles"
        if self.nova_stable_interval <= 1:
            return True, "stable cadence disabled"
        if self.consecutive_fix_cycles > 0:
            return True, "active fix cycle"
        if self.consecutive_no_deploy_cycles > 0:
            return True, "waiting for successful deploy"
        if self.consecutive_nova_no_output > 0:
            return True, "recovering from no-output streak"

        recent_feedback = self.context.feedback_history[-4:] if self.context else []
        recent_issue_feedback = any(
            isinstance(item, dict)
            and isinstance(item.get("issues"), list)
            and item.get("issues")
            and _safe_int(item.get("iteration", 0) or 0) >= max(1, self.iteration - 2)
            for item in recent_feedback
        )
        if recent_issue_feedback:
            return True, "recent unresolved feedback"

        elapsed = self.iteration - self.last_nova_iteration
        if elapsed >= self.nova_stable_interval:
            return True, f"nova cadence interval reached ({elapsed}/{self.nova_stable_interval})"
        return False, f"stable cadence ({elapsed}/{self.nova_stable_interval})"

    def _cached_nova_output_files(self) -> List[str]:
        """Return existing output files so downstream steps can keep operating when Nova is skipped."""
        output_dir = Path("app-output")
        preferred = ["index.html", "styles.css", "app.js"]
        file_names = preferred[:]

        if self.context and isinstance(self.context.files, dict):
            for name in sorted(self.context.files.keys()):
                if name not in file_names:
                    file_names.append(name)

        existing: List[str] = []
        for name in file_names:
            candidate = output_dir / name
            if candidate.exists() and candidate.is_file():
                existing.append(str(candidate.resolve()))
        return existing

    async def _dispatch_nova_or_skip(self) -> TaskResult:
        """Run Nova on demand; skip stable cycles to control token spend."""
        if not self.is_agent_enabled("nova"):
            return TaskResult(
                success=True,
                agent="Nova",
                output={
                    "skipped": True,
                    "reason": "Nova disabled by dashboard control plane",
                    "files": self._cached_nova_output_files(),
                    "generation_flags": {
                        "repair_pass": False,
                        "local_scaffold_fallback": False,
                        "cadence_skip": True,
                        "agent_disabled": True,
                    },
                },
                score=8.4,
                suggestions=["Enable Nova from dashboard when you need fresh code generation."],
            )

        should_run, reason = self._should_run_nova()
        if not should_run:
            self._log(f"💤 Nova skipped this cycle ({reason})")
            return TaskResult(
                success=True,
                agent="Nova",
                output={
                    "skipped": True,
                    "reason": reason,
                    "files": self._cached_nova_output_files(),
                    "generation_flags": {
                        "repair_pass": False,
                        "local_scaffold_fallback": False,
                        "cadence_skip": True,
                    },
                },
                score=8.4,
                suggestions=[
                    "Nova cadence skip active on stable cycles to reduce token usage.",
                    "A full Nova generation will resume automatically on feedback/fix/deploy triggers.",
                ],
            )

        result = await self._dispatch_nova()
        if hasattr(result, "success") and result.success:
            self.last_nova_iteration = self.iteration
            self.last_nova_result = result
        return result

    async def _dispatch_echo_or_skip(self) -> TaskResult:
        """Dispatch Echo with adaptive cadence to reduce redundant token usage."""
        if "echo" not in self.agents:
            return self.create_result(True, {"note": "Echo not registered"})
        if not self.is_agent_enabled("echo"):
            return TaskResult(
                success=True,
                agent="Echo",
                output={"skipped": True, "reason": "Echo disabled by dashboard control plane"},
                score=8.0,
                suggestions=["Enable Echo from dashboard when you need test generation again."],
            )

        current_fingerprint = self._files_fingerprint()
        should_run = (
            self.iteration <= 2
            or not current_fingerprint
            or current_fingerprint != self.last_tested_fingerprint
            or (self.iteration - self.last_echo_iteration) >= self.echo_interval
        )
        if not should_run:
            return TaskResult(
                success=True,
                agent="Echo",
                output={"skipped": True, "reason": "No meaningful code changes for retest"},
                score=8.0,
                suggestions=["Retest will run automatically on next code-delta or interval trigger."],
            )

        self.last_echo_iteration = self.iteration
        result = await self._dispatch_echo()
        self.last_tested_fingerprint = current_fingerprint
        return result

    async def _dispatch_pixel(self) -> TaskResult:
        """Dispatch Pixel for UI analysis"""
        if "pixel" not in self.agents:
            return self.create_result(False, {"error": "Pixel not registered"})
        if not self.is_agent_enabled("pixel"):
            return TaskResult(
                success=True,
                agent="Pixel",
                output={"skipped": True, "reason": "Pixel disabled by dashboard control plane"},
                score=8.0,
                suggestions=["Enable Pixel from dashboard to resume UI/UX analysis."],
            )

        # Get latest screenshot
        screenshot = None
        if self.context and self.context.screenshots:
            screenshot = self.context.screenshots[-1]

        message = AgentMessage(
            from_agent="orion",
            to_agent="pixel",
            type=MessageType.TASK,
            priority=Priority.MEDIUM,
            content={
                "screenshot": screenshot,
                "context": self.context.to_dict() if self.context else {},
                "iteration": self.iteration
            }
        )

        return await self.agents["pixel"].process(message)

    async def _dispatch_cipher(self, nova_result: TaskResult) -> TaskResult:
        """Dispatch Cipher for security review"""
        if "cipher" not in self.agents:
            return self.create_result(True, {"note": "Cipher not registered, skipping review"})
        if not self.is_agent_enabled("cipher"):
            return TaskResult(
                success=True,
                agent="Cipher",
                output={
                    "security_score": 8.5,
                    "issues": [],
                    "passed": True,
                    "mode": "disabled_skip",
                    "reason": "Cipher disabled by dashboard control plane",
                },
                score=8.5,
                suggestions=["Enable Cipher from dashboard for full security audits."],
            )

        message = AgentMessage(
            from_agent="orion",
            to_agent="cipher",
            type=MessageType.REVIEW,
            priority=Priority.CRITICAL,
            content={
                "code": nova_result.output if nova_result.success else {},
                "context": self.context.to_dict() if self.context else {}
            }
        )

        return await self.agents["cipher"].process(message)

    def _should_run_security_audit(self, nova_result: TaskResult) -> (bool, str):
        """Decide whether to run expensive security audit this cycle."""
        if "cipher" not in self.agents:
            return False, "Cipher not registered"
        if self.iteration <= 2:
            return True, "warmup cycles"

        output = getattr(nova_result, "output", {}) or {}
        generation_flags = output.get("generation_flags", {}) if isinstance(output, dict) else {}
        used_fallback = bool(generation_flags.get("local_scaffold_fallback")) if isinstance(generation_flags, dict) else False
        current_fingerprint = self._files_fingerprint()

        if not current_fingerprint:
            return True, "missing fingerprint"
        if current_fingerprint != self.last_security_fingerprint:
            return True, "code fingerprint changed"

        interval = self.security_fallback_audit_interval if used_fallback else self.security_audit_interval
        if (self.iteration - self.last_full_security_audit_iteration) >= interval:
            return True, f"periodic interval {interval}"

        return False, "cached fingerprint and interval not reached"

    def _build_cached_cipher_result(self, reason: str) -> TaskResult:
        """Build lightweight security status when full audit is skipped."""
        return TaskResult(
            success=True,
            agent="Cipher",
            output={
                "security_score": 8.6,
                "issues": [],
                "passed": True,
                "mode": "cached_skip",
                "reason": reason,
            },
            score=8.6,
            suggestions=["Deep security audit scheduled periodically or on code fingerprint change."],
        )

    async def _dispatch_echo(self) -> TaskResult:
        """Dispatch Echo for test generation"""
        if "echo" not in self.agents:
            return self.create_result(True, {"note": "Echo not registered"})

        message = AgentMessage(
            from_agent="orion",
            to_agent="echo",
            type=MessageType.TASK,
            priority=Priority.LOW,
            content={
                "context": self.context.to_dict() if self.context else {},
                "iteration": self.iteration
            }
        )

        return await self.agents["echo"].process(message)

    async def _dispatch_flux(self, nova_result: TaskResult) -> TaskResult:
        """Dispatch Flux for deployment"""
        if "flux" not in self.agents:
            return self.create_result(True, {"note": "Flux not registered"})
        if not self.is_agent_enabled("flux"):
            return TaskResult(
                success=True,
                agent="Flux",
                output={
                    "skipped": True,
                    "reason": "Flux disabled by dashboard control plane",
                },
                score=8.0,
                suggestions=["Enable Flux from dashboard when you want auto deploy again."],
            )

        message = AgentMessage(
            from_agent="orion",
            to_agent="flux",
            type=MessageType.DEPLOY,
            priority=Priority.HIGH,
            content={
                "code": nova_result.output if nova_result.success else {},
                "iteration": self.iteration
            }
        )

        return await self.agents["flux"].process(message)

    def _make_decision(
        self,
        nova_result: TaskResult,
        pixel_result: TaskResult,
        cipher_result: TaskResult,
        echo_result: TaskResult
    ) -> Dict:
        """Make decision based on all results"""

        # Check for vetoes
        if hasattr(cipher_result, 'veto') and cipher_result.veto:
            return {
                "action": "fix",
                "reason": f"Security veto: {cipher_result.veto_reason}"
            }

        # Check UI score
        pixel_score = getattr(pixel_result, 'score', 10) or 10
        if pixel_score < 6:
            return {
                "action": "fix",
                "reason": f"UI score too low: {pixel_score}/10"
            }

        # Check if Nova succeeded
        if not (hasattr(nova_result, 'success') and nova_result.success):
            return {
                "action": "fix",
                "reason": "Code generation failed"
            }

        # All good - deploy!
        return {
            "action": "deploy",
            "reason": "All checks passed"
        }

    def _update_context_with_feedback(self, *results):
        """Update context with feedback from all agents"""
        if not self.context:
            return
        for result in results:
            if hasattr(result, 'issues') and result.issues:
                self.context.feedback_history.append({
                    "agent": result.agent,
                    "iteration": self.iteration,
                    "issues": result.issues,
                    "suggestions": getattr(result, 'suggestions', [])
                })

    def _get_status(self) -> Dict:
        """Get current status"""
        return {
            "running": self.running,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "paused_since": self.paused_since.isoformat() if self.paused_since else None,
            "iteration": self.iteration,
            "target_score": self.target_score,
            "agents": list(self.agents.keys()),
            "agent_controls": self.get_agent_controls(),
            "cycle_timeout_sec": self.cycle_timeout_sec,
            "last_cycle_started_at": self.last_cycle_started_at.isoformat() if self.last_cycle_started_at else None,
            "last_cycle_finished_at": self.last_cycle_finished_at.isoformat() if self.last_cycle_finished_at else None,
            "last_progress_at": self.last_progress_at.isoformat() if self.last_progress_at else None,
            "stability": {
                "fix_streak": self.consecutive_fix_cycles,
                "no_deploy_streak": self.consecutive_no_deploy_cycles,
                "nova_no_output_streak": self.consecutive_nova_no_output,
                "fix_streak_trigger": self.max_fix_streak_before_recovery,
                "no_output_trigger": self.max_no_output_before_recovery,
            },
            "context": self.context.to_dict() if self.context else None
        }

    def _sync_context_files_from_nova(self, nova_result: TaskResult) -> None:
        """Load Nova output files into shared context for next-cycle analysis."""
        if not self.context or not hasattr(nova_result, "success") or not nova_result.success:
            return
        output = getattr(nova_result, "output", {}) or {}
        files = output.get("files", []) if isinstance(output, dict) else []
        if not isinstance(files, list):
            return

        synced = 0
        for file_path in files:
            if not isinstance(file_path, str):
                continue
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                continue
            try:
                self.context.files[path.name] = path.read_text(encoding="utf-8")
                synced += 1
            except Exception:
                continue
        if synced:
            self._log(f"🗂️ Synced {synced} Nova file(s) into context")

    def _files_fingerprint(self) -> str:
        """Deterministic fingerprint for current in-memory files."""
        if not self.context or not isinstance(self.context.files, dict) or not self.context.files:
            return ""
        digest = hashlib.sha256()
        for path in sorted(self.context.files.keys()):
            digest.update(path.encode("utf-8", errors="ignore"))
            digest.update(b"\0")
            digest.update(str(self.context.files.get(path, "")).encode("utf-8", errors="ignore"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _update_stability_signals(self, cycle_result: Dict[str, Any]) -> bool:
        """Track repetitive failures and trigger recovery actions when needed."""
        decision = cycle_result.get("decision", {}) if isinstance(cycle_result, dict) else {}
        action = str(decision.get("action", "")).strip().lower()
        deployed = bool(cycle_result.get("deployed"))
        nova_result = ((cycle_result.get("results") or {}).get("nova") or {}) if isinstance(cycle_result, dict) else {}

        if action == "fix":
            self.consecutive_fix_cycles += 1
        else:
            self.consecutive_fix_cycles = 0

        if deployed:
            self.consecutive_no_deploy_cycles = 0
        else:
            self.consecutive_no_deploy_cycles += 1

        nova_issues = []
        if isinstance(nova_result, dict):
            raw_issues = nova_result.get("issues", [])
            if isinstance(raw_issues, list):
                nova_issues = [str(item) for item in raw_issues]
            output = nova_result.get("output", {})
            if isinstance(output, dict):
                output_error = str(output.get("error", "")).lower()
                if "no deployable files" in output_error:
                    nova_issues.append("No files generated")
        nova_no_output = any(
            ("no files generated" in issue.lower()) or ("local scaffold fallback" in issue.lower())
            for issue in nova_issues
        )
        if nova_no_output:
            self.consecutive_nova_no_output += 1
        else:
            self.consecutive_nova_no_output = 0

        trigger_fix = self.consecutive_fix_cycles >= self.max_fix_streak_before_recovery
        trigger_nova = self.consecutive_nova_no_output >= self.max_no_output_before_recovery

        if trigger_fix:
            self._log(
                f"⚠️ Stability trigger: fix streak={self.consecutive_fix_cycles}. "
                "Forcing self-improvement cycle."
            )
        if trigger_nova:
            self._log(
                f"⚠️ Stability trigger: Nova no-output streak={self.consecutive_nova_no_output}. "
                "Using recovery strategy and extended backoff."
            )
        return trigger_fix or trigger_nova

    def _next_iteration_delay_sec(self) -> int:
        """Adaptive delay to avoid high-cost hot loops during stagnation."""
        delay = 2
        if self.consecutive_no_deploy_cycles >= 5:
            delay = max(delay, 4)
        if self.consecutive_no_deploy_cycles >= 10:
            delay = max(delay, 8)
        if self.consecutive_fix_cycles >= self.max_fix_streak_before_recovery:
            delay = max(delay, 10)
        if self.consecutive_nova_no_output >= self.max_no_output_before_recovery:
            delay = max(delay, 12)
        return min(delay, self.max_backoff_sec)

    def _save_history(self):
        """Save iteration history"""
        history_file = Path("logs/orion_history.json")
        history_file.parent.mkdir(parents=True, exist_ok=True)

        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, default=str)
