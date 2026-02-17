"""
CIPHER - Security Master & Code Reviewer
Paranoid security expert with VETO power
"""

import os
import json
import re
from typing import Dict, Any, List

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, TaskResult
from src.core.model_router import TaskType


class Cipher(AsyncAgent):
    """
    CIPHER - Security Master & Code Reviewer

    Responsibilities:
    - Review all code changes
    - Security audit (OWASP Top 10)
    - Challenge decisions
    - VETO power on security issues
    - Best practices enforcement
    """

    # OWASP Top 10 patterns to check
    SECURITY_PATTERNS = {
        "sql_injection": [
            r"execute\s*\(",
            r"exec\s*\(",
            r"\+\s*['\"]SELECT",
            r"f['\"].*SELECT.*{",
            r"\.format\s*\(.*SELECT"
        ],
        "xss": [
            r"innerHTML\s*=",
            r"document\.write\s*\(",
            r"eval\s*\(",
            r"v-html\s*=",
            r"dangerouslySetInnerHTML"
        ],
        "hardcoded_secrets": [
            r"password\s*=\s*['\"][^'\"]+['\"]",
            r"api_key\s*=\s*['\"][^'\"]+['\"]",
            r"secret\s*=\s*['\"][^'\"]+['\"]",
            r"token\s*=\s*['\"][^'\"]+['\"]"
        ],
        "insecure_random": [
            r"Math\.random\(\)",
            r"random\.random\(\)"
        ],
        "unsafe_redirect": [
            r"window\.location\s*=",
            r"location\.href\s*=",
            r"redirect\s*\("
        ]
    }

    def __init__(self):
        super().__init__(
            name="Cipher",
            role="Security Master & Code Reviewer",
            model=os.getenv("SECURITY_MODEL", "glm-5"),
            api_key=os.getenv("GLM_API_KEY"),
            api_base=os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        )

        self.veto_enabled = True

    async def process(self, message: AgentMessage) -> TaskResult:
        """Process security review"""

        self._log("🔐 Processing security review...")

        code = self._normalize_code_payload(message.content.get("code", {}))
        context = message.content.get("context", {})
        runtime_hints = message.content.get("runtime_hints", {})
        if not isinstance(runtime_hints, dict):
            runtime_hints = {}

        # Run automated security checks
        auto_issues = self._automated_checks(code)

        # Run AI-powered deep review
        importance = message.priority.value if hasattr(message, "priority") else "normal"
        ai_review = await self._ai_review(
            code,
            context,
            auto_issues,
            importance,
            prefer_cost=bool(runtime_hints.get("prefer_cost", False)),
        )

        # Combine results
        all_issues = auto_issues + ai_review.get("issues", [])
        suggestions = ai_review.get("suggestions", [])

        # Determine if veto
        critical_issues = [i for i in all_issues if i.get("severity") == "critical"]
        veto = len(critical_issues) > 0

        veto_reason = None
        if veto:
            veto_reason = f"Critical security issues found: {len(critical_issues)}"
            self._log(f"🚫 VETO: {veto_reason}")

        # Calculate security score
        score = self._calculate_score(all_issues)

        return self.create_result(
            success=True,
            output={
                "security_score": score,
                "issues": all_issues,
                "passed": not veto
            },
            score=score,
            issues=[i.get("description", str(i)) for i in all_issues],
            suggestions=suggestions,
            veto=veto and self.veto_enabled,
            veto_reason=veto_reason
        )

    def _normalize_code_payload(self, code: Any) -> Dict[str, Any]:
        """Normalize arbitrary payloads into {'files': {...}} shape."""
        if isinstance(code, dict):
            files = code.get("files")
            if isinstance(files, dict):
                normalized = {str(k): str(v) for k, v in files.items() if isinstance(v, (str, int, float))}
                return {"files": normalized}
            # If dict is already file-like mapping, wrap it.
            file_like = {str(k): str(v) for k, v in code.items() if isinstance(v, (str, int, float))}
            if file_like:
                return {"files": file_like}
            return {"files": {}}

        if isinstance(code, list):
            files: Dict[str, str] = {}
            for idx, item in enumerate(code):
                if isinstance(item, str):
                    files[f"generated_{idx + 1}.txt"] = item
                elif isinstance(item, dict):
                    path = str(item.get("path", f"generated_{idx + 1}.txt"))
                    content = str(item.get("content", ""))
                    files[path] = content
            return {"files": files}

        if isinstance(code, str):
            return {"files": {"generated_output.txt": code}}

        return {"files": {}}

    def _automated_checks(self, code: Dict) -> List[Dict]:
        """Run automated security pattern checks"""

        issues = []

        for file_path, content in code.get("files", {}).items():
            if not isinstance(content, str):
                continue

            # Check each security pattern category
            for category, patterns in self.SECURITY_PATTERNS.items():
                for pattern in patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    if matches:
                        severity = "critical" if category in ["sql_injection", "xss", "hardcoded_secrets"] else "warning"
                        issues.append({
                            "type": "automated",
                            "category": category,
                            "file": file_path,
                            "severity": severity,
                            "description": f"Potential {category.replace('_', ' ')} detected",
                            "matches": matches[:3]  # Limit matches
                        })

        return issues

    async def _ai_review(
        self,
        code: Dict,
        context: Dict,
        existing_issues: List,
        importance: str = "normal",
        prefer_cost: bool = False,
    ) -> Dict:
        """AI-powered deep security review"""

        prompt = f"""You are CIPHER, a paranoid security expert reviewing code.

CODE TO REVIEW:
{json.dumps(code.get('files', {}), indent=2)[:3000]}

EXISTING AUTOMATED FINDINGS:
{json.dumps(existing_issues, indent=2)}

Perform a thorough security review. Check for:

1. OWASP Top 10:
   - Injection flaws
   - Broken authentication
   - Sensitive data exposure
   - XML External Entities
   - Broken access control
   - Security misconfiguration
   - Cross-site scripting
   - Insecure deserialization
   - Known vulnerabilities
   - Insufficient logging

2. Code Quality:
   - Error handling
   - Input validation
   - Output encoding
   - Secure defaults

3. Best Practices:
   - CORS configuration
   - CSP headers
   - HTTPS enforcement
   - Cookie security

Respond in JSON:
{{
    "issues": [
        {{
            "type": "ai_detected",
            "severity": "critical|warning|info",
            "description": "Description of the issue",
            "file": "affected file",
            "line": "approximate line",
            "fix": "How to fix it"
        }}
    ],
    "suggestions": [
        "Security improvement 1",
        "Security improvement 2"
    ],
    "overall_assessment": "Brief summary"
}}

Be THOROUGH and PARANOID. Better safe than sorry."""

        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": prompt}
        ]

        response = await self.call_api(
            messages,
            task_type=TaskType.SECURITY_AUDIT,
            importance="critical" if importance in {"high", "critical"} else importance,
            prefer_cost=prefer_cost,
        )

        if "error" in response:
            return {"issues": [], "suggestions": []}

        result = self._parse_json_response(response)

        if result.get("parse_error"):
            return {"issues": [], "suggestions": []}

        return result

    def _get_system_prompt(self) -> str:
        return """You are CIPHER, the most paranoid security expert in the world.

Your job:
1. Find EVERY security vulnerability
2. Challenge EVERY assumption
3. Question EVERY design decision
4. Never accept "it's probably fine"
5. Think like an attacker

Your motto: "Trust nothing. Verify everything."

When reviewing code:
- Assume the worst
- Look for edge cases
- Consider all attack vectors
- Think about what could go wrong
- Remember: users are unpredictable, attackers are creative

You have VETO POWER. Use it wisely but use it.
If there's a critical security issue, you MUST block the deployment.

Be paranoid. Be thorough. Be right."""

    def _calculate_score(self, issues: List[Dict]) -> float:
        """Calculate security score based on issues"""

        score = 10.0

        for issue in issues:
            severity = issue.get("severity", "info")
            if severity == "critical":
                score -= 3.0
            elif severity == "warning":
                score -= 1.0
            else:
                score -= 0.2

        return max(score, 0.0)
