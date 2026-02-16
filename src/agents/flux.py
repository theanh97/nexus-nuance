"""
FLUX - DevOps & Deployment Engineer
Reliable deployment and monitoring
"""

import os
import json
import asyncio
import subprocess
from typing import Dict, Any, List
from pathlib import Path
from datetime import datetime

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, TaskResult


class Flux(AsyncAgent):
    """
    FLUX - DevOps & Deployment Engineer

    Responsibilities:
    - Deploy applications
    - Monitor health
    - Rollback on failure
    - Infrastructure management
    """

    def __init__(self):
        super().__init__(
            name="Flux",
            role="DevOps & Deployment Engineer",
            model=os.getenv("DEVOPS_MODEL", "glm-5"),
            api_key=os.getenv("GLM_API_KEY"),
            api_base=os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        )

        self.deploy_dir = Path("deployments")
        self.deploy_dir.mkdir(parents=True, exist_ok=True)

        self.current_version = 0
        self.deployment_history: List[Dict] = []

    async def process(self, message: AgentMessage) -> TaskResult:
        """Process deployment task"""

        self._log("🚀 Processing deployment task...")

        code = message.content.get("code", {})
        iteration = message.content.get("iteration", 1)

        # Pre-deployment checks
        pre_check = await self._pre_deployment_check(code)
        if not pre_check["passed"]:
            return self.create_result(
                False,
                {"error": "Pre-deployment check failed", "details": pre_check},
                issues=pre_check.get("issues", [])
            )

        # Deploy
        self.current_version += 1
        deployment = await self._deploy(code, iteration)

        if deployment["success"]:
            # Post-deployment verification
            verification = await self._verify_deployment()

            if not verification["healthy"]:
                # Rollback
                self._log("⚠️ Deployment unhealthy, rolling back...")
                await self._rollback()
                return self.create_result(
                    False,
                    {"error": "Deployment failed health check", "rolled_back": True}
                )

            # Success
            self.deployment_history.append({
                "version": self.current_version,
                "iteration": iteration,
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            })

            return self.create_result(
                True,
                {
                    "version": self.current_version,
                    "url": deployment.get("url", "http://localhost:3000"),
                    "deployed_at": datetime.now().isoformat()
                },
                score=10.0
            )

        else:
            return self.create_result(
                False,
                {"error": deployment.get("error", "Deployment failed")}
            )

    async def _pre_deployment_check(self, code: Dict) -> Dict:
        """Run pre-deployment checks"""

        issues = []

        files = code.get("files", [])

        # Check if we have files to deploy
        if not files:
            issues.append("No files to deploy")
            return {"passed": False, "issues": issues}

        # Check for required files
        has_index = any("index.html" in str(f) for f in files)
        if not has_index:
            issues.append("Missing index.html")

        return {
            "passed": len(issues) == 0,
            "issues": issues
        }

    async def _deploy(self, code: Dict, iteration: int) -> Dict:
        """Deploy the application"""

        try:
            # Create version directory
            version_dir = self.deploy_dir / f"v{self.current_version}"
            version_dir.mkdir(parents=True, exist_ok=True)

            # Copy files
            files = code.get("files", [])
            for file_path in files:
                if isinstance(file_path, str):
                    # File path from Nova output
                    src = Path(file_path)
                    if src.exists():
                        dst = version_dir / src.name
                        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            # Update app-output (serving directory)
            app_output = Path("app-output")
            app_output.mkdir(parents=True, exist_ok=True)

            # Copy to app-output for serving
            for file_path in files:
                if isinstance(file_path, str):
                    src = Path(file_path)
                    if src.exists():
                        dst = app_output / src.name
                        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            self._log(f"✅ Deployed version {self.current_version}")

            return {
                "success": True,
                "version": self.current_version,
                "url": "http://localhost:3000"
            }

        except Exception as e:
            self._log(f"❌ Deployment error: {str(e)}")
            return {"success": False, "error": str(e)}

    async def _verify_deployment(self) -> Dict:
        """Verify deployment health"""

        # Check if index.html exists in app-output
        index_path = Path("app-output/index.html")

        if not index_path.exists():
            return {"healthy": False, "reason": "index.html not found"}

        # Check file is not empty
        content = index_path.read_text(encoding="utf-8")
        if len(content) < 100:
            return {"healthy": False, "reason": "index.html too small"}

        return {"healthy": True}

    async def _rollback(self):
        """Rollback to previous version"""

        if self.current_version <= 1:
            self._log("⚠️ Cannot rollback - no previous version")
            return

        prev_version = self.current_version - 1
        prev_dir = self.deploy_dir / f"v{prev_version}"

        if prev_dir.exists():
            app_output = Path("app-output")

            # Copy previous version back
            for file in prev_dir.glob("*"):
                dst = app_output / file.name
                dst.write_text(file.read_text(encoding="utf-8"), encoding="utf-8")

            self._log(f"✅ Rolled back to version {prev_version}")
            self.current_version = prev_version

    def _get_system_prompt(self) -> str:
        return """You are FLUX, a reliable DevOps engineer.

Your principles:
- Zero-downtime deployment
- Always have a rollback plan
- Monitor everything
- Automate everything
- Security first

Deployment checklist:
1. Pre-deployment checks
2. Backup current version
3. Deploy new version
4. Health check
5. Monitor for issues
6. Rollback if needed"""

    def get_status(self) -> Dict:
        """Get deployment status"""
        return {
            "current_version": self.current_version,
            "deployments": len(self.deployment_history),
            "last_deployment": self.deployment_history[-1] if self.deployment_history else None
        }
