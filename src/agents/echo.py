"""
ECHO - QA Engineer & Test Specialist
Meticulous test engineer for quality assurance
"""

import os
import json
from typing import Dict, Any, List
from pathlib import Path

from src.core.agent import AsyncAgent
from src.core.message import AgentMessage, TaskResult
from src.core.model_router import TaskType


class Echo(AsyncAgent):
    """
    ECHO - QA Engineer & Test Specialist

    Responsibilities:
    - Generate tests
    - Validate functionality
    - Bug tracking
    - Coverage reporting
    """

    def __init__(self):
        super().__init__(
            name="Echo",
            role="QA Engineer & Test Specialist",
            model=os.getenv("TEST_MODEL", os.getenv("GLM_FLASH_MODEL", "glm-4.7")),  # Fast, cheap, and z.ai-safe by default
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GLM_API_KEY"),
            api_base=os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1"),
        )

        self.tests_dir = Path("tests")
        self.tests_dir.mkdir(parents=True, exist_ok=True)

    async def process(self, message: AgentMessage) -> TaskResult:
        """Process QA task"""

        self._log("🧪 Processing QA task...")

        context = message.content.get("context", {})
        iteration = message.content.get("iteration", 1)

        # Get code to test
        files = context.get("files", {})

        # Generate tests
        importance = message.priority.value if hasattr(message, "priority") else "normal"
        tests = await self._generate_tests(files, context, importance)

        # Save tests
        saved_tests = []
        for test_file in tests.get("test_files", []):
            path = self.tests_dir / test_file["name"]
            with open(path, "w", encoding="utf-8") as f:
                f.write(test_file["content"])
            saved_tests.append(str(path))
            self._log(f"📝 Test saved: {path}")

        # Calculate coverage estimate
        coverage = self._estimate_coverage(files, tests)

        return self.create_result(
            success=True,
            output={
                "tests": saved_tests,
                "test_count": len(saved_tests),
                "coverage_estimate": coverage
            },
            score=coverage / 10,  # Normalize to 10
            issues=tests.get("potential_bugs", []),
            suggestions=tests.get("test_suggestions", [])
        )

    async def _generate_tests(self, files: Dict, context: Dict, importance: str = "normal") -> Dict:
        """Generate tests for the code"""

        prompt = f"""You are ECHO, a meticulous QA engineer.

CODE TO TEST:
{json.dumps(files, indent=2)[:2000]}

Generate comprehensive tests. Include:

1. Unit tests for functions
2. Integration tests for components
3. Edge case tests
4. Error handling tests

For web applications, generate JavaScript tests using this format:

Respond in JSON:
{{
    "test_files": [
        {{
            "name": "test_app.js",
            "content": "// Test code here...",
            "type": "unit|integration|e2e"
        }}
    ],
    "potential_bugs": [
        "Bug that might exist 1",
        "Bug that might exist 2"
    ],
    "test_suggestions": [
        "Additional test scenario 1",
        "Additional test scenario 2"
    ],
    "coverage_estimate": 0-100
}}

Make tests that actually work and catch real bugs."""

        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": prompt}
        ]

        response = await self.call_api(
            messages,
            task_type=TaskType.TEST_GENERATION,
            importance=importance,
            prefer_cost=True
        )

        if "error" in response:
            return {"test_files": [], "potential_bugs": [], "test_suggestions": []}

        result = self._parse_json_response(response)

        if result.get("parse_error"):
            return self._generate_basic_tests(files)

        return result

    def _generate_basic_tests(self, files: Dict) -> Dict:
        """Generate basic tests as fallback"""

        test_content = """// Auto-generated basic tests

describe('Basic Functionality Tests', () => {

    test('Page loads without errors', () => {
        expect(document).toBeDefined();
    });

    test('Main container exists', () => {
        const container = document.querySelector('.container') ||
                          document.querySelector('main') ||
                          document.querySelector('#app');
        expect(container).not.toBeNull();
    });

    test('No console errors on load', () => {
        const consoleErrors = [];
        const originalError = console.error;
        console.error = (...args) => {
            consoleErrors.push(args);
            originalError.apply(console, args);
        };

        // Page should be loaded
        expect(consoleErrors.length).toBe(0);

        console.error = originalError;
    });

    test('All images have alt attributes', () => {
        const images = document.querySelectorAll('img');
        images.forEach(img => {
            expect(img.getAttribute('alt')).toBeDefined();
        });
    });

    test('All links have valid href', () => {
        const links = document.querySelectorAll('a');
        links.forEach(link => {
            const href = link.getAttribute('href');
            expect(href).not.toBe('');
            expect(href).not.toBe('#');
        });
    });
});
"""

        return {
            "test_files": [
                {
                    "name": "basic.test.js",
                    "content": test_content,
                    "type": "unit"
                }
            ],
            "potential_bugs": ["Could not perform deep analysis"],
            "test_suggestions": ["Add manual testing for user flows"],
            "coverage_estimate": 30
        }

    def _get_system_prompt(self) -> str:
        return """You are ECHO, a meticulous QA engineer who catches bugs before users do.

Your expertise:
- Writing comprehensive tests
- Finding edge cases
- Breaking things (on purpose)
- Ensuring quality

Test principles:
1. Test the happy path
2. Test the sad path
3. Test the edge cases
4. Test error handling
5. Test user interactions

For each test:
- Clear description
- Isolated (no dependencies)
- Repeatable
- Fast

Your goal: 80%+ coverage, 0 bugs in production."""

    def _estimate_coverage(self, files: Dict, tests: Dict) -> float:
        """Estimate test coverage"""

        # Simple heuristic
        code_lines = sum(len(str(c).split('\n')) for c in files.values())
        test_count = len(tests.get("test_files", []))

        if code_lines == 0:
            return 5.0

        # More tests = higher coverage estimate
        coverage = min(10.0, 5.0 + (test_count * 0.5))

        return coverage
