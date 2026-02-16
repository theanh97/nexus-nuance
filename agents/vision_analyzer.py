"""
Vision Analyzer Agent
Uses Claude/Gemini for UI/UX analysis from screenshots
"""

import os
import base64
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import requests


@dataclass
class UIAnalysis:
    """UI/UX analysis result"""
    overall_score: float
    layout_score: float
    color_score: float
    typography_score: float
    usability_issues: List[str]
    design_suggestions: List[str]
    accessibility_concerns: List[str]
    positive_aspects: List[str]


class ClaudeVisionAgent:
    """Claude Opus 4.6 for detailed UI/UX analysis"""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.model = "claude-opus-4-6-20250514"
        self.api_base = "https://api.anthropic.com/v1"

    def analyze_screenshot(self, screenshot_path: str, context: str = "") -> UIAnalysis:
        """Analyze a screenshot for UI/UX quality"""

        # Read and encode image
        with open(screenshot_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

        prompt = f"""Analyze this UI screenshot for a web application.

        Context: {context}

        Provide a detailed analysis in JSON format:
        {{
            "overall_score": 0-10,
            "layout_score": 0-10,
            "color_score": 0-10,
            "typography_score": 0-10,
            "usability_issues": ["issue1", "issue2"],
            "design_suggestions": ["suggestion1", "suggestion2"],
            "accessibility_concerns": ["concern1"],
            "positive_aspects": ["positive1"]
        }}

        Be critical but constructive. Focus on actionable improvements.
        """

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        }

        response = requests.post(
            f"{self.api_base}/messages",
            headers=headers,
            json=payload
        )

        result = response.json()
        content = result.get('content', [{}])[0].get('text', '{}')

        try:
            parsed = json.loads(content)
            return UIAnalysis(**parsed)
        except:
            return UIAnalysis(
                overall_score=5.0,
                layout_score=5.0,
                color_score=5.0,
                typography_score=5.0,
                usability_issues=["Failed to parse analysis"],
                design_suggestions=[],
                accessibility_concerns=[],
                positive_aspects=[]
            )


class GeminiVisionAgent:
    """Google Gemini for fast visual feedback"""

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.model = "gemini-2.0-flash"
        self.api_base = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"

    def analyze_screenshot(self, screenshot_path: str, context: str = "") -> UIAnalysis:
        """Quick UI/UX analysis"""

        with open(screenshot_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        url = f"{self.api_base}:generateContent?key={self.api_key}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_data
                            }
                        },
                        {
                            "text": f"""Analyze this UI screenshot. Context: {context}

                            Return JSON:
                            {{
                                "overall_score": 0-10,
                                "layout_score": 0-10,
                                "color_score": 0-10,
                                "typography_score": 0-10,
                                "usability_issues": [],
                                "design_suggestions": [],
                                "accessibility_concerns": [],
                                "positive_aspects": []
                            }}
                            """
                        }
                    ]
                }
            ]
        }

        response = requests.post(url, json=payload)
        result = response.json()

        try:
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '{}')
            parsed = json.loads(text)
            return UIAnalysis(**parsed)
        except:
            return UIAnalysis(
                overall_score=5.0,
                layout_score=5.0,
                color_score=5.0,
                typography_score=5.0,
                usability_issues=["Failed to parse"],
                design_suggestions=[],
                accessibility_concerns=[],
                positive_aspects=[]
            )


class VisionAnalyzer:
    """
    Unified Vision Analyzer
    Coordinates multiple vision agents for comprehensive analysis
    """

    def __init__(self, primary: str = "claude"):
        self.agents = {
            "claude": ClaudeVisionAgent(),
            "gemini": GeminiVisionAgent()
        }
        self.primary = primary

    def analyze(self, screenshot_path: str, context: str = "", use_all: bool = False) -> Dict:
        """
        Analyze screenshot using vision models

        Args:
            screenshot_path: Path to screenshot file
            context: Additional context about the app
            use_all: Use all available vision models

        Returns:
            Combined analysis results
        """

        results = {}

        if use_all:
            for name, agent in self.agents.items():
                try:
                    results[name] = agent.analyze_screenshot(screenshot_path, context)
                except Exception as e:
                    results[name] = {"error": str(e)}
        else:
            agent = self.agents.get(self.primary)
            if agent:
                results[self.primary] = agent.analyze_screenshot(screenshot_path, context)

        # Combine scores if multiple analyses
        if len(results) > 1:
            combined_score = sum(
                r.overall_score for r in results.values()
                if isinstance(r, UIAnalysis)
            ) / len(results)

            all_issues = []
            all_suggestions = []

            for r in results.values():
                if isinstance(r, UIAnalysis):
                    all_issues.extend(r.usability_issues)
                    all_suggestions.extend(r.design_suggestions)

            results["combined"] = {
                "score": combined_score,
                "all_issues": list(set(all_issues)),
                "all_suggestions": list(set(all_suggestions))
            }

        return results

    def compare_versions(self, screenshot_before: str, screenshot_after: str) -> Dict:
        """Compare two versions of UI"""
        before = self.analyze(screenshot_before, "Before version")
        after = self.analyze(screenshot_after, "After version")

        return {
            "before": before,
            "after": after,
            "improvement": after.get("combined", {}).get("score", 0) - before.get("combined", {}).get("score", 0)
        }


if __name__ == "__main__":
    analyzer = VisionAnalyzer(primary="claude")
    print("Vision Analyzer initialized")
    print(f"Available agents: {list(analyzer.agents.keys())}")
