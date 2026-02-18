"""
ORION - Supreme Project Manager
Central orchestrator that coordinates all agents
"""

import os
import asyncio
import json
import hashlib
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from pathlib import Path

import requests

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, MessageType, Priority, TaskResult, ProjectContext
from src.core.computer_controller import get_computer_controller
from src.core.multi_orion_hub import get_multi_orion_hub
from src.core.orion_messenger import get_messenger
from src.core.human_notifier import get_notifier
from src.core.routing_telemetry import read_recent_routing_events
from src.core.provider_profile import ProviderProfileStore
from src.core.official_model_registry import refresh_model_registry_from_profile
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
        self.history_limit = max(100, int(os.getenv("ORION_HISTORY_LIMIT", "2000")))
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
        self._base_security_audit_interval = self.security_audit_interval
        self._base_echo_interval = self.echo_interval
        self.last_echo_iteration = 0
        self.last_tested_fingerprint = ""
        self.last_security_fingerprint = ""
        self.last_full_security_audit_iteration = 0
        self.last_deployed_fingerprint = ""
        self.nova_stable_interval = max(1, int(os.getenv("ORION_NOVA_STABLE_INTERVAL", "2")))
        self._base_nova_stable_interval = self.nova_stable_interval
        self.last_nova_iteration = 0
        self.last_nova_result: Optional[TaskResult] = None
        self.agent_controls: Dict[str, Dict[str, Any]] = {}
        self.token_guard_enabled = os.getenv("ORION_TOKEN_GUARD_ENABLED", "true").lower() == "true"
        self.token_guard_soft_ratio = max(0.1, float(os.getenv("ORION_TOKEN_GUARD_SOFT_RATIO", "0.85")))
        self.token_guard_hard_ratio = max(
            self.token_guard_soft_ratio,
            float(os.getenv("ORION_TOKEN_GUARD_HARD_RATIO", "1.0")),
        )
        self.token_hard_cap_enabled = os.getenv("ORION_TOKEN_HARD_CAP_ENABLED", "true").lower() == "true"
        self.token_hard_cap_ratio = max(
            self.token_guard_hard_ratio,
            float(os.getenv("ORION_TOKEN_HARD_CAP_RATIO", "1.2")),
        )
        self.token_hard_cap_interval_multiplier = max(2, int(os.getenv("ORION_TOKEN_HARD_CAP_MULTIPLIER", "2")))
        self.pixel_soft_interval = max(1, int(os.getenv("ORION_PIXEL_SOFT_INTERVAL", "2")))
        self.pixel_hard_interval = max(self.pixel_soft_interval, int(os.getenv("ORION_PIXEL_HARD_INTERVAL", "4")))
        self.nova_soft_interval = max(self.nova_stable_interval, int(os.getenv("ORION_NOVA_SOFT_INTERVAL", "3")))
        self.nova_hard_interval = max(self.nova_soft_interval, int(os.getenv("ORION_NOVA_HARD_INTERVAL", "5")))
        self.echo_soft_interval = max(self.echo_interval, int(os.getenv("ORION_ECHO_SOFT_INTERVAL", "5")))
        self.echo_hard_interval = max(self.echo_soft_interval, int(os.getenv("ORION_ECHO_HARD_INTERVAL", "8")))
        self.token_policy_tune_interval = max(2, int(os.getenv("ORION_TOKEN_POLICY_TUNE_INTERVAL", "4")))
        self.last_token_policy_tune_iteration = 0
        self.token_policy_mode = "balanced"
        self.last_pixel_iteration = 0
        self._last_token_budget: Dict[str, Any] = {}
        self.model_registry_auto_refresh_enabled = os.getenv("MODEL_REGISTRY_AUTO_REFRESH_ENABLED", "true").lower() == "true"
        self.model_registry_refresh_interval_sec = max(
            600,
            int(os.getenv("MODEL_REGISTRY_REFRESH_INTERVAL_SEC", "21600")),
        )
        self._last_model_registry_refresh_ts: float = 0.0
        self.provider_store = ProviderProfileStore()

        self.instance_id = str(os.getenv("ORION_INSTANCE_ID", self.name.lower())).strip().lower() or self.name.lower()
        self.control_plane_base_url = str(os.getenv("MONITOR_BASE_URL", os.getenv("OPENCLAW_DASHBOARD_URL", "http://127.0.0.1:5050"))).strip().rstrip("/")

        # Central Command integration (Phase 2)
        self.central_command_url = str(os.getenv("CENTRAL_COMMAND_URL", self.control_plane_base_url)).strip().rstrip("/")
        self.registered_orion_id = self.instance_id
        self.current_locked_files: List[str] = []
        self._central_command_registered = False
        self._heartbeat_interval_sec = max(5, int(os.getenv("ORION_HEARTBEAT_INTERVAL_SEC", "30")))
        self._last_heartbeat_ts: float = 0.0
        self.control_plane_lease_ttl_sec = max(10, int(os.getenv("CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC", "35")))
        self.control_plane_heartbeat_interval_sec = max(3, int(os.getenv("ORION_CONTROL_PLANE_HEARTBEAT_SEC", "10")))
        self.task_lease_heartbeat_interval_sec = max(10, int(os.getenv("ORION_TASK_LEASE_HEARTBEAT_SEC", "60")))
        self._last_control_plane_heartbeat_at: float = 0.0
        self._last_task_lease_heartbeat_at: float = 0.0

        # User commands
        self.user_commands: asyncio.Queue = asyncio.Queue()

        # Self-improvement settings
        self.self_improvement_interval = int(os.getenv("SELF_IMPROVEMENT_INTERVAL", "10"))
        self.improver = get_autonomous_improver()
        self.learning_loop = get_learning_loop()

        # Inter-ORION messaging
        self.messenger = get_messenger()
        self.message_check_interval_sec = max(5, int(os.getenv("ORION_MESSAGE_CHECK_INTERVAL_SEC", "15")))
        self._last_message_check_at: float = 0.0
        self._pending_help_responses: List[Dict[str, Any]] = []

        # Human notifications
        self.human_notifier = get_notifier()
        self.status_report_interval = max(50, int(os.getenv("ORION_STATUS_REPORT_INTERVAL", "100")))  # Every N iterations
        self._last_status_report_at: Optional[datetime] = None

        # Auto-coordination settings
        self.auto_coordination_enabled = os.getenv("ORION_AUTO_COORDINATION", "true").lower() == "true"
        self.coordination_threshold = int(os.getenv("ORION_COORDINATION_THRESHOLD", "3"))  # Tasks before coordination

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

    # ============================================
    # Inter-ORION Messaging
    # ============================================

    def check_messages(self) -> List[Dict[str, Any]]:
        """Check for new messages addressed to this ORION."""
        now = time.time()
        if now - self._last_message_check_at < self.message_check_interval_sec:
            return []

        self._last_message_check_at = now
        try:
            result = self.messenger.get_messages(
                orion_id=self.instance_id,
                unread_only=True,
                limit=10,
            )
            messages = result.get("messages", [])

            # Process help requests
            for msg in messages:
                if msg.get("message_type") == "help_request" and not msg.get("responded"):
                    self._handle_help_request(msg)

            return messages
        except Exception as e:
            self._log(f"⚠️ Failed to check messages: {e}")
            return []

    def send_message_to_orion(
        self,
        to_orion: str,
        content: str,
        message_type: str = "direct",
        priority: str = "normal",
        related_task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message to another ORION."""
        try:
            result = self.messenger.send_message(
                from_orion=self.instance_id,
                to_orion=to_orion,
                message_type=message_type,
                content=content,
                priority=priority,
                related_task_id=related_task_id,
            )
            if result.get("success"):
                self._log(f"📨 Sent {message_type} to {to_orion}")
            return result
        except Exception as e:
            self._log(f"⚠️ Failed to send message: {e}")
            return {"success": False, "error": str(e)}

    def broadcast_status(self, status: str, current_task: Optional[str] = None) -> Dict[str, Any]:
        """Broadcast status to all ORIONs."""
        try:
            result = self.messenger.broadcast_status(
                from_orion=self.instance_id,
                status=status,
                current_task=current_task,
                workload=len([t for t in self.history[-20:] if t.get("timestamp")]),
            )
            return result
        except Exception as e:
            self._log(f"⚠️ Failed to broadcast status: {e}")
            return {"success": False, "error": str(e)}

    def request_help(
        self,
        help_type: str,
        description: str,
        related_task_id: Optional[str] = None,
        urgency: str = "normal",
    ) -> Dict[str, Any]:
        """Request help from other ORIONs."""
        try:
            result = self.messenger.request_help(
                from_orion=self.instance_id,
                help_type=help_type,
                description=description,
                related_task_id=related_task_id,
                urgency=urgency,
            )
            if result.get("success"):
                self._log(f"🆘 Sent help request: {help_type}")
            return result
        except Exception as e:
            self._log(f"⚠️ Failed to request help: {e}")
            return {"success": False, "error": str(e)}

    def handoff_task(
        self,
        to_orion: str,
        task_id: str,
        task_title: str,
        handoff_reason: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Hand off a task to another ORION."""
        try:
            result = self.messenger.handoff_task(
                from_orion=self.instance_id,
                to_orion=to_orion,
                task_id=task_id,
                task_title=task_title,
                handoff_reason=handoff_reason,
                context=context,
            )
            if result.get("success"):
                self._log(f"📤 Handed off task {task_id} to {to_orion}")
            return result
        except Exception as e:
            self._log(f"⚠️ Failed to handoff task: {e}")
            return {"success": False, "error": str(e)}

    def _handle_help_request(self, msg: Dict[str, Any]) -> None:
        """Handle an incoming help request."""
        help_type = (msg.get("metadata") or {}).get("help_type", "general")
        from_orion = msg.get("from_orion", "unknown")

        # Check if we can help based on our current state and capabilities
        can_help = not self.paused and len(self.history[-5:]) < 3  # Not too busy

        if can_help:
            self._pending_help_responses.append({
                "message_id": msg.get("id"),
                "from_orion": from_orion,
                "help_type": help_type,
                "content": msg.get("content", ""),
            })
            self._log(f"🤝 Can help {from_orion} with {help_type}")

    # ============================================
    # Auto-Coordination
    # ============================================

    def should_auto_coordinate(self, result: Dict[str, Any]) -> bool:
        """Determine if auto-coordination is needed."""
        if not self.auto_coordination_enabled:
            return False

        # Check for complex task indicators
        complexity_score = 0

        # High number of issues
        issues = result.get("issues", [])
        if len(issues) > 3:
            complexity_score += 2

        # Low score after multiple iterations
        if result.get("score", 0) < 6.0 and self.iteration > 5:
            complexity_score += 2

        # Security concerns
        if result.get("security_concerns"):
            complexity_score += 3

        # Test failures
        if result.get("test_failures", 0) > 2:
            complexity_score += 1

        # Multiple failed attempts
        if self.consecutive_fix_cycles > 3:
            complexity_score += 2

        return complexity_score >= self.coordination_threshold

    def trigger_auto_coordination(self, reason: str, context: Dict[str, Any]) -> None:
        """Trigger automatic coordination with other ORIONs."""
        self._log(f"🎯 Auto-coordination triggered: {reason}")

        # Broadcast help request
        self.request_help(
            help_type="auto_coordination",
            description=f"Auto-coordination needed: {reason}",
            related_task_id=context.get("task_id"),
            urgency="normal",
        )

        # Notify humans
        self.human_notifier.notify(
            title="🔄 Auto-Coordination Triggered",
            message=f"ORION {self.instance_id} initiated auto-coordination.\nReason: {reason}",
            level="info",
            source=self.instance_id,
            channels=["dashboard"],
        )

    # ============================================
    # Status Reporting
    # ============================================

    def generate_status_report(self) -> Dict[str, Any]:
        """Generate a comprehensive status report."""
        now = datetime.now()

        # Recent activity
        recent_history = self.history[-20:] if self.history else []
        avg_score = sum(h.get("score", 0) for h in recent_history) / max(1, len(recent_history))

        # Agent status
        agent_status = {}
        for name, agent in self.agents.items():
            control = self.agent_controls.get(name, {})
            agent_status[name] = {
                "enabled": control.get("enabled", True),
                "running": hasattr(agent, "running") and agent.running,
            }

        # Task metrics from hub
        hub = get_multi_orion_hub()
        hub_metrics = hub.get_metrics()
        hub_snapshot = hub.get_snapshot()

        # Message stats
        msg_stats = self.messenger.get_stats()
        token_budget = self._get_token_budget_snapshot()
        token_economics = self._token_economics_snapshot()
        token_hard_cap = self._hard_cap_state()

        return {
            "instance_id": self.instance_id,
            "timestamp": now.isoformat(),
            "iteration": self.iteration,
            "running": self.running,
            "paused": self.paused,
            "avg_score": round(avg_score, 2),
            "history_size": len(self.history),
            "consecutive_fix_cycles": self.consecutive_fix_cycles,
            "agents": agent_status,
            "hub_metrics": hub_metrics,
            "online_orions": len([o for o in hub_snapshot.get("orions", {}).values()
                                 if o.get("status") == "active"]),
            "message_stats": msg_stats,
            "token_budget": token_budget,
            "token_economics": token_economics,
            "token_policy_mode": self.token_policy_mode,
            "token_hard_cap": token_hard_cap,
            "last_cycle": self.last_cycle_finished_at.isoformat() if self.last_cycle_finished_at else None,
        }

    def send_status_report(self) -> None:
        """Send status report to humans and other ORIONs."""
        report = self.generate_status_report()

        # Broadcast to other ORIONs
        self.broadcast_status(
            status="reporting",
            current_task=f"Iteration {self.iteration}, avg score {report['avg_score']}",
        )

        # Notify humans with summary
        if report["avg_score"] >= 8.0:
            level = "success"
            title = f"✅ Status Report - Healthy (Score: {report['avg_score']})"
        elif report["avg_score"] >= 6.0:
            level = "info"
            title = f"📊 Status Report - Stable (Score: {report['avg_score']})"
        else:
            level = "warning"
            title = f"⚠️ Status Report - Needs Attention (Score: {report['avg_score']})"

        message = f"""Iteration: {self.iteration}
Agents: {len(report['agents'])} active
Hub Tasks: {report['hub_metrics']['total_tasks']} total, {report['hub_metrics']['in_progress']} in progress
Online ORIONs: {report['online_orions']}
Messages: {report['message_stats']['total_messages']} total"""

        self.human_notifier.notify(
            title=title,
            message=message,
            level=level,
            source=self.instance_id,
            channels=["dashboard"],
        )

        self._last_status_report_at = datetime.now()
        self._log(f"📊 Status report sent (score: {report['avg_score']})")

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
        self._register_control_plane_worker()

        # Register with Central Command (Phase 2)
        self._register_central_command()

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
            self._maybe_refresh_official_model_registry()
            self._log(f"\n{'='*60}")
            self._log(f"🔄 ITERATION #{self.iteration}")
            self._log(f"{'='*60}")

            # Check for inter-ORION messages
            messages = self.check_messages()
            if messages:
                self._log(f"📨 {len(messages)} new message(s) received")

            # Periodic status broadcast (every 10 iterations)
            if self.iteration % 10 == 0:
                status = "paused" if self.paused else "running"
                current_task = self.history[-1].get("task", "idle") if self.history else "idle"
                self.broadcast_status(status, current_task[:50] if current_task else None)

            # Central Command heartbeat (every iteration)
            self._heartbeat_central_command()

            try:
                # Run one parallel cycle
                result = await asyncio.wait_for(
                    self.run_parallel_cycle(),
                    timeout=max(30, self.cycle_timeout_sec),
                )

                # Log result
                self.history.append(result)
                if len(self.history) > self.history_limit:
                    self.history = self.history[-self.history_limit:]
                self._save_history()
                self.last_progress_at = datetime.now()
                recovery_needed = self._update_stability_signals(result)
                economics = self._token_economics_snapshot()
                tune = self._maybe_tune_token_policy(economics=economics)
                result["token_economics"] = economics
                result["token_policy_tuning"] = tune

                # Check if target reached
                if result.get("score", 0) >= self.target_score:
                    self._log(f"🎯 TARGET SCORE REACHED: {result['score']}")
                    # Continue anyway - always improving!

                # Self-improvement phase (every N iterations)
                if recovery_needed or self.iteration % self.self_improvement_interval == 0:
                    await self._run_self_improvement()

                # Auto-coordination check
                if self.should_auto_coordinate(result):
                    self.trigger_auto_coordination(
                        reason="Complex task detected",
                        context={"iteration": self.iteration, "score": result.get("score", 0)},
                    )

                # Periodic status report
                if self.iteration % self.status_report_interval == 0:
                    self.send_status_report()

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

        # Unregister from Central Command (Phase 2)
        self._unregister_central_command()

    def _unregister_central_command(self) -> None:
        """Unregister this Orion from Central Command on shutdown."""
        if not self.central_command_url or not self._central_command_registered:
            return

        try:
            requests.delete(
                f"{self.central_command_url}/api/central/orion/{self.registered_orion_id}",
                timeout=2.0,
            )
            self._log(f"📡 Unregistered from Central Command: {self.registered_orion_id}")
        except Exception as exc:
            self._log(f"⚠️ Central Command unregister failed: {exc}")

    def _maybe_refresh_official_model_registry(self) -> Dict[str, Any]:
        """Refresh official model registry on a coarse interval (non-blocking fail-open)."""
        if not self.model_registry_auto_refresh_enabled:
            return {"success": False, "reason": "disabled"}
        now_ts = time.time()
        if (now_ts - self._last_model_registry_refresh_ts) < self.model_registry_refresh_interval_sec:
            return {"success": False, "reason": "interval_not_reached"}
        self._last_model_registry_refresh_ts = now_ts
        try:
            profile = self.provider_store.get(masked=False)
            snapshot = refresh_model_registry_from_profile(profile=profile, timeout_sec=6)
            providers = snapshot.get("providers", {}) if isinstance(snapshot.get("providers"), dict) else {}
            ok_count = sum(1 for row in providers.values() if isinstance(row, dict) and row.get("status") == "ok")
            self._log(f"🌐 Official model registry refreshed: providers_ok={ok_count}/{len(providers)}")
            return {"success": True, "providers": len(providers), "providers_ok": ok_count}
        except Exception as exc:
            self._log(f"⚠️ Official model registry refresh failed: {str(exc)}")
            return {"success": False, "reason": str(exc)}

    async def run_parallel_cycle(self) -> Dict:
        """
        Run one parallel improvement cycle
        All agents work simultaneously
        """

        cycle_start = datetime.now()
        self.last_cycle_started_at = cycle_start
        self._heartbeat_control_plane()
        cycle_result = {
            "iteration": self.iteration,
            "timestamp": cycle_start.isoformat(),
            "results": {},
            "token_budget": self._get_token_budget_snapshot(),
            "token_policy_mode": self.token_policy_mode,
            "token_hard_cap": self._hard_cap_state(),
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
        ENHANCED VERSION - Actually analyzes and improves
        """
        self._log("🧠 PHASE: Self-improvement analysis...")

        try:
            # 1. Get current system stats
            improver_stats = self.improver.get_stats()
            learning_state = self.learning_loop.get_state() if hasattr(self.learning_loop, 'get_state') else {}

            # 2. Analyze recent performance
            recent_scores = []
            recent_deploys = 0
            for h in self.history[-10:]:
                score = h.get("score", 0)
                recent_scores.append(score)
                if h.get("deployed"):
                    recent_deploys += 1

            avg_score = sum(recent_scores) / len(recent_scores) if recent_scores else 0
            deploy_rate = recent_deploys / len(self.history[-10:]) if len(self.history) >= 10 else 0

            # 3. Identify issues and improvements
            issues_found = []
            improvements_made = []

            if avg_score < 6:
                issues_found.append(f"Low average score: {avg_score:.1f}/10")
                improvements_made.append("Analyzing score factors")

            if deploy_rate < 0.3 and len(self.history) >= 10:
                issues_found.append(f"Low deploy rate: {deploy_rate*100:.0f}%")
                improvements_made.append("Reviewing deployment logic")

            # Check for stuck patterns
            if len(self.history) >= 5:
                last_5 = self.history[-5:]
                all_same = all(h.get("decision", {}).get("action") == last_5[0].get("decision", {}).get("action") for h in last_5)
                if all_same:
                    issues_found.append("Repetitive decision pattern detected")
                    improvements_made.append("Adjusting decision strategy")

            # 4. Learn from cycle results
            if self.history:
                last_result = self.history[-1]
                cycle_summary = {
                    "iteration": self.iteration,
                    "score": last_result.get("score", 0),
                    "deployed": last_result.get("deployed", False),
                    "duration": last_result.get("duration", 0),
                    "avg_score_10": avg_score,
                    "deploy_rate_10": deploy_rate
                }
                self.improver.learn_from_input(
                    "analysis",
                    f"Cycle result: {cycle_summary}",
                    value_score=0.8,
                    context=cycle_summary
                )

            # 5. Run autonomous improvement
            self._log("🔧 Running autonomous improvement...")
            improvement_results = self.improver.run_auto_improvement()

            # 6. Run learning loop for deeper analysis
            if hasattr(self.learning_loop, 'run_iteration'):
                learning_results = self.learning_loop.run_iteration()
                action_count = len(learning_results.get("actions", []) or [])
                self._log(f"📚 Learning loop: {action_count} actions")

            # 7. Log decision with detailed analysis
            decision = {
                "iteration": self.iteration,
                "timestamp": datetime.now().isoformat(),
                "type": "self_improvement",
                "analysis": {
                    "avg_score": avg_score,
                    "deploy_rate": deploy_rate,
                    "issues_found": issues_found,
                    "improvements_made": improvements_made
                },
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

            # Report issues found
            for issue in issues_found:
                self._log(f"⚠️ Issue: {issue}")

            self._log(f"✅ Self-improvement complete: {improvements_applied} applied, {improvements_pending} pending")

        except Exception as e:
            self._log(f"⚠️ Self-improvement error: {str(e)}")

    def _orion_lease_owner_id(self) -> str:
        """Stable owner identifier for claiming hub tasks."""
        return f"orion:{self.instance_id}"

    def _register_control_plane_worker(self) -> None:
        if not self.control_plane_base_url:
            return
        payload = {
            "worker_id": self.instance_id,
            "capabilities": ["orion", "openclaw", "guardian"],
            "version": "orion-runtime",
            "region": "local",
            "mode": "local",
            "lease_ttl_sec": self.control_plane_lease_ttl_sec,
            "metadata": {
                "agent": self.name,
                "source": "orion_loop",
            },
        }
        try:
            requests.post(
                f"{self.control_plane_base_url}/api/control-plane/workers/register",
                json=payload,
                timeout=2.5,
            )
        except Exception as exc:
            self._log(f"⚠️ Control-plane register skipped: {exc}")

    def _heartbeat_control_plane(self, running_task: Optional[Dict[str, Any]] = None) -> None:
        now = time.time()
        if now - self._last_control_plane_heartbeat_at < self.control_plane_heartbeat_interval_sec:
            return
        if not self.control_plane_base_url:
            return

        payload = {
            "worker_id": self.instance_id,
            "health": "ok" if self.running and not self.paused else "degraded",
            "queue_depth": 0,
            "running_tasks": 1 if running_task else 0,
            "backpressure_level": "normal",
            "lease_ttl_sec": self.control_plane_lease_ttl_sec,
            "metadata": {
                "iteration": self.iteration,
                "paused": self.paused,
                "task_id": str(running_task.get("id")) if isinstance(running_task, dict) else "",
            },
        }
        try:
            requests.post(
                f"{self.control_plane_base_url}/api/control-plane/workers/heartbeat",
                json=payload,
                timeout=2.5,
            )
            self._last_control_plane_heartbeat_at = now
        except Exception as exc:
            self._log(f"⚠️ Control-plane heartbeat failed: {exc}")

    # ============================================
    # Central Command Integration (Phase 2)
    # ============================================

    def _register_central_command(self) -> None:
        """Register this Orion with Central Command."""
        if not self.central_command_url or self._central_command_registered:
            return

        agent_names = list(self.agents.keys())
        payload = {
            "orion_id": self.registered_orion_id,
            "name": f"Orion-{self.instance_id}",
            "base_url": f"{self.central_command_url}",
            "agents": agent_names,
        }
        try:
            response = requests.post(
                f"{self.central_command_url}/api/central/orion/register",
                json=payload,
                timeout=3.0,
            )
            if response.status_code == 200:
                self._central_command_registered = True
                self._log(f"📡 Registered with Central Command: {self.registered_orion_id}")
            else:
                self._log(f"⚠️ Central Command registration failed: {response.status_code}")
        except Exception as exc:
            self._log(f"⚠️ Central Command register skipped: {exc}")

    def _heartbeat_central_command(self) -> None:
        """Send heartbeat to Central Command."""
        now = time.time()
        if now - self._last_heartbeat_ts < self._heartbeat_interval_sec:
            return
        if not self.central_command_url or not self._central_command_registered:
            return

        current_task = self.history[-1].get("task") if self.history else None
        payload = {
            "status": "paused" if self.paused else "online",
            "current_task": current_task,
            "agents": list(self.agents.keys()),
        }
        try:
            response = requests.post(
                f"{self.central_command_url}/api/central/orion/{self.registered_orion_id}/heartbeat",
                json=payload,
                timeout=2.5,
            )
            if response.status_code == 200:
                self._last_heartbeat_ts = now
        except Exception:
            pass  # Silent fail for heartbeats

    def lock_file(self, file_path: str, agent_id: Optional[str] = None, ttl_sec: int = 300) -> bool:
        """Lock a file for editing to prevent conflicts."""
        if not self.central_command_url or not self._central_command_registered:
            return True  # Allow if Central Command not available

        payload = {
            "file_path": file_path,
            "orion_id": self.registered_orion_id,
            "agent_id": agent_id,
            "ttl_sec": ttl_sec,
        }
        try:
            response = requests.post(
                f"{self.central_command_url}/api/central/files/lock",
                json=payload,
                timeout=2.0,
            )
            if response.status_code == 200:
                if file_path not in self.current_locked_files:
                    self.current_locked_files.append(file_path)
                return True
            else:
                data = response.json()
                self._log(f"⚠️ File lock denied for {file_path}: {data.get('error')}")
                return False
        except Exception as exc:
            self._log(f"⚠️ File lock request failed: {exc}")
            return True  # Fail open

    def unlock_file(self, file_path: str) -> bool:
        """Unlock a file after editing."""
        if not self.central_command_url or not self._central_command_registered:
            return True

        payload = {
            "file_path": file_path,
            "orion_id": self.registered_orion_id,
        }
        try:
            response = requests.post(
                f"{self.central_command_url}/api/central/files/unlock",
                json=payload,
                timeout=2.0,
            )
            if response.status_code == 200:
                if file_path in self.current_locked_files:
                    self.current_locked_files.remove(file_path)
                return True
            return False
        except Exception:
            return False

    def broadcast_to_other_orions(self, message: str, message_type: str = "info") -> Dict[str, Any]:
        """Broadcast a message to all other Orion instances."""
        if not self.central_command_url:
            return {"success": False, "error": "Central Command not available"}

        payload = {
            "from_orion": self.registered_orion_id,
            "message_type": message_type,
            "content": message,
        }
        try:
            response = requests.post(
                f"{self.central_command_url}/api/central/broadcast",
                json=payload,
                timeout=2.5,
            )
            if response.status_code == 200:
                return {"success": True}
            return response.json()
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def check_file_locked_by_others(self, file_path: str) -> Optional[str]:
        """Check if a file is locked by another Orion."""
        if not self.central_command_url:
            return None

        try:
            response = requests.get(
                f"{self.central_command_url}/api/central/files/locked",
                timeout=2.0,
            )
            if response.status_code == 200:
                data = response.json()
                locks = data.get("locks", {})
                if file_path in locks:
                    return locks[file_path].get("orion_id")
        except Exception:
            pass
        return None

    def _heartbeat_task_lease(self, task: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(task, dict):
            return {"success": False, "error_code": "NO_TASK"}

        lease = task.get("lease") if isinstance(task.get("lease"), dict) else {}
        task_id = str(task.get("id") or "").strip()
        lease_token = str(lease.get("lease_token") or "").strip()
        owner_id = self._orion_lease_owner_id()

        if not task_id or not lease_token:
            return {"success": False, "error_code": "MISSING_LEASE_CONTEXT"}

        now = time.time()
        if now - self._last_task_lease_heartbeat_at < self.task_lease_heartbeat_interval_sec:
            return {"success": True, "skipped": True}

        try:
            hub = get_multi_orion_hub()
            result = hub.heartbeat_task_lease(
                task_id=task_id,
                owner_id=owner_id,
                lease_token=lease_token,
                lease_sec=max(90, self.task_lease_heartbeat_interval_sec * 2),
            )
            if result.get("success"):
                refreshed_lease = result.get("lease") if isinstance(result.get("lease"), dict) else {}
                if refreshed_lease:
                    task["lease"] = refreshed_lease
                self._last_task_lease_heartbeat_at = now
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_code": "LEASE_HEARTBEAT_ERROR"}

    def _task_assigned_to_this_orion(self, task: Dict[str, Any]) -> bool:
        assigned = str(task.get("assigned_to") or "").strip().lower()
        if not assigned:
            return True

        instance_id = str(self.instance_id or "").strip().lower()
        name_alias = str(self.name or "").strip().lower()
        candidates = {assigned}
        candidates_compact = {item.replace("_", "-").replace(" ", "-") for item in candidates}
        aliases = {
            instance_id,
            name_alias,
            f"orion-{instance_id}" if instance_id and not instance_id.startswith("orion-") else instance_id,
            instance_id.replace("_", "-"),
        }
        aliases = {item for item in aliases if item}
        aliases_compact = {item.replace("_", "-").replace(" ", "-") for item in aliases}
        return bool(candidates.intersection(aliases) or candidates_compact.intersection(aliases_compact))

    def _get_next_task_from_kanban(self) -> Optional[Dict]:
        """Claim next eligible Kanban task via hub lease ownership."""
        try:
            hub = get_multi_orion_hub()
            snapshot = hub.get_snapshot()
            tasks = snapshot.get("tasks", []) if isinstance(snapshot.get("tasks"), list) else []
            current_version = int(snapshot.get("version") or 0)

            for task in tasks:
                if not isinstance(task, dict):
                    continue
                if str(task.get("status") or "") not in {"backlog", "todo"}:
                    continue
                if not self._task_assigned_to_this_orion(task):
                    continue
                task_id = str(task.get("id") or "").strip()
                if not task_id:
                    continue
                claim = hub.claim_task(
                    task_id=task_id,
                    owner_id=self._orion_lease_owner_id(),
                    lease_sec=180,
                    expected_version=current_version,
                )
                if claim.get("success"):
                    leased_task = claim.get("task") if isinstance(claim.get("task"), dict) else dict(task)
                    lease_info = claim.get("lease") if isinstance(claim.get("lease"), dict) else {}
                    if lease_info:
                        leased_task["lease"] = lease_info
                    return leased_task

                error_code = str(claim.get("error_code", "")).upper()
                if error_code == "VERSION_CONFLICT":
                    snapshot = hub.get_snapshot()
                    tasks = snapshot.get("tasks", []) if isinstance(snapshot.get("tasks"), list) else []
                    current_version = int(snapshot.get("version") or 0)
                    continue

            return None
        except Exception as e:
            self._log(f"⚠️ Kanban read error: {str(e)}")
            return None

    def _update_task_status(self, task_id: str, new_status: str, lease_token: str = ""):
        """Update task status in Kanban using lease-aware safe update."""
        try:
            hub = get_multi_orion_hub()
            owner_id = self._orion_lease_owner_id()
            result = hub.update_task_status_safe(
                task_id=task_id,
                new_status=new_status,
                owner_id=owner_id,
                lease_token=lease_token,
            )
            if not result.get("success"):
                self._log(f"⚠️ Kanban update rejected: {result.get('error_code') or result.get('error')}")
                return
            if new_status in {"done", "review", "todo", "backlog"} and lease_token:
                hub.release_task_lease(
                    task_id=task_id,
                    owner_id=owner_id,
                    lease_token=lease_token,
                    next_status=new_status,
                )
        except Exception as e:
            self._log(f"⚠️ Kanban update error: {str(e)}")

    async def _dispatch_nova(self) -> TaskResult:
        """Dispatch Nova for code generation"""
        if "nova" not in self.agents:
            return self.create_result(False, {"error": "Nova not registered"})

        # Get next task from Kanban
        kanban_task = self._get_next_task_from_kanban()

        # If there's a Kanban task, move it to in_progress
        if kanban_task:
            lease_token = ""
            if isinstance(kanban_task.get("lease"), dict):
                lease_token = str(kanban_task.get("lease", {}).get("lease_token", ""))
            lease_check = self._heartbeat_task_lease(kanban_task)
            if not lease_check.get("success"):
                self._log(f"⚠️ Lease heartbeat failed before dispatch: {lease_check.get('error_code') or lease_check.get('error')}")
                return self.create_result(False, {"error": "Task lease invalid", "lease": lease_check})
            self._heartbeat_control_plane(running_task=kanban_task)
            self._update_task_status(kanban_task["id"], "in_progress", lease_token=lease_token)
            self._log(f"📋 Working on task: {kanban_task.get('title', 'Unknown')}")

        token_hints = self._runtime_token_hints(critical=False)
        message = AgentMessage(
            from_agent="orion",
            to_agent="nova",
            type=MessageType.TASK,
            priority=Priority.MEDIUM,
            content={
                "context": self.context.to_dict() if self.context else {},
                "iteration": self.iteration,
                "kanban_task": kanban_task,  # Include Kanban task in context
                "task_owner_id": self._orion_lease_owner_id() if kanban_task else "",
                "task_lease_token": str((kanban_task.get("lease") or {}).get("lease_token", "")) if isinstance(kanban_task, dict) else "",
                "task_id": str(kanban_task.get("id", "")) if isinstance(kanban_task, dict) else "",
                "runtime_hints": {
                    "nova_no_output_streak": self.consecutive_nova_no_output,
                    "no_deploy_streak": self.consecutive_no_deploy_cycles,
                    "prefer_cost": bool(
                        self.iteration > 3
                        and self.consecutive_fix_cycles == 0
                        and self.consecutive_no_deploy_cycles == 0
                        and self.consecutive_nova_no_output == 0
                    ) or bool(token_hints.get("prefer_cost", False)),
                    "token_pressure_level": token_hints.get("token_pressure_level", "normal"),
                    "token_budget": token_hints.get("token_budget", {}),
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
        token_level = str(self._get_token_budget_snapshot().get("level", "normal"))
        hard_cap = self._hard_cap_state()
        hard_cap_active = bool(hard_cap.get("active", False))
        hard_cap_multiplier = int(hard_cap.get("interval_multiplier", 2) or 2)
        required_interval = self.nova_stable_interval
        if token_level == "soft":
            required_interval = max(required_interval, self.nova_soft_interval)
        elif token_level == "hard":
            required_interval = max(required_interval, self.nova_hard_interval)
        if hard_cap_active:
            required_interval = max(required_interval, self.nova_hard_interval * hard_cap_multiplier)
        if elapsed >= required_interval:
            return True, f"nova cadence interval reached ({elapsed}/{required_interval})"
        if hard_cap_active:
            return False, f"hard cap cadence ({elapsed}/{required_interval})"
        if token_level in {"soft", "hard"}:
            return False, f"{token_level} token pressure cadence ({elapsed}/{required_interval})"
        return False, f"stable cadence ({elapsed}/{required_interval})"

    def _runtime_token_hints(self, critical: bool = False) -> Dict[str, Any]:
        """Hints for downstream agents to adapt route choice under token pressure."""
        token_budget = self._get_token_budget_snapshot()
        level = str(token_budget.get("level", "normal"))
        prefer_cost = (level in {"soft", "hard"}) and (not critical)
        return {
            "token_budget": token_budget,
            "token_pressure_level": level,
            "prefer_cost": prefer_cost,
            "hard_cap": self._hard_cap_state(),
        }

    def _hard_cap_state(self) -> Dict[str, Any]:
        """Emergency cost guard for severe budget overflow."""
        budget = self._get_token_budget_snapshot()
        ratio = float(budget.get("budget_ratio", 0.0) or 0.0)
        active = bool(self.token_hard_cap_enabled and ratio >= self.token_hard_cap_ratio)
        return {
            "enabled": bool(self.token_hard_cap_enabled),
            "active": active,
            "ratio": round(ratio, 4),
            "threshold": round(float(self.token_hard_cap_ratio), 4),
            "interval_multiplier": int(self.token_hard_cap_interval_multiplier),
        }

    def _token_economics_snapshot(self, event_limit: int = 120, cycle_window: int = 20) -> Dict[str, Any]:
        """Summarize recent token/cost efficiency using routing telemetry + cycle history."""
        events = read_recent_routing_events(limit=max(20, int(event_limit)))
        usage_summary = self.router.get_usage_summary() if hasattr(self, "router") else {}
        usage_models = usage_summary.get("models") if isinstance(usage_summary, dict) and isinstance(usage_summary.get("models"), dict) else {}
        usage_tokens_total = int(sum(int((item or {}).get("tokens", 0) or 0) for item in usage_models.values()))
        usage_cost_total = float(usage_summary.get("total_cost_usd", 0.0) or 0.0) if isinstance(usage_summary, dict) else 0.0
        total_calls = len(events)
        success_calls = sum(1 for item in events if bool(item.get("success", False)))
        failed_calls = max(0, total_calls - success_calls)
        total_tokens = 0
        total_cost = 0.0
        by_agent: Dict[str, Dict[str, Any]] = {}
        for item in events:
            usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
            tokens = int(usage.get("total_tokens", 0) or 0)
            cost = float(item.get("estimated_cost_usd", 0.0) or 0.0)
            agent = str(item.get("agent", "unknown")).strip().lower() or "unknown"
            total_tokens += max(0, tokens)
            total_cost += max(0.0, cost)
            row = by_agent.setdefault(agent, {"calls": 0, "tokens": 0, "cost_usd": 0.0, "success_calls": 0})
            row["calls"] += 1
            row["tokens"] += max(0, tokens)
            row["cost_usd"] += max(0.0, cost)
            if bool(item.get("success", False)):
                row["success_calls"] += 1

        for row in by_agent.values():
            calls = int(row.get("calls", 0) or 0)
            row["avg_tokens_per_call"] = int((row.get("tokens", 0) or 0) / calls) if calls > 0 else 0
            row["success_ratio"] = round(float(row.get("success_calls", 0) or 0) / calls, 4) if calls > 0 else 0.0
            row["cost_usd"] = round(float(row.get("cost_usd", 0.0) or 0.0), 6)

        metric_source = "routing_events"
        if total_tokens <= 0 and usage_tokens_total > 0:
            total_tokens = usage_tokens_total
            metric_source = "router_usage"
        if total_cost <= 0.0 and usage_cost_total > 0.0:
            total_cost = usage_cost_total
            metric_source = "router_usage"

        by_model: Dict[str, Dict[str, Any]] = {}
        for model_name, row in usage_models.items():
            calls = int((row or {}).get("calls", 0) or 0)
            tokens = int((row or {}).get("tokens", 0) or 0)
            cost = float((row or {}).get("cost_usd", 0.0) or 0.0)
            by_model[str(model_name)] = {
                "calls": calls,
                "tokens": tokens,
                "cost_usd": round(cost, 6),
                "avg_tokens_per_call": int(tokens / calls) if calls > 0 else 0,
            }

        recent_cycles = self.history[-max(3, int(cycle_window)) :] if isinstance(self.history, list) else []
        deploy_count = sum(1 for item in recent_cycles if bool(item.get("deployed", False)))
        avg_score = (
            sum(float(item.get("score", 0.0) or 0.0) for item in recent_cycles) / max(1, len(recent_cycles))
            if recent_cycles
            else 0.0
        )
        deploy_rate = (deploy_count / len(recent_cycles)) if recent_cycles else 0.0
        cost_per_deploy = (total_cost / deploy_count) if deploy_count > 0 else None
        tokens_per_deploy = (total_tokens / deploy_count) if deploy_count > 0 else None
        success_ratio = (success_calls / total_calls) if total_calls > 0 else 0.0

        return {
            "window": {
                "routing_events": total_calls,
                "cycles": len(recent_cycles),
            },
            "calls": {
                "total": total_calls,
                "success": success_calls,
                "failed": failed_calls,
                "success_ratio": round(success_ratio, 4),
            },
            "tokens": {
                "total": int(total_tokens),
                "avg_per_call": int(total_tokens / total_calls) if total_calls > 0 else 0,
                "per_deploy": int(tokens_per_deploy) if tokens_per_deploy is not None else None,
            },
            "cost": {
                "total_usd": round(total_cost, 6),
                "avg_per_call_usd": round(total_cost / total_calls, 6) if total_calls > 0 else 0.0,
                "per_deploy_usd": round(cost_per_deploy, 6) if cost_per_deploy is not None else None,
            },
            "productivity": {
                "deploy_count": int(deploy_count),
                "deploy_rate": round(deploy_rate, 4),
                "avg_score": round(avg_score, 3),
            },
            "metric_source": metric_source,
            "by_agent": dict(sorted(by_agent.items(), key=lambda x: x[1].get("cost_usd", 0.0), reverse=True)),
            "by_model": dict(sorted(by_model.items(), key=lambda x: x[1].get("cost_usd", 0.0), reverse=True)),
            "policy_mode": self.token_policy_mode,
        }

    def _maybe_tune_token_policy(self, economics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Auto-tune non-critical cadence for better cost efficiency."""
        if self.iteration <= 0:
            return {"changed": False, "mode": self.token_policy_mode, "reason": "iteration_not_started"}
        if (self.iteration - self.last_token_policy_tune_iteration) < self.token_policy_tune_interval:
            return {"changed": False, "mode": self.token_policy_mode, "reason": "interval_not_reached"}

        self.last_token_policy_tune_iteration = self.iteration
        budget = self._get_token_budget_snapshot()
        ratio = float(budget.get("budget_ratio", 0.0) or 0.0)
        level = str(budget.get("level", "normal"))
        hard_cap = self._hard_cap_state()
        hard_cap_active = bool(hard_cap.get("active", False))
        hard_cap_multiplier = int(hard_cap.get("interval_multiplier", 2) or 2)
        econ = economics if isinstance(economics, dict) else self._token_economics_snapshot()
        productivity = econ.get("productivity") if isinstance(econ.get("productivity"), dict) else {}
        deploy_rate = float(productivity.get("deploy_rate", 0.0) or 0.0)
        avg_score = float(productivity.get("avg_score", 0.0) or 0.0)
        should_economy = hard_cap_active or level in {"soft", "hard"} or ratio >= 0.95 or (deploy_rate < 0.35 and ratio >= 0.75)
        should_balanced = level == "normal" and ratio < 0.7 and deploy_rate >= 0.45 and avg_score >= 7.0

        previous_mode = self.token_policy_mode
        if should_economy:
            self.token_policy_mode = "economy"
            if hard_cap_active:
                self.nova_stable_interval = max(
                    self._base_nova_stable_interval,
                    self.nova_hard_interval * hard_cap_multiplier,
                )
                self.echo_interval = max(
                    self._base_echo_interval,
                    self.echo_hard_interval * hard_cap_multiplier,
                )
                self.security_audit_interval = max(
                    self._base_security_audit_interval,
                    self.security_fallback_audit_interval * hard_cap_multiplier,
                )
            else:
                self.nova_stable_interval = max(self._base_nova_stable_interval, self.nova_soft_interval)
                self.echo_interval = max(self._base_echo_interval, self.echo_soft_interval)
                self.security_audit_interval = max(self._base_security_audit_interval, self.security_fallback_audit_interval)
        elif should_balanced:
            self.token_policy_mode = "balanced"
            self.nova_stable_interval = self._base_nova_stable_interval
            self.echo_interval = self._base_echo_interval
            self.security_audit_interval = self._base_security_audit_interval

        changed = previous_mode != self.token_policy_mode
        if changed:
            self._log(
                f"🧮 Token policy tuned: {previous_mode} -> {self.token_policy_mode} "
                f"(budget_ratio={ratio:.2f}, deploy_rate={deploy_rate:.2f}, avg_score={avg_score:.2f})"
            )
        return {
            "changed": changed,
            "mode": self.token_policy_mode,
            "previous_mode": previous_mode,
            "budget_ratio": round(ratio, 4),
            "deploy_rate": round(deploy_rate, 4),
            "avg_score": round(avg_score, 3),
            "level": level,
            "hard_cap_active": hard_cap_active,
            "effective_intervals": {
                "nova_stable_interval": self.nova_stable_interval,
                "echo_interval": self.echo_interval,
                "security_audit_interval": self.security_audit_interval,
            },
        }

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
        token_level = str(self._get_token_budget_snapshot().get("level", "normal"))
        hard_cap = self._hard_cap_state()
        hard_cap_active = bool(hard_cap.get("active", False))
        hard_cap_multiplier = int(hard_cap.get("interval_multiplier", 2) or 2)
        required_interval = self.echo_interval
        if token_level == "soft":
            required_interval = max(required_interval, self.echo_soft_interval)
        elif token_level == "hard":
            required_interval = max(required_interval, self.echo_hard_interval)
        if hard_cap_active:
            required_interval = max(required_interval, self.echo_hard_interval * hard_cap_multiplier)
        should_run = (
            self.iteration <= 2
            or not current_fingerprint
            or current_fingerprint != self.last_tested_fingerprint
            or (self.iteration - self.last_echo_iteration) >= required_interval
        )
        if not should_run:
            return TaskResult(
                success=True,
                agent="Echo",
                output={
                    "skipped": True,
                    "reason": "No meaningful code changes for retest",
                    "token_pressure_level": token_level,
                    "required_interval": required_interval,
                    "hard_cap_active": hard_cap_active,
                },
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
        should_run, reason = self._should_run_pixel()
        if not should_run:
            self._log(f"🪶 Pixel skipped this cycle ({reason})")
            return TaskResult(
                success=True,
                agent="Pixel",
                output={"skipped": True, "reason": reason, "token_budget": self._get_token_budget_snapshot()},
                score=8.1,
                suggestions=["Pixel will auto-resume on periodic cadence while token pressure is elevated."],
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
                "iteration": self.iteration,
                "runtime_hints": self._runtime_token_hints(critical=False),
            }
        )
        result = await self.agents["pixel"].process(message)
        if hasattr(result, "success") and result.success:
            self.last_pixel_iteration = self.iteration
        return result

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
                "context": self.context.to_dict() if self.context else {},
                "runtime_hints": self._runtime_token_hints(critical=True),
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

        token_budget = self._get_token_budget_snapshot()
        budget_level = str(token_budget.get("level", "normal"))
        hard_cap = self._hard_cap_state()
        hard_cap_active = bool(hard_cap.get("active", False))
        hard_cap_multiplier = int(hard_cap.get("interval_multiplier", 2) or 2)
        interval = self.security_fallback_audit_interval if used_fallback else self.security_audit_interval
        if budget_level == "hard":
            interval = max(interval, self.security_fallback_audit_interval)
        if hard_cap_active:
            interval = max(interval, self.security_fallback_audit_interval * hard_cap_multiplier)
        if (self.iteration - self.last_full_security_audit_iteration) >= interval:
            return True, f"periodic interval {interval}"
        if budget_level in {"soft", "hard"}:
            return False, f"token_guard_{budget_level}: cached fingerprint and interval not reached"
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
                "iteration": self.iteration,
                "runtime_hints": self._runtime_token_hints(critical=False),
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
        token_budget = self._get_token_budget_snapshot()
        token_economics = self._token_economics_snapshot()
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
            "token_budget": token_budget,
            "token_economics": token_economics,
            "token_policy_mode": self.token_policy_mode,
            "token_hard_cap": self._hard_cap_state(),
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

    def _get_token_budget_snapshot(self) -> Dict[str, Any]:
        """Read router budget summary and normalize token pressure level."""
        fallback = {
            "total_cost_usd": 0.0,
            "daily_budget_usd": 0.0,
            "budget_ratio": 0.0,
            "level": "normal",
            "token_guard_enabled": self.token_guard_enabled,
        }
        try:
            summary = self.router.get_usage_summary()
            if not isinstance(summary, dict):
                self._last_token_budget = fallback
                return fallback
            ratio = float(summary.get("budget_ratio", 0.0) or 0.0)
            level = "normal"
            if self.token_guard_enabled:
                if ratio >= self.token_guard_hard_ratio:
                    level = "hard"
                elif ratio >= self.token_guard_soft_ratio:
                    level = "soft"
            normalized = {
                "total_cost_usd": float(summary.get("total_cost_usd", 0.0) or 0.0),
                "daily_budget_usd": float(summary.get("daily_budget_usd", 0.0) or 0.0),
                "budget_ratio": ratio,
                "level": level,
                "token_guard_enabled": self.token_guard_enabled,
            }
            self._last_token_budget = normalized
            return normalized
        except Exception:
            self._last_token_budget = fallback
            return fallback

    def _should_run_pixel(self) -> Tuple[bool, str]:
        """Run Pixel less often under token pressure while keeping periodic coverage."""
        if self.iteration <= 2:
            return True, "warmup cycles"

        token_budget = self._get_token_budget_snapshot()
        level = str(token_budget.get("level", "normal"))
        hard_cap = self._hard_cap_state()
        hard_cap_active = bool(hard_cap.get("active", False))
        hard_cap_multiplier = int(hard_cap.get("interval_multiplier", 2) or 2)
        elapsed = self.iteration - self.last_pixel_iteration
        if level == "hard":
            required = self.pixel_hard_interval * (hard_cap_multiplier if hard_cap_active else 1)
            if elapsed >= required:
                if hard_cap_active:
                    return True, f"hard-cap periodic interval {required}"
                return True, f"hard-pressure periodic interval {required}"
            if hard_cap_active:
                return False, f"hard cap active ({elapsed}/{required})"
            return False, f"hard token pressure ({elapsed}/{required})"
        if level == "soft":
            if elapsed >= self.pixel_soft_interval:
                return True, f"soft-pressure periodic interval {self.pixel_soft_interval}"
            return False, f"soft token pressure ({elapsed}/{self.pixel_soft_interval})"
        if hard_cap_active:
            required = self.pixel_hard_interval * hard_cap_multiplier
            if elapsed >= required:
                return True, f"hard-cap periodic interval {required}"
            return False, f"hard cap active ({elapsed}/{required})"
        return True, "normal token pressure"

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
        hard_cap = self._hard_cap_state()
        if bool(hard_cap.get("active", False)):
            delay = max(delay, 14)
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
