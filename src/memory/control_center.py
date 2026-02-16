"""
User Control Center
Manages user approvals, denials, and overrides.

THE CORE PRINCIPLE:
"User has FULL CONTROL at all times."
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
from collections import defaultdict
import threading


class ControlCenter:
    """
    Central hub for user control.
    Manages approvals, denials, overrides, and preferences.
    """

    # Control levels
    LEVEL_APPROVE = "APPROVE"     # User must approve everything
    LEVEL_NOTIFY = "NOTIFY"        # User notified, system acts
    LEVEL_AUTONOMOUS = "AUTONOMOUS"  # System acts, learns, reports

    # Approval status
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_EXPIRED = "expired"

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "control"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "control"

        self.approvals_file = self.base_path / "approvals.json"
        self.preferences_file = self.base_path / "control_preferences.json"
        self.overrides_file = self.base_path / "overrides.json"

        # Configuration
        self.approval_timeout_hours = int(os.getenv("APPROVAL_TIMEOUT_HOURS", "24"))

        # In-memory state
        self.pending_approvals: Dict[str, Dict] = {}
        self.approval_history: List[Dict] = []
        self.preferences: Dict[str, Any] = {}
        self.overrides: Dict[str, Any] = {}
        self.auto_approve_rules: Dict[str, bool] = {}

        # Callbacks
        self.approval_callbacks: List[Callable] = []
        self.denial_callbacks: List[Callable] = []

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load()

    def _load(self):
        """Load control center data."""
        if self.approvals_file.exists():
            try:
                with open(self.approvals_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.approval_history = data.get("history", [])
            except Exception:
                pass

        if self.preferences_file.exists():
            try:
                with open(self.preferences_file, 'r', encoding='utf-8') as f:
                    self.preferences = json.load(f)
            except Exception:
                pass

        if self.overrides_file.exists():
            try:
                with open(self.overrides_file, 'r', encoding='utf-8') as f:
                    self.overrides = json.load(f)
            except Exception:
                pass

        # Set default control level
        if "control_level" not in self.preferences:
            self.preferences["control_level"] = self.LEVEL_NOTIFY

    def _save(self):
        """Save control center data."""
        with self._lock:
            with open(self.approvals_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "history": self.approval_history[-1000:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

            with open(self.preferences_file, 'w', encoding='utf-8') as f:
                json.dump(self.preferences, f, indent=2)

            with open(self.overrides_file, 'w', encoding='utf-8') as f:
                json.dump(self.overrides, f, indent=2)

    # ==================== APPROVAL MANAGEMENT ====================

    def request_approval(
        self,
        action: str,
        details: Optional[Dict] = None,
        context: Optional[Dict] = None,
    ) -> str:
        """
        Request user approval for an action.

        Args:
            action: Action to approve
            details: Action details
            context: Additional context

        Returns:
            Approval request ID
        """
        with self._lock:
            # Check if auto-approved
            if self._should_auto_approve(action):
                self._record_approval(action, details, context, auto=True)
                return "auto_approved"

            # Check control level
            control_level = self.get_control_level()
            if control_level == self.LEVEL_AUTONOMOUS:
                return "autonomous"

            # Create approval request
            request_id = f"req_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.pending_approvals)}"

            request = {
                "id": request_id,
                "action": action,
                "details": details or {},
                "context": context or {},
                "status": self.STATUS_PENDING,
                "requested_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(hours=self.approval_timeout_hours)).isoformat(),
            }

            self.pending_approvals[request_id] = request

            # Log the request
            self._record_request(action, details, context)

            # Trigger callbacks
            for callback in self.approval_callbacks:
                try:
                    callback(action, request_id)
                except Exception:
                    pass

            self._save()
            return request_id

    def approve(
        self,
        request_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Approve a request.

        Args:
            request_id: Approval request ID
            reason: Optional reason

        Returns:
            True if approved successfully
        """
        with self._lock:
            if request_id == "auto_approved" or request_id == "autonomous":
                return True

            request = self.pending_approvals.get(request_id)
            if not request:
                return False

            request["status"] = self.STATUS_APPROVED
            request["approved_at"] = datetime.now().isoformat()
            request["reason"] = reason

            # Record in history
            self._record_approval(
                request["action"],
                request["details"],
                request["context"],
                request_id=request_id,
                reason=reason,
            )

            # Add to auto-approve rules if approved 3+ times
            action = request["action"]
            approval_count = self._get_approval_count(action)
            if approval_count >= 3:
                self.auto_approve_rules[action] = True

            # Trigger callbacks
            for callback in self.denial_callbacks:
                try:
                    callback(action, request_id, True)
                except Exception:
                    pass

            # Remove from pending
            del self.pending_approvals[request_id]

            self._save()
            return True

    def deny(
        self,
        request_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Deny a request.

        Args:
            request_id: Approval request ID
            reason: Reason for denial

        Returns:
            True if denied successfully
        """
        with self._lock:
            if request_id == "auto_approved" or request_id == "autonomous":
                return False

            request = self.pending_approvals.get(request_id)
            if not request:
                return False

            request["status"] = self.STATUS_DENIED
            request["denied_at"] = datetime.now().isoformat()
            request["reason"] = reason

            # Record in history
            self._record_denial(
                request["action"],
                request["details"],
                reason,
                request_id=request_id,
            )

            # Remove from auto-approve rules
            action = request["action"]
            if action in self.auto_approve_rules:
                del self.auto_approve_rules[action]

            # Trigger callbacks
            for callback in self.denial_callbacks:
                try:
                    callback(action, request_id, False)
                except Exception:
                    pass

            # Remove from pending
            del self.pending_approvals[request_id]

            self._save()
            return True

    # ==================== QUERY ====================

    def get_pending(self) -> List[Dict]:
        """Get all pending approvals."""
        with self._lock:
            # Clean up expired
            now = datetime.now()
            expired = []
            for rid, req in self.pending_approvals.items():
                expires = datetime.fromisoformat(req.get("expires_at", "2000-01-01"))
                if expires < now:
                    req["status"] = self.STATUS_EXPIRED
                    expired.append(rid)

            for rid in expired:
                del self.pending_approvals[rid]

            return list(self.pending_approvals.values())

    def get_pending_count(self) -> int:
        """Get count of pending approvals."""
        return len(self.get_pending())

    def get_history(
        self,
        action: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """Get approval history."""
        history = self.approval_history

        if action:
            history = [h for h in history if h.get("action") == action]
        if status:
            history = [h for h in history if h.get("status") == status]

        return history[-limit:]

    # ==================== OVERRIDES ====================

    def add_override(
        self,
        target: str,
        override_value: Any,
        reason: Optional[str] = None,
    ):
        """Add a user override."""
        with self._lock:
            self.overrides[target] = {
                "value": override_value,
                "reason": reason,
                "added_at": datetime.now().isoformat(),
            }
            self._save()

    def get_override(self, target: str) -> Optional[Any]:
        """Get override value."""
        override = self.overrides.get(target)
        if override:
            return override.get("value")
        return None

    def remove_override(self, target: str):
        """Remove an override."""
        if target in self.overrides:
            del self.overrides[target]
            self._save()

    # ==================== PREFERENCES ====================

    def set_control_level(self, level: str):
        """Set control level."""
        valid_levels = [self.LEVEL_APPROVE, self.LEVEL_NOTIFY, self.LEVEL_AUTONOMOUS]
        if level not in valid_levels:
            raise ValueError(f"Invalid level. Must be one of: {valid_levels}")

        self.preferences["control_level"] = level
        self._save()

    def get_control_level(self) -> str:
        """Get current control level."""
        return self.preferences.get("control_level", self.LEVEL_NOTIFY)

    def set_preference(self, key: str, value: Any):
        """Set a preference."""
        self.preferences[key] = value
        self._save()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a preference."""
        return self.preferences.get(key, default)

    # ==================== PRIVATE HELPERS ====================

    def _should_auto_approve(self, action: str) -> bool:
        """Check if action should be auto-approved."""
        return self.auto_approve_rules.get(action, False)

    def _get_approval_count(self, action: str) -> int:
        """Get approval count for an action."""
        return sum(
            1 for h in self.approval_history
            if h.get("action") == action and h.get("status") == self.STATUS_APPROVED
        )

    def _record_request(self, action: str, details: Dict, context: Dict):
        """Record approval request."""
        self.approval_history.append({
            "action": action,
            "details": details,
            "context": context,
            "status": self.STATUS_PENDING,
            "timestamp": datetime.now().isoformat(),
        })

    def _record_approval(
        self,
        action: str,
        details: Dict,
        context: Dict,
        request_id: Optional[str] = None,
        reason: Optional[str] = None,
        auto: bool = False,
    ):
        """Record approval."""
        self.approval_history.append({
            "action": action,
            "details": details,
            "context": context,
            "status": self.STATUS_APPROVED,
            "request_id": request_id,
            "reason": reason,
            "auto": auto,
            "timestamp": datetime.now().isoformat(),
        })

    def _record_denial(
        self,
        action: str,
        details: Dict,
        reason: Optional[str],
        request_id: Optional[str] = None,
    ):
        """Record denial."""
        self.approval_history.append({
            "action": action,
            "details": details,
            "status": self.STATUS_DENIED,
            "request_id": request_id,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        })

    # ==================== CALLBACKS ====================

    def on_approval_request(self, callback: Callable):
        """Register callback for approval requests."""
        self.approval_callbacks.append(callback)

    def on_decision(self, callback: Callable):
        """Register callback for approval/denial decisions."""
        self.denial_callbacks.append(callback)

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get control center statistics."""
        pending = self.get_pending()
        approved = sum(1 for h in self.approval_history if h.get("status") == self.STATUS_APPROVED)
        denied = sum(1 for h in self.approval_history if h.get("status") == self.STATUS_DENIED)

        return {
            "pending": len(pending),
            "approved": approved,
            "denied": denied,
            "control_level": self.get_control_level(),
            "auto_approve_rules": len(self.auto_approve_rules),
            "overrides": len(self.overrides),
        }

    def generate_report(self) -> str:
        """Generate control center report."""
        stats = self.get_stats()
        pending = self.get_pending()

        lines = [
            "🎛️ Control Center Report",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Control Level",
            f"- Current: {stats['control_level']}",
            "",
            "## Statistics",
            f"- Pending approvals: {stats['pending']}",
            f"- Approved: {stats['approved']}",
            f"- Denied: {stats['denied']}",
            f"- Auto-approve rules: {stats['auto_approve_rules']}",
            f"- Overrides: {stats['overrides']}",
            "",
        ]

        if pending:
            lines.append("## Pending Approvals")
            for p in pending:
                lines.append(f"- [{p['id']}] {p['action']}")
            lines.append("")

        # Auto-approve rules
        if self.auto_approve_rules:
            lines.append("## Auto-Approve Rules")
            for action in self.auto_approve_rules:
                lines.append(f"- {action}")
            lines.append("")

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_control_center = None


def get_control_center() -> ControlCenter:
    """Get singleton control center instance."""
    global _control_center
    if _control_center is None:
        _control_center = ControlCenter()
    return _control_center


def request_approval(action: str, details: Optional[Dict] = None) -> str:
    """Request user approval."""
    return get_control_center().request_approval(action, details)


def approve_request(request_id: str, reason: Optional[str] = None) -> bool:
    """Approve a request."""
    return get_control_center().approve(request_id, reason)


def deny_request(request_id: str, reason: Optional[str] = None) -> bool:
    """Deny a request."""
    return get_control_center().deny(request_id, reason)


def get_pending_approvals() -> List[Dict]:
    """Get pending approvals."""
    return get_control_center().get_pending()


def set_control_level(level: str):
    """Set control level."""
    get_control_center().set_control_level(level)


def get_control_level() -> str:
    """Get control level."""
    return get_control_center().get_control_level()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Control Center")
    print("=" * 50)

    cc = ControlCenter()

    # Test approval request
    print("\nRequesting approval...")
    req_id = cc.request_approval("deploy_to_production", {"risk": "high"})
    print(f"Request ID: {req_id}")

    # Get pending
    print("\nPending approvals:", cc.get_pending_count())

    # Approve
    if req_id not in ["auto_approved", "autonomous"]:
        cc.approve(req_id, "Approved for deployment")

    print("\n" + cc.generate_report())
