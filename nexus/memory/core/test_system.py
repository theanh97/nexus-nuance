#!/usr/bin/env python3
"""
Self-Learning System Test Script
Run this to verify all components are working.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import (
    remember, recall, learn_pattern, learn_lesson,
    scan_knowledge, get_pending_improvements,
    track_decision, track_action, track_error, health_check,
    start_learning, learn, status, get_learning_engine
)
try:
    from src.core.prompt_system import get_prompt_system
except ImportError:
    from core.prompt_system import get_prompt_system


def test_memory():
    """Test memory system."""
    print("\n📚 Testing Memory System...")

    # Store knowledge
    id1 = remember(
        key="test_knowledge",
        content="First Principles: Break problems down to fundamental truths",
        category="thinking",
        keywords=["reasoning", "problem-solving"],
        importance=9
    )
    print(f"  ✅ Stored knowledge: {id1}")

    # Retrieve knowledge
    results = recall(keywords=["reasoning"])
    print(f"  ✅ Retrieved {len(results)} items")

    # Learn pattern
    pid = learn_pattern(
        pattern_type="code_generation",
        pattern_data={"style": "clean_code"},
        success_rate=0.85
    )
    print(f"  ✅ Recorded pattern: {pid}")

    # Learn lesson
    lid = learn_lesson(
        lesson_type="insight",
        description="Token efficiency is critical for scalability",
        context="System design",
        impact="high"
    )
    print(f"  ✅ Learned lesson: {lid}")

    return True


def test_scanner():
    """Test knowledge scanner."""
    print("\n🔍 Testing Knowledge Scanner...")

    # Run scan
    results = scan_knowledge(min_score=6.0)
    print(f"  ✅ Discovered {results['total_discovered']} items")
    print(f"  ✅ Filtered {results['filtered_count']} items (6+ score)")
    print(f"  ✅ Generated {results['proposals_generated']} proposals")

    # Get pending improvements
    pending = get_pending_improvements()
    print(f"  ✅ Pending improvements: {len(pending)}")

    return True


def test_debugger():
    """Test self-debugger."""
    print("\n🔧 Testing Self-Debugger...")

    # Track decisions and actions
    track_decision("ORION", "test", "Testing decision tracking", confidence=0.9)
    print("  ✅ Tracked decision")

    track_action("NOVA", "test_action", {"test": True}, success=True, duration_ms=100)
    print("  ✅ Tracked action")

    track_error("CIPHER", "test_error", "Test error message", recoverable=True)
    print("  ✅ Tracked error")

    # Health check
    health = health_check()
    print(f"  ✅ Health check: {health['health_score']}/100")

    return True


def test_learning_loop():
    """Test learning loop."""
    print("\n🔄 Testing Learning Loop...")

    # Learn from interaction
    learn({
        "agent": "NOVA",
        "type": "code_generation",
        "success": True,
        "details": {"file": "test.tsx"},
        "duration_ms": 1500
    })
    print("  ✅ Learned from interaction")

    # Get status
    stat = status()
    print(f"  ✅ Status: Iteration {stat['iteration']}")
    notes = stat.get("daily_notes", {})
    print(f"  ✅ Daily notes tracked: {notes.get('count_today', 0)}")
    self_check = stat.get("self_check", {})
    print(f"  ✅ Self-check enabled: {self_check.get('enabled', False)}")
    daily_cycle = stat.get("daily_self_learning", {})
    print(f"  ✅ Daily self-learning enabled: {daily_cycle.get('enabled', False)}")
    focus = stat.get("focus", {})
    print(f"  ✅ Solution options available: {len(focus.get('solution_options', []))}")
    strategy = stat.get("strategy", {})
    print(f"  ✅ Current focus area: {strategy.get('current_focus_area', 'N/A')}")

    return (
        "daily_self_learning" in stat
        and "self_check" in stat
        and "solution_options" in focus
        and "current_focus_area" in strategy
    )


def test_advanced_learning():
    """Test advanced learning engine persistence and review flow."""
    print("\n🧠 Testing Advanced Learning...")

    engine = get_learning_engine()
    before_count = len(engine.knowledge_items)

    item = engine.add_knowledge(
        content="Spaced repetition improves retention by reviewing at increasing intervals.",
        category="learning",
        importance=8,
        tags=["spaced_repetition", "retention"],
    )
    print(f"  ✅ Added/updated knowledge item: {item.id}")

    due_items = engine.get_items_for_review()
    print(f"  ✅ Items due for review: {len(due_items)}")

    if due_items:
        review = engine.review_item(due_items[0], quality=4)
        print(f"  ✅ Reviewed item: {review['item_id']}")

    after_count = len(engine.knowledge_items)
    print(f"  ✅ Knowledge count: {before_count} -> {after_count}")

    return True


def test_prompt_system():
    """Test core prompt system directive injection and persistence."""
    print("\n🧩 Testing Prompt System...")

    ps = get_prompt_system()
    snapshot = ps.get_snapshot(limit=3)
    print(f"  ✅ Prompt system enabled: {snapshot.get('enabled', False)}")
    print(f"  ✅ Directives count: {snapshot.get('directives_count', 0)}")

    injected = ps.inject_messages(
        messages=[{"role": "user", "content": "Create test cases"}],
        agent_name="ECHO",
        role="QA Engineer",
    )
    has_system = bool(injected and injected[0].get("role") == "system")
    print(f"  ✅ System directive injected: {has_system}")

    return has_system


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("🧪 SELF-LEARNING SYSTEM TEST SUITE")
    print("=" * 60)

    tests = [
        ("Memory System", test_memory),
        ("Knowledge Scanner", test_scanner),
        ("Self-Debugger", test_debugger),
        ("Learning Loop", test_learning_loop),
        ("Advanced Learning", test_advanced_learning),
        ("Prompt System", test_prompt_system),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"  ❌ Error: {e}")
            results[name] = False

    # Summary
    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, passed_test in results.items():
        status = "✅ PASS" if passed_test else "❌ FAIL"
        print(f"  {name}: {status}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Self-learning system is ready.")
        print("\nTo start continuous learning:")
        print("  from memory import start_learning")
        print("  start_learning(interval=60)  # Learn every 60 seconds")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
