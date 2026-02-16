"""
GUARDIAN - The Supervisor Agent
Acts on behalf of the user to accept/decline permissions and monitor actions
"""

import os
import json
import asyncio
import subprocess
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, MessageType, TaskResult
from src.core.model_router import TaskType


class Permission:
    """Represents a permission request"""
    def __init__(self, request_id: str, action: str, details: Dict, risk_level: str = "medium"):
        self.request_id = request_id
        self.action = action
        self.details = details
        self.risk_level = risk_level  # low, medium, high, critical
        self.timestamp = datetime.now()
        self.decision = None  # None, "approved", "declined"
        self.reason = None


class Guardian(AsyncAgent):
    """
    GUARDIAN - The Supervisor Agent

    Responsibilities:
    - Monitor all agent activities
    - Accept/decline permission requests on behalf of user
    - View terminal output
    - Control computer when needed
    - Ensure safety and prevent harmful actions
    - Make decisions based on project context and user preferences
    """

    # Actions that ALWAYS need approval
    CRITICAL_ACTIONS = [
        "delete_files",
        "git_push_force",
        "git_reset_hard",
        "system_command",
        "install_packages",
        "modify_settings",
        "expose_network",
        "access_secrets"
    ]

    # Actions that are usually safe
    SAFE_ACTIONS = [
        "read_file",
        "write_file",
        "create_directory",
        "run_tests",
        "analyze_code",
        "generate_code",
        "take_screenshot",
        "browse_url"
    ]

    def __init__(self):
        super().__init__(
            name="Guardian",
            role="Supervisor & Permission Manager",
            model=os.getenv("GUARDIAN_MODEL", "glm-5"),  # Use GLM for decisions
            api_key=os.getenv("GLM_API_KEY"),
            api_base=os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        )

        # Pending permission requests
        self.pending_requests: Dict[str, Permission] = {}

        # Decision history
        self.decision_history: List[Dict] = []

        # User preferences (can be learned over time)
        self.user_preferences = {
            "auto_approve_safe": True,
            "auto_approve_medium": True,  # For trusted actions
            "always_ask_critical": False,  # Guardian decides based on context
            "allow_system_commands": True,
            "allow_git_operations": True,
            "allow_package_install": True
        }
        self.force_approve_all = os.getenv("GUARDIAN_FORCE_APPROVE_ALL", "true").lower() == "true"
        self.force_approve_reason = os.getenv(
            "GUARDIAN_FORCE_APPROVE_REASON",
            "User requested full autonomy; approve without confirmation.",
        )

        # Terminal monitoring
        self.terminal_buffer: List[str] = []
        self.max_buffer_size = 1000
        self.supervision_context: Dict[str, Any] = {
            "project_goal": "",
            "principles": [
                "Autonomous continuity first: keep Orion running unless user explicitly pauses.",
                "User-value first: surface high-signal information over noisy logs.",
                "Recover quickly from stuck states with safe interventions.",
                "Minimize risky actions while preserving project momentum.",
            ],
            "updated_at": datetime.now().isoformat(),
        }

    async def process(self, message: AgentMessage) -> TaskResult:
        """Process permission request or monitoring task"""

        if message.type == MessageType.TASK:
            action = message.content.get("action")

            if action == "permission_request":
                return await self._handle_permission_request(message.content)
            elif action == "terminal_update":
                return await self._handle_terminal_update(message.content)
            elif action == "activity_report":
                return await self._handle_activity_report(message.content)
            elif action == "get_status":
                return self.create_result(True, self._get_status())
            elif action == "orion_stuck":
                return await self._handle_orion_stuck(message.content)
            elif action == "command_intervention":
                return await self._handle_command_intervention(message.content)

        return self.create_result(False, {"error": "Unknown message type"})

    async def _handle_command_intervention(self, content: Dict) -> TaskResult:
        """Advise or decide for command approval prompts (yes/no/alternative)."""
        command = str(content.get("command", "")).strip()
        reason = str(content.get("reason", "permission_prompt")).strip()
        options = content.get("options") or []
        risk = self._assess_shell_command_risk(command)

        if self.force_approve_all:
            summary = {
                "decision": "approved",
                "recommended_action": "approve",
                "risk": risk,
                "reason": self.force_approve_reason,
                "safer_alternative": "",
                "command": command,
                "prompt_reason": reason,
                "available_options": options,
            }
            self._log("🧩 Command intervention: force-approved (full autonomy).")
            return self.create_result(True, summary)

        decision = "ask_user"
        recommended = "review"
        note = "Nên để user xác nhận thủ công."
        safer_alternative = ""

        if risk == "critical":
            decision = "decline"
            recommended = "deny"
            note = "Lệnh có rủi ro cao (xóa/ghi đè/hard reset). Guardian khuyến nghị KHÔNG chạy."
            safer_alternative = "Dùng dry-run hoặc lệnh chỉ đọc để kiểm tra trước."
        elif risk == "high":
            decision = "ask_user"
            recommended = "manual_review"
            note = "Lệnh có rủi ro đáng kể. Chỉ chạy khi đúng mục tiêu và đã backup."
            safer_alternative = "Chạy phiên bản scope hẹp hơn, thêm xác nhận hoặc backup."
        elif risk == "low":
            decision = "approved"
            recommended = "approve"
            note = "Lệnh rủi ro thấp. Có thể bấm tiếp tục."
        else:
            decision = "ask_user"
            recommended = "review"
            note = "Lệnh ở mức trung bình. Khuyến nghị kiểm tra ngữ cảnh rồi quyết định."

        summary = {
            "decision": decision,
            "recommended_action": recommended,
            "risk": risk,
            "reason": note,
            "safer_alternative": safer_alternative,
            "command": command,
            "prompt_reason": reason,
            "available_options": options,
        }
        self._log(f"🧩 Command intervention: risk={risk}, decision={decision}")
        return self.create_result(True, summary)

    async def _handle_orion_stuck(self, content: Dict) -> TaskResult:
        """Decide how to recover when Orion appears stuck or paused unexpectedly."""
        reason = str(content.get("reason", "unknown"))
        status = content.get("orion_status", {}) or {}
        paused = bool(status.get("paused"))
        pause_reason = str(status.get("pause_reason", "") or "").strip().lower()
        last_progress_age_sec = int(content.get("last_progress_age_sec", 0) or 0)
        paused_age_sec = int(content.get("paused_age_sec", 0) or 0)
        full_auto = bool(content.get("full_auto", False))
        full_auto_user_pause_max_sec = max(1, int(content.get("full_auto_user_pause_max_sec", 240) or 240))

        decision = "resume_orion"
        action = "resume"
        human_reason = f"Guardian recovery for {reason}"

        # Respect explicit user pause; do not auto-resume in that case.
        if paused and pause_reason in {"user", "manual_user"}:
            user_pause_timeout = (
                full_auto
                and (paused_age_sec >= full_auto_user_pause_max_sec or "paused_user_timeout" in reason.lower())
            )
            if user_pause_timeout:
                decision = "resume_orion"
                action = "resume"
                human_reason = (
                    f"Full-auto timeout reached ({paused_age_sec}s/{full_auto_user_pause_max_sec}s); "
                    "resuming Orion continuity."
                )
            else:
                decision = "wait_user"
                action = "none"
                human_reason = "Orion paused by user command; waiting for user resume."
        elif paused:
            decision = "resume_orion"
            action = "resume"
            human_reason = f"Orion paused unexpectedly ({pause_reason or 'unknown'}), resuming."
        elif last_progress_age_sec >= 120:
            decision = "nudge_orion"
            action = "nudge"
            human_reason = f"No meaningful progress for {last_progress_age_sec}s; nudging Orion loop."

        principle_hint = self.supervision_context.get("principles", [])
        if isinstance(principle_hint, list) and principle_hint:
            human_reason = f"{human_reason} | principle: {principle_hint[0]}"

        self._log(f"🛡️ Recovery decision: {decision} ({human_reason})")
        return self.create_result(
            success=True,
            output={
                "decision": decision,
                "action": action,
                "reason": human_reason,
                "last_progress_age_sec": last_progress_age_sec,
                "project_goal": self.supervision_context.get("project_goal", ""),
            },
        )

    async def _handle_permission_request(self, content: Dict) -> TaskResult:
        """
        Handle a permission request from another agent
        Returns approved or declined decision
        """

        request_id = content.get("request_id", str(datetime.now().timestamp()))
        action = content.get("action", "unknown")
        details = content.get("details", {})
        context = content.get("context", {})

        self._log(f"🔐 Permission request: {action}")

        # Create permission object
        permission = Permission(
            request_id=request_id,
            action=action,
            details=details,
            risk_level=self._assess_risk(action, details)
        )

        self.pending_requests[request_id] = permission

        # Make decision
        decision = await self._make_decision(permission, context)

        # Record decision
        permission.decision = decision["decision"]
        permission.reason = decision.get("reason", "")

        self.decision_history.append({
            "request_id": request_id,
            "action": action,
            "risk_level": permission.risk_level,
            "decision": permission.decision,
            "reason": permission.reason,
            "timestamp": datetime.now().isoformat()
        })

        # Remove from pending
        del self.pending_requests[request_id]

        self._log(f"{'✅ APPROVED' if permission.decision == 'approved' else '❌ DECLINED'}: {action}")
        if permission.reason:
            self._log(f"   Reason: {permission.reason}")

        return self.create_result(
            success=True,
            output={
                "request_id": request_id,
                "decision": permission.decision,
                "reason": permission.reason,
                "auto_decided": True
            }
        )

    async def _make_decision(self, permission: Permission, context: Dict) -> Dict:
        """
        Make an intelligent decision about the permission request
        Uses AI to analyze context and make appropriate decision
        """

        if self.force_approve_all:
            return {
                "decision": "approved",
                "reason": self.force_approve_reason
            }

        # Check user preferences first
        if permission.action in self.SAFE_ACTIONS and self.user_preferences["auto_approve_safe"]:
            return {
                "decision": "approved",
                "reason": f"Safe action: {permission.action}"
            }

        if permission.risk_level == "low":
            return {
                "decision": "approved",
                "reason": "Low risk action"
            }

        # For medium/high risk, use AI to decide
        if permission.risk_level in ["medium", "high", "critical"]:
            return await self._ai_decision(permission, context)

        # Default: approve
        return {
            "decision": "approved",
            "reason": "Default approval"
        }

    async def _ai_decision(self, permission: Permission, context: Dict) -> Dict:
        """
        Use AI to make a decision about the permission request
        """

        prompt = f"""You are GUARDIAN, a supervisor agent making decisions on behalf of the user.

PERMISSION REQUEST:
- Action: {permission.action}
- Risk Level: {permission.risk_level}
- Details: {json.dumps(permission.details, indent=2)}

PROJECT CONTEXT:
{json.dumps(context.get('project', {}), indent=2)[:1000]}

RECENT TERMINAL OUTPUT:
{chr(10).join(self.terminal_buffer[-20:])}

USER PREFERENCES:
{json.dumps(self.user_preferences, indent=2)}

Make a decision to APPROVE or DECLINE this request.

Consider:
1. Is this action necessary for the project?
2. Could this action cause harm or data loss?
3. Is this action consistent with the project goals?
4. Would the user approve this if they were here?

Respond in JSON:
{{
    "decision": "approved" or "declined",
    "reason": "Brief explanation of the decision",
    "confidence": 0.0-1.0,
    "conditions": ["any conditions to apply"] // optional
}}

Be protective but not paranoid. Approve actions that help the project.
Decline actions that are dangerous, unnecessary, or suspicious."""

        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": prompt}
        ]

        importance = "critical" if permission.risk_level in ["high", "critical"] else "normal"
        response = await self.call_api(
            messages,
            task_type=TaskType.PERMISSION_REVIEW,
            importance=importance
        )

        if "error" in response:
            # If AI fails, use conservative default
            if permission.risk_level == "critical":
                return {"decision": "declined", "reason": "AI decision failed, critical action declined for safety"}
            return {"decision": "approved", "reason": "AI decision failed, defaulting to approve"}

        result = self._parse_json_response(response)

        if result.get("parse_error"):
            # Conservative default
            return {"decision": "approved", "reason": "Could not parse AI response"}

        return {
            "decision": result.get("decision", "approved"),
            "reason": result.get("reason", "AI decision"),
            "confidence": result.get("confidence", 0.5)
        }

    def _assess_risk(self, action: str, details: Dict) -> str:
        """Assess the risk level of an action"""

        if action in self.CRITICAL_ACTIONS:
            # Check details for additional risk factors
            if "force" in str(details).lower():
                return "critical"
            if "delete" in str(details).lower():
                return "critical"
            if "rm -rf" in str(details).lower():
                return "critical"
            return "high"

        if action in self.SAFE_ACTIONS:
            return "low"

        return "medium"

    def _assess_shell_command_risk(self, command: str) -> str:
        text = str(command or "").strip().lower()
        if not text:
            return "medium"

        critical_patterns = [
            r"\brm\s+-rf\b",
            r"\bmkfs\b",
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+push\s+--force\b",
            r"\bchmod\s+777\b",
            r"\bsudo\b",
        ]
        high_patterns = [
            r"\bnpm\s+install\b",
            r"\bpip3?\s+install\b",
            r"\bbrew\s+install\b",
            r"\bcurl\b.*\|\s*(sh|bash)",
            r"\bpython3?\s+.*",
        ]
        low_patterns = [
            r"^curl\s+-s",
            r"^curl\s+--max-time",
            r"^lsof\b",
            r"^cat\b",
            r"^rg\b",
            r"^ls\b",
        ]

        for pattern in critical_patterns:
            if re.search(pattern, text):
                return "critical"
        for pattern in high_patterns:
            if re.search(pattern, text):
                return "high"
        for pattern in low_patterns:
            if re.search(pattern, text):
                return "low"
        return "medium"

    async def _handle_terminal_update(self, content: Dict) -> TaskResult:
        """Handle terminal output update"""

        output = content.get("output", "")
        lines = output.split("\n")

        # Add to buffer
        self.terminal_buffer.extend(lines)

        # Trim buffer
        if len(self.terminal_buffer) > self.max_buffer_size:
            self.terminal_buffer = self.terminal_buffer[-self.max_buffer_size:]

        # Check for concerning patterns
        concerns = self._check_for_concerns(lines)

        return self.create_result(
            success=True,
            output={"buffered": len(lines), "concerns": concerns}
        )

    def _check_for_concerns(self, lines: List[str]) -> List[Dict]:
        """Check terminal output for concerning patterns"""

        concerns = []
        concern_patterns = [
            ("error", "Error detected in output"),
            ("failed", "Failure detected"),
            ("permission denied", "Permission issue"),
            ("killed", "Process killed"),
            ("segfault", "Segmentation fault"),
            ("exception", "Exception thrown")
        ]

        for line in lines:
            line_lower = line.lower()
            for pattern, message in concern_patterns:
                if pattern in line_lower:
                    concerns.append({
                        "pattern": pattern,
                        "message": message,
                        "line": line[:100]
                    })

        return concerns

    async def _handle_activity_report(self, content: Dict) -> TaskResult:
        """Handle activity report from another agent"""

        agent = content.get("agent", "unknown")
        activity = content.get("activity", "unknown")

        self._log(f"📋 Activity report from {agent}: {activity}")

        # Store for decision context
        if not hasattr(self, 'activity_log'):
            self.activity_log = []

        self.activity_log.append({
            "agent": agent,
            "activity": activity,
            "timestamp": datetime.now().isoformat()
        })

        return self.create_result(True, {"logged": True})

    def _get_system_prompt(self) -> str:
        return """You are GUARDIAN, the supervisor and protector of the autonomous development system.

Your responsibilities:
1. Make intelligent decisions about permission requests
2. Protect the system from harmful actions
3. Enable productivity by approving helpful actions
4. Learn from context to make better decisions

Decision principles:
- APPROVE actions that:
  * Are necessary for the project
  * Follow best practices
  * Don't cause data loss
  * Are consistent with project goals

- DECLINE actions that:
  * Could cause data loss
  * Are unnecessary or wasteful
  * Could harm the system
  * Seem suspicious or malicious

You are NOT paranoid, but you ARE careful.
The user trusts you to make good decisions on their behalf.
Make decisions quickly and confidently."""

    def _get_status(self) -> Dict:
        """Get guardian status"""
        return {
            "pending_requests": len(self.pending_requests),
            "total_decisions": len(self.decision_history),
            "recent_decisions": self.decision_history[-10:],
            "user_preferences": self.user_preferences,
            "terminal_buffer_size": len(self.terminal_buffer),
            "supervision_context": self.supervision_context,
        }

    def get_decision_history(self, limit: int = 50) -> List[Dict]:
        """Get recent decision history"""
        return self.decision_history[-limit:]

    def update_preferences(self, preferences: Dict):
        """Update user preferences"""
        self.user_preferences.update(preferences)
        self._log(f"📝 Updated preferences: {preferences}")

    def update_supervision_context(self, project_goal: str, principles: Optional[List[str]] = None):
        """Update high-level supervision context so Guardian acts as delegated operator."""
        if project_goal:
            self.supervision_context["project_goal"] = str(project_goal)
        if principles:
            self.supervision_context["principles"] = [str(item) for item in principles if str(item).strip()]
        self.supervision_context["updated_at"] = datetime.now().isoformat()
        self._log("🧭 Supervision context updated from user delegation")


# Permission helper function for other agents to use
async def request_permission(
    guardian: Guardian,
    action: str,
    details: Dict,
    context: Dict = None
) -> Dict:
    """
    Request permission from Guardian

    Usage:
        result = await request_permission(
            guardian,
            "delete_files",
            {"files": ["temp.log"]},
            {"project": project_context}
        )

        if result["decision"] == "approved":
            # Proceed with action
        else:
            # Don't proceed
    """

    message = AgentMessage(
        from_agent="requester",
        to_agent="guardian",
        type=MessageType.TASK,
        content={
            "action": "permission_request",
            "request_id": str(datetime.now().timestamp()),
            "action_type": action,
            "action": action,
            "details": details,
            "context": context or {}
        }
    )

    result = await guardian.process(message)
    return result.output if result.success else {"decision": "declined", "reason": "Request failed"}
