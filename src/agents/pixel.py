"""
PIXEL - UI/UX Visionary & Tester
Design genius with Steve Jobs-level aesthetics
"""

import os
import json
import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, TaskResult
from src.core.model_router import TaskType, TaskComplexity


class Pixel(AsyncAgent):
    """
    PIXEL - UI/UX Visionary & Tester

    Responsibilities:
    - Analyze UI/UX from screenshots
    - Evaluate design quality
    - Suggest improvements
    - Ensure accessibility
    - VETO power on critical UX issues
    """

    def __init__(self):
        super().__init__(
            name="Pixel",
            role="UI/UX Visionary & Tester",
            model=os.getenv("VISION_MODEL", "glm-4.6v"),  # GLM-4.6V for UI analysis
            api_key=os.getenv("GLM_API_KEY"),
            api_base=os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        )

        # Fallback to Gemini if GLM vision fails
        self.fallback_model = "gemini-2.0-flash"
        self.fallback_api_key = os.getenv("GOOGLE_API_KEY")

        self.screenshot_dir = Path("screenshots")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self.veto_threshold = 4.0  # VETO if score below this

    async def process(self, message: AgentMessage) -> TaskResult:
        """Process UI/UX analysis task"""

        self._log("🎨 Processing UI/UX analysis...")

        screenshot_path = message.content.get("screenshot")
        context = message.content.get("context", {})
        iteration = message.content.get("iteration", 1)
        runtime_hints = message.content.get("runtime_hints", {})
        if not isinstance(runtime_hints, dict):
            runtime_hints = {}

        # If no screenshot, analyze based on code
        if not screenshot_path:
            return await self._analyze_from_context(context, iteration)

        # Analyze screenshot
        try:
            importance = message.priority.value if hasattr(message, "priority") else "normal"
            analysis = await self._analyze_screenshot(
                screenshot_path,
                context,
                importance,
                prefer_cost=bool(runtime_hints.get("prefer_cost", False)),
            )

            overall_score = analysis.get("overall_score", 5.0)

            # Check for veto
            veto = overall_score < self.veto_threshold
            veto_reason = None
            if veto:
                veto_reason = f"UI score too low: {overall_score}/10. Critical issues must be fixed."
                self._log(f"🚫 VETO triggered: {veto_reason}")

            return self.create_result(
                success=True,
                output=analysis,
                score=overall_score,
                issues=analysis.get("issues", []),
                suggestions=analysis.get("suggestions", []),
                veto=veto,
                veto_reason=veto_reason
            )

        except Exception as e:
            self._log(f"❌ Analysis failed: {str(e)}")
            return self.create_result(False, {"error": str(e)}, score=5.0)

    async def _analyze_screenshot(
        self,
        screenshot_path: str,
        context: Dict,
        importance: str = "normal",
        prefer_cost: bool = False,
    ) -> Dict:
        """Analyze UI screenshot using vision model"""

        prompt = self._get_analysis_prompt(context)
        allow_subscription_primary = self.subscription_primary_routing_enabled and (
            not self.subscription_primary_cost_only or importance == "low"
        )

        # Route vision models by policy.
        route_chain = self.router.get_fallback_chain(
            task_type=TaskType.UI_ANALYSIS,
            complexity=TaskComplexity.MEDIUM,
            importance=importance,
            prefer_speed=(importance == "low"),
            prefer_cost=prefer_cost,
            runtime_only=not allow_subscription_primary,
        )
        if not route_chain:
            route_chain = []

        # Backward compatible hard fallback list if router has no candidates
        if not route_chain:
            route_chain = []
            if self.api_key:
                route_chain.append(type("Cfg", (), {"name": self.model, "api_base": self.api_base}))
            if self.fallback_api_key:
                route_chain.append(type("Cfg", (), {"name": self.fallback_model, "api_base": "https://generativelanguage.googleapis.com/v1beta"}))

        old_model = self.model
        old_key = self.api_key
        old_base = self.api_base
        response: Dict[str, Any] = {"error": "vision_route_failed"}

        for cfg in route_chain:
            self.model = cfg.name
            self.api_base = getattr(cfg, "api_base", self.api_base)
            routed_key = self.router.get_api_key(cfg) if hasattr(cfg, "api_source") else None
            if routed_key:
                self.api_key = routed_key
            elif "gemini" in self.model.lower() and self.fallback_api_key:
                self.api_key = self.fallback_api_key

            if getattr(cfg, "supports_api", True):
                self._log(f"🧭 Vision route -> {self.model}")
                response = await self.call_vision_api(screenshot_path, prompt, importance=importance)
            else:
                self._log(f"🧭 Vision route -> subscription:{self.model}")
                response = await self._call_subscription_vision(
                    cfg=cfg,
                    screenshot_path=screenshot_path,
                    prompt=prompt,
                )
            if "error" not in response:
                break

        self.model = old_model
        self.api_key = old_key
        self.api_base = old_base

        # Parse response
        analysis = self._parse_json_response(response)

        if analysis.get("parse_error"):
            # Return default analysis
            return self._default_analysis()

        return analysis

    async def _call_subscription_vision(self, cfg, screenshot_path: str, prompt: str) -> Dict[str, Any]:
        """
        Vision fallback through subscription CLI.
        It passes screenshot path in prompt so CLI-based agents can inspect local file if supported.
        """
        absolute_path = str(Path(screenshot_path).resolve())
        vision_prompt = (
            f"{prompt}\n\n"
            "Analyze this screenshot file from local path and respond with the required JSON only.\n"
            f"Screenshot path: {absolute_path}"
        )
        return await self._call_single_subscription_model(
            cfg=cfg,
            messages=[{"role": "user", "content": vision_prompt}],
            estimated_tokens=max(800, len(vision_prompt) // 4),
        )

    async def _analyze_from_context(self, context: Dict, iteration: int) -> TaskResult:
        """Analyze UI based on code context when no screenshot available"""

        self._log("📋 Analyzing from code context...")

        files = context.get("files", {})

        # Analyze HTML/CSS if available
        issues = []
        suggestions = []

        html_content = files.get("index.html", "")

        # Check for common issues
        if "<!DOCTYPE html>" not in html_content:
            issues.append("Missing DOCTYPE declaration")

        if 'lang=' not in html_content:
            issues.append("Missing language attribute on HTML element")

        if 'aria-' not in html_content.lower():
            suggestions.append("Add ARIA attributes for accessibility")

        if '<meta name="viewport"' not in html_content:
            issues.append("Missing viewport meta tag for responsive design")

        # Calculate score based on findings
        base_score = 8.0
        base_score -= len(issues) * 0.5
        base_score = max(base_score, 3.0)

        return self.create_result(
            success=True,
            output={"analysis_type": "code_based", "issues": issues, "suggestions": suggestions},
            score=base_score,
            issues=issues,
            suggestions=suggestions
        )

    def _get_analysis_prompt(self, context: Dict) -> str:
        project_goal = context.get("project_goal", "Web application")

        return f"""You are PIXEL, a UI/UX visionary with Steve Jobs-level design sensibility.

Analyze this UI screenshot for: {project_goal}

Evaluate on these criteria (0-10 each):
1. VISUAL HIERARCHY - Is information organized logically?
2. COLOR HARMONY - Do colors work well together?
3. TYPOGRAPHY - Is text readable and well-styled?
4. SPACING & LAYOUT - Is whitespace used effectively?
5. ACCESSIBILITY - Can all users access the content?
6. USER FLOW - Is navigation intuitive?
7. RESPONSIVENESS - Does it adapt to different screens?
8. INNOVATION - Is there creative design thinking?
9. CONSISTENCY - Are design elements uniform?
10. POLISH - Is everything pixel-perfect?

Respond in JSON format:
{{
    "overall_score": 0-10,
    "scores": {{
        "visual_hierarchy": 0-10,
        "color_harmony": 0-10,
        "typography": 0-10,
        "spacing_layout": 0-10,
        "accessibility": 0-10,
        "user_flow": 0-10,
        "responsiveness": 0-10,
        "innovation": 0-10,
        "consistency": 0-10,
        "polish": 0-10
    }},
    "issues": [
        "Specific issue 1",
        "Specific issue 2"
    ],
    "suggestions": [
        "Specific improvement 1",
        "Specific improvement 2"
    ],
    "positive_aspects": [
        "What's working well"
    ]
}}

Be CRITICAL but CONSTRUCTIVE. Focus on actionable feedback.
If something is truly terrible, say so. If it's excellent, acknowledge it.
Steve Jobs would not accept mediocrity. Neither should you."""

    def _default_analysis(self) -> Dict:
        return {
            "overall_score": 5.0,
            "scores": {
                "visual_hierarchy": 5.0,
                "color_harmony": 5.0,
                "typography": 5.0,
                "spacing_layout": 5.0,
                "accessibility": 5.0,
                "user_flow": 5.0,
                "responsiveness": 5.0,
                "innovation": 5.0,
                "consistency": 5.0,
                "polish": 5.0
            },
            "issues": ["Could not complete vision analysis"],
            "suggestions": ["Try again with a clearer screenshot"],
            "positive_aspects": []
        }

    def _calculate_overall_score(self, scores: Dict) -> float:
        """Calculate weighted overall score"""
        weights = {
            "visual_hierarchy": 1.5,
            "color_harmony": 1.0,
            "typography": 1.0,
            "spacing_layout": 1.5,
            "accessibility": 1.5,  # Higher weight for accessibility
            "user_flow": 1.5,
            "responsiveness": 1.0,
            "innovation": 0.5,
            "consistency": 1.0,
            "polish": 1.0
        }

        weighted_sum = 0
        total_weight = 0

        for criterion, score in scores.items():
            weight = weights.get(criterion, 1.0)
            weighted_sum += score * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 5.0
