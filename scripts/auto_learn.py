#!/usr/bin/env python3
"""
Auto-Learning Hook for NEXUS
Thêm vào bất kỳ đâu để tự động học từ mọi tin nhắn.

Cách dùng đơn giản:
    from scripts.auto_learn import auto_learn

    # Mỗi khi user gửi message:
    auto_learn(user_message)

    # Mỗi khi bạn trả lời:
    auto_learn_response(my_response, user_correction=None)
"""

import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Lazy imports for performance
_nexus = None


def _get_nexus():
    """Lazy load to avoid import overhead."""
    global _nexus
    if _nexus is None:
        from src.memory.nexus_integration import get_nexus
        _nexus = get_nexus()
    return _nexus


def auto_learn(user_message: str) -> dict:
    """
    Học từ message của user.
    Gọi hàm này cho MỌI message của user.

    Returns:
        dict với 'learned' (bool) và 'details'
    """
    if not user_message or len(user_message.strip()) < 3:
        return {"learned": False, "reason": "too_short"}

    try:
        nexus = _get_nexus()
        result = nexus.learn(user_message)

        return {
            "learned": result is not None,
            "count": len(result) if result else 0,
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
    except Exception as e:
        return {"learned": False, "error": str(e)}


def auto_learn_response(my_response: str, user_correction: str = None) -> dict:
    """
    Học từ response của AI.
    Nếu user correction → học correction đó.
    """
    try:
        nexus = _get_nexus()
        result = nexus.learn_response(my_response, user_correction)

        return {
            "learned": result is not None,
            "type": "correction" if user_correction else None,
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
    except Exception as e:
        return {"learned": False, "error": str(e)}


def auto_improve() -> dict:
    """
    Chạy improvement cycle.
    Gọi định kỳ hoặc khi có nhiều learnings mới.
    """
    try:
        nexus = _get_nexus()
        result = nexus.improve()

        return {
            "improvements": result.get("improvements_generated", 0),
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
    except Exception as e:
        return {"improvements": 0, "error": str(e)}


def status() -> dict:
    """Xem status."""
    try:
        return _get_nexus().status()
    except Exception as e:
        return {"error": str(e)}


def report() -> str:
    """Xem report."""
    try:
        return _get_nexus().report()
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    # Demo
    print("🧠 Auto-Learning Hook Demo")
    print("=" * 50)

    # Test learning
    test_messages = [
        "fix the login bug in auth",
        "add dark mode feature",
        "hello",  # too short - skipped
    ]

    for msg in test_messages:
        result = auto_learn(msg)
        status = "✅" if result.get("learned") else "⏭️"
        print(f"{status} {msg[:40]}")

    print("\n" + report())
