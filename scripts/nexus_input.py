#!/usr/bin/env python3
"""
NEXUS Universal Input - Learn Everything from User

Usage:
    python scripts/nexus_input.py --input "your message here"
    python scripts/nexus_input.py --file path/to/file.py
    python scripts/nexus_input.py --learn "fix this bug" --type bug
    python scripts/nexus_input.py --interactive

Auto-detects input type and learns automatically.
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.memory import (
    learn_from_input,
    run_auto_improvement,
    get_autonomous_improver,
    record_thumbs_up,
    record_thumbs_down,
    record_correction,
    record_approval,
    record_denial,
    get_hls,
)


class NexusInput:
    """Universal input handler for NEXUS."""

    # Input type patterns
    TYPE_PATTERNS = {
        "bug": ["bug", "error", "fix", "issue", "broken", "fail", "crash"],
        "feature": ["add", "new", "create", "implement", "feature", "want", "need"],
        "improvement": ["improve", "better", "enhance", "optimize", "upgrade"],
        "question": ["how", "what", "why", "when", "where", "?", "explain"],
        "command": ["do", "make", "run", "execute", "start", "stop"],
        "feedback": ["good", "bad", "like", "dislike", "prefer", "love", "hate"],
        "code": ["def ", "class ", "function", "import ", "=>", "->", "{"],
        "idea": ["idea", "think", "maybe", "perhaps", "could", "should"],
        "preference": ["prefer", "always", "never", "I want", "I like"],
    }

    # Value scores by type
    DEFAULT_VALUES = {
        "bug": 0.95,
        "feature": 0.85,
        "improvement": 0.8,
        "question": 0.5,
        "command": 0.9,
        "feedback": 0.8,
        "code": 0.6,
        "idea": 0.7,
        "preference": 0.85,
        "general": 0.5,
    }

    def __init__(self):
        self.hls = get_hls()
        self.improver = get_autonomous_improver()

    def detect_type(self, content: str) -> str:
        """Auto-detect input type."""
        content_lower = content.lower()

        for input_type, patterns in self.TYPE_PATTERNS.items():
            for pattern in patterns:
                if pattern in content_lower:
                    return input_type

        return "general"

    def calculate_value(self, content: str, input_type: str) -> float:
        """Calculate value score based on content and type."""
        base_value = self.DEFAULT_VALUES.get(input_type, 0.5)

        # Increase value for specific indicators
        if "!" in content:
            base_value += 0.1  # Emphasis
        if "urgent" in content.lower() or "ASAP" in content.upper():
            base_value += 0.15  # Urgency
        if "critical" in content.lower() or "important" in content.lower():
            base_value += 0.1  # Importance

        return min(1.0, base_value)

    def process(
        self,
        content: str,
        input_type: str = None,
        value_score: float = None,
        context: dict = None,
    ) -> dict:
        """Process user input and learn."""
        # Auto-detect type
        if not input_type:
            input_type = self.detect_type(content)

        # Calculate value
        if value_score is None:
            value_score = self.calculate_value(content, input_type)

        # Learn
        learning = learn_from_input(
            input_type=input_type,
            content=content,
            value_score=value_score,
            context=context or {},
        )

        # Also learn in feedback system
        if input_type == "feedback":
            if value_score >= 0.6:
                record_thumbs_up(content[:50])
            else:
                record_thumbs_down(content[:50])

        if input_type == "preference":
            record_approval(content[:50])

        # Run auto improvement
        improvement = run_auto_improvement()

        return {
            "learning": learning,
            "improvement": improvement,
            "detected_type": input_type,
            "value_score": value_score,
        }

    def interactive_mode(self):
        """Run interactive input mode."""
        print("🧠 NEXUS Interactive Mode")
        print("=" * 50)
        print("Type your input (commands, ideas, bugs, feedback, etc.)")
        print("Type 'quit' to exit, 'status' to see stats")
        print("Type 'learnings' to see what I've learned")
        print("")

        while True:
            try:
                user_input = input("💬 You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ["quit", "exit", "q"]:
                    print("\n👋 Goodbye!")
                    break

                if user_input.lower() == "status":
                    stats = self.improver.get_stats()
                    print(f"\n📊 Stats: {json.dumps(stats, indent=2)}")
                    continue

                if user_input.lower() == "learnings":
                    learnings = self.improver.get_all_learnings(limit=10)
                    print(f"\n📚 Recent Learnings:")
                    for l in learnings:
                        print(f"  - [{l.get('input_type')}] {l.get('content')[:60]}")
                    continue

                # Process input
                result = self.process(user_input)

                # Feedback
                print(f"\n✅ Learned as: {result['detected_type']} (value: {result['value_score']:.2f})")
                if result['improvement']['improvements_generated'] > 0:
                    print(f"   🔧 Generated {result['improvement']['improvements_generated']} improvement(s)")
                print("")

            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="NEXUS Universal Input")
    parser.add_argument("--input", "-i", type=str, help="Input to learn")
    parser.add_argument("--type", "-t", type=str, help="Input type (bug, feature, etc.)")
    parser.add_argument("--value", "-v", type=float, help="Value score (0-1)")
    parser.add_argument("--file", "-f", type=str, help="Learn from file")
    parser.add_argument("--interactive", action="store_true", help="Interactive mode")
    parser.add_argument("--status", action="store_true", help="Show status")

    args = parser.parse_args()

    nexus = NexusInput()

    if args.status:
        stats = nexus.improver.get_stats()
        print("📊 NEXUS Status:")
        print(json.dumps(stats, indent=2))
        return

    if args.interactive:
        nexus.interactive_mode()
        return

    if args.file:
        # Learn from file
        path = Path(args.file)
        if not path.exists():
            print(f"❌ File not found: {args.file}")
            sys.exit(1)

        content = path.read_text(encoding='utf-8')
        result = nexus.process(content, input_type="code")
        print(f"✅ Learned from file: {path.name}")
        print(f"   Type: {result['detected_type']}, Value: {result['value_score']:.2f}")
        return

    if args.input:
        result = nexus.process(
            args.input,
            input_type=args.type,
            value_score=args.value,
        )
        print(f"✅ Learned: {result['detected_type']} (value: {result['value_score']:.2f})")
        if result['improvement']['improvements_generated'] > 0:
            print(f"🔧 Generated {result['improvement']['improvements_generated']} improvement(s)")
        return

    # No args, show help
    parser.print_help()
    print("\nExamples:")
    print('  python scripts/nexus_input.py -i "fix the login bug"')
    print('  python scripts/nexus_input.py -i "add dark mode" -t feature')
    print('  python scripts/nexus_input.py -i "I prefer short output" -t preference')
    print('  python scripts/nexus_input.py --interactive')


if __name__ == "__main__":
    main()
