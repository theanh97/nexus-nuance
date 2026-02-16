"""
Data Cleanup Script - Remove duplicates and clean up memory data
Part of The Dream Team Self-Learning Infrastructure
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import hashlib


def generate_content_hash(content: Dict) -> str:
    """Generate a hash for content comparison."""
    # Create a stable string representation for comparison
    key_fields = ["type", "description", "data", "context"]
    content_str = "|".join(str(content.get(k, "")) for k in key_fields)
    return hashlib.md5(content_str.encode()).hexdigest()


def deduplicate_list(items: List[Dict], id_field: str = "id") -> List[Dict]:
    """Remove duplicate items based on content hash, keeping the earliest."""
    seen_hashes = {}
    unique_items = []

    for item in items:
        content_hash = generate_content_hash(item)

        if content_hash not in seen_hashes:
            seen_hashes[content_hash] = item.get("created", "")
            unique_items.append(item)
        else:
            # Keep the earlier one
            existing_date = seen_hashes[content_hash]
            current_date = item.get("created", "")
            if current_date and (not existing_date or current_date < existing_date):
                # Replace with earlier version
                idx = next(i for i, x in enumerate(unique_items)
                          if generate_content_hash(x) == content_hash)
                unique_items[idx] = item
                seen_hashes[content_hash] = current_date

    return unique_items


def cleanup_patterns(patterns_path: Path) -> Dict:
    """Clean up patterns.json"""
    print("🧹 Cleaning patterns.json...")

    with open(patterns_path, 'r') as f:
        data = json.load(f)

    original_count = len(data.get("patterns", []))
    data["patterns"] = deduplicate_list(data.get("patterns", []))
    new_count = len(data["patterns"])

    data["last_cleaned"] = datetime.now().isoformat()

    with open(patterns_path, 'w') as f:
        json.dump(data, f, indent=2)

    return {
        "file": "patterns.json",
        "original": original_count,
        "cleaned": new_count,
        "removed": original_count - new_count
    }


def cleanup_lessons(lessons_path: Path) -> Dict:
    """Clean up lessons.json"""
    print("🧹 Cleaning lessons.json...")

    with open(lessons_path, 'r') as f:
        data = json.load(f)

    original_count = len(data.get("lessons", []))

    # Filter out false positive infinite loop warnings
    lessons = data.get("lessons", [])
    filtered_lessons = []
    for lesson in lessons:
        desc = lesson.get("description", "")
        # Remove the false positive loop detection entries
        if "Possible infinite loop detected: SYSTEM repeated iteration" in desc:
            continue
        filtered_lessons.append(lesson)

    # Then deduplicate
    data["lessons"] = deduplicate_list(filtered_lessons)
    new_count = len(data["lessons"])

    data["last_cleaned"] = datetime.now().isoformat()

    with open(lessons_path, 'w') as f:
        json.dump(data, f, indent=2)

    return {
        "file": "lessons.json",
        "original": original_count,
        "cleaned": new_count,
        "removed": original_count - new_count
    }


def cleanup_issues(issues_path: Path) -> Dict:
    """Clean up issues.json"""
    print("🧹 Cleaning issues.json...")

    if not issues_path.exists():
        return {"file": "issues.json", "original": 0, "cleaned": 0, "removed": 0}

    with open(issues_path, 'r') as f:
        data = json.load(f)

    original_count = len(data.get("issues", []))

    # Filter out false positive infinite loop issues
    issues = data.get("issues", [])
    filtered_issues = []
    for issue in issues:
        title = issue.get("title", "")
        if "infinite loop" in title.lower() and "iteration" in title.lower():
            continue
        filtered_issues.append(issue)

    data["issues"] = filtered_issues
    new_count = len(data["issues"])

    data["last_cleaned"] = datetime.now().isoformat()

    with open(issues_path, 'w') as f:
        json.dump(data, f, indent=2)

    return {
        "file": "issues.json",
        "original": original_count,
        "cleaned": new_count,
        "removed": original_count - new_count
    }


def cleanup_all(base_path: str = None) -> Dict:
    """Run all cleanup operations."""
    if base_path:
        base = Path(base_path)
    else:
        try:
            project_root = Path(__file__).parent.parent.parent
            base = project_root / "data" / "memory"
        except Exception:
            base = Path.cwd() / "data" / "memory"

    print("=" * 50)
    print("🧹 DATA CLEANUP")
    print("=" * 50)

    results = []

    # Clean patterns
    patterns_path = base / "patterns.json"
    if patterns_path.exists():
        results.append(cleanup_patterns(patterns_path))

    # Clean lessons
    lessons_path = base / "lessons.json"
    if lessons_path.exists():
        results.append(cleanup_lessons(lessons_path))

    # Clean issues
    issues_path = base / "issues.json"
    if issues_path.exists():
        results.append(cleanup_issues(issues_path))

    # Print summary
    print("\n📊 CLEANUP SUMMARY:")
    print("-" * 50)
    total_removed = 0
    for r in results:
        status = "✅" if r["removed"] > 0 else "➖"
        print(f"  {status} {r['file']}: {r['original']} → {r['cleaned']} (removed {r['removed']})")
        total_removed += r["removed"]

    print("-" * 50)
    print(f"  Total entries removed: {total_removed}")
    print("=" * 50)

    return {
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "total_removed": total_removed
    }


if __name__ == "__main__":
    cleanup_all()
