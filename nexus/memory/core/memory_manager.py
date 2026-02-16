"""
Memory Manager - Persistent Knowledge Storage & Retrieval System
Part of The Dream Team Self-Learning Infrastructure

Features:
- Hierarchical storage (Index → Summary → Full)
- Token-efficient retrieval
- Pattern recognition
- Cross-session persistence
- Auto-compression
- ROBUST ERROR HANDLING
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
import hashlib
import logging
import threading

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Centralized memory management for The Dream Team.
    Implements hierarchical knowledge storage for token efficiency.
    Thread-safe implementation.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Thread-safe singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, base_path: str = None):
        if hasattr(self, '_initialized') and self._initialized:
            return

        self._lock = threading.RLock()

        # Handle path safely - now points to data/memory/
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Default to data/memory/ directory relative to project root
            try:
                # src/memory/ -> ../../data/memory/
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "memory"
            except NameError:
                self.base_path = Path.cwd() / "data" / "memory"

        # Ensure directory exists
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.index_path = self.base_path / "knowledge_index.json"
        self.storage_path = self.base_path / "knowledge_store.json"
        self.patterns_path = self.base_path / "patterns.json"
        self.lessons_path = self.base_path / "lessons.json"

        # Max content size (1MB)
        self.MAX_CONTENT_SIZE = 1024 * 1024

        # Initialize storage
        self._initialize_storage()
        if os.getenv("MEMORY_AUTO_REPAIR_INDEX", "true").strip().lower() == "true":
            self._repair_index_if_needed()
        self._initialized = True

    def _initialize_storage(self):
        """Initialize storage files if they don't exist."""
        default_index = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "categories": {},
            "keyword_index": {}
        }

        default_storage = {
            "version": "1.0",
            "entries": {}
        }

        default_patterns = {
            "version": "1.0",
            "patterns": []
        }

        default_lessons = {
            "version": "1.0",
            "lessons": []
        }

        for path, default in [
            (self.index_path, default_index),
            (self.storage_path, default_storage),
            (self.patterns_path, default_patterns),
            (self.lessons_path, default_lessons)
        ]:
            if not path.exists():
                self._save_json(path, default)

    def _save_json(self, path: Path, data: Dict) -> bool:
        """Save data to JSON file safely."""
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, path)
                return True
        except (IOError, OSError, TypeError, ValueError) as e:
            logger.error(f"Failed to save {path}: {e}")
            return False

    def _load_json(self, path: Path) -> Dict:
        """Load data from JSON file safely."""
        try:
            if not path.exists():
                return {}
            with self._lock:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (IOError, OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load {path}: {e}")
            return {}

    def _generate_id(self, content: str) -> str:
        """Generate unique ID for content."""
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _repair_index_if_needed(self):
        """Repair index inconsistencies so retrieval remains stable."""
        try:
            storage = self._load_json(self.storage_path)
            index = self._load_json(self.index_path)
            entries = storage.get("entries", {}) if storage else {}
            if not isinstance(entries, dict):
                return

            entry_ids = set(entries.keys())
            repaired = False

            if "categories" not in index or not isinstance(index.get("categories"), dict):
                index["categories"] = {}
                repaired = True
            if "keyword_index" not in index or not isinstance(index.get("keyword_index"), dict):
                index["keyword_index"] = {}
                repaired = True

            # Remove stale IDs from index.
            for category, ids in list(index.get("categories", {}).items()):
                valid_ids = [entry_id for entry_id in ids if entry_id in entry_ids]
                if valid_ids != ids:
                    index["categories"][category] = valid_ids
                    repaired = True
                if not valid_ids:
                    del index["categories"][category]
                    repaired = True

            for keyword, ids in list(index.get("keyword_index", {}).items()):
                valid_ids = [entry_id for entry_id in ids if entry_id in entry_ids]
                if valid_ids != ids:
                    index["keyword_index"][keyword] = valid_ids
                    repaired = True
                if not valid_ids:
                    del index["keyword_index"][keyword]
                    repaired = True

            # Ensure every entry is indexable.
            for entry_id, entry in entries.items():
                category = str(entry.get("category", "general"))
                if category not in index["categories"]:
                    index["categories"][category] = []
                    repaired = True
                if entry_id not in index["categories"][category]:
                    index["categories"][category].append(entry_id)
                    repaired = True

                key = str(entry.get("key", "")).lower()
                if key:
                    if key not in index["keyword_index"]:
                        index["keyword_index"][key] = []
                        repaired = True
                    if entry_id not in index["keyword_index"][key]:
                        index["keyword_index"][key].append(entry_id)
                        repaired = True

            if repaired:
                index["last_updated"] = datetime.now().isoformat()
                self._save_json(self.index_path, index)
        except Exception as e:
            logger.error(f"Failed to repair index: {e}")

    def _safe_iso_parse(self, iso_string: str) -> datetime:
        """Safely parse ISO format datetime."""
        try:
            if iso_string:
                return datetime.fromisoformat(iso_string)
            return datetime.now()
        except (ValueError, TypeError):
            return datetime.now()

    def _compress(self, text: str) -> str:
        """Compress text for token efficiency while preserving meaning."""
        if not text:
            return ""

        # Remove extra whitespace
        compressed = ' '.join(text.split())

        # Limit length but try to end at a word boundary
        if len(compressed) > 500:
            compressed = compressed[:497]
            # Find last space to avoid cutting words
            last_space = compressed.rfind(' ')
            if last_space > 400:
                compressed = compressed[:last_space]
            compressed += "..."

        return compressed

    # ==================== STORAGE ====================

    def store(self, key: str, content: Any, category: str = "general",
              keywords: List[str] = None, importance: int = 5) -> Optional[str]:
        """
        Store knowledge with hierarchical structure.

        Args:
            key: Unique identifier for this knowledge
            content: The actual content (can be dict, list, or string)
            category: Category for organization
            keywords: List of keywords for retrieval
            importance: 1-10 scale (higher = more important)

        Returns:
            entry_id: Unique ID for the stored entry, or None if failed
        """
        try:
            # Convert and validate content
            if isinstance(content, dict):
                full_content = json.dumps(content, ensure_ascii=False)
                key_points = {k: v for k, v in list(content.items())[:5]}
                summary = f"Dict with {len(content)} keys: {', '.join(list(content.keys())[:3])}"
            elif isinstance(content, list):
                full_content = json.dumps(content, ensure_ascii=False)
                key_points = content[:5] if len(content) > 5 else content
                summary = f"List with {len(content)} items"
            else:
                full_content = str(content)
                key_points = self._compress(full_content)
                summary = self._compress(full_content)

            # Check content size
            if len(full_content) > self.MAX_CONTENT_SIZE:
                logger.warning(f"Content too large ({len(full_content)} bytes), truncating")
                full_content = full_content[:self.MAX_CONTENT_SIZE] + "...[TRUNCATED]"

            # De-dup: same key + same content should not create another entry.
            storage = self._load_json(self.storage_path)
            if not storage:
                storage = {"version": "1.0", "entries": {}}

            content_hash = hashlib.md5(full_content.encode("utf-8")).hexdigest()
            for existing_id, existing in storage.get("entries", {}).items():
                same_key = existing.get("key") == key
                existing_hash = existing.get("content_hash")
                if same_key and existing_hash == content_hash:
                    return existing_id

                # Backward compatibility for old entries without content_hash.
                if same_key and not existing_hash:
                    existing_full = existing.get("hierarchy", {}).get("full", "")
                    if existing_full == full_content:
                        existing["content_hash"] = content_hash
                        self._save_json(self.storage_path, storage)
                        return existing_id

            entry_id = self._generate_id(f"{key}:{content_hash}:{datetime.now().isoformat()}")

            # Create entry
            entry = {
                "id": entry_id,
                "key": key,
                "category": category,
                "keywords": keywords or [],
                "importance": max(1, min(10, importance)),  # Clamp 1-10
                "created": datetime.now().isoformat(),
                "content_hash": content_hash,
                "access_count": 0,
                "last_accessed": None,
                "hierarchy": {
                    "summary": summary,
                    "key_points": key_points,
                    "full": full_content
                }
            }

            storage["entries"][entry_id] = entry

            if self._save_json(self.storage_path, storage):
                self._update_index(entry_id, key, category, keywords, importance)
                return entry_id

            return None

        except Exception as e:
            logger.error(f"Failed to store {key}: {e}")
            return None

    def _update_index(self, entry_id: str, key: str, category: str,
                      keywords: List[str], importance: int):
        """Update the knowledge index for fast retrieval."""
        try:
            index = self._load_json(self.index_path)
            if not index:
                index = {"version": "1.0", "categories": {}, "keyword_index": {}}

            # Update category index
            if category not in index.get("categories", {}):
                index["categories"][category] = []
            if entry_id not in index["categories"][category]:
                index["categories"][category].append(entry_id)

            # Update keyword index
            if "keyword_index" not in index:
                index["keyword_index"] = {}

            for keyword in (keywords or []):
                keyword = keyword.lower()
                if keyword not in index["keyword_index"]:
                    index["keyword_index"][keyword] = []
                if entry_id not in index["keyword_index"][keyword]:
                    index["keyword_index"][keyword].append(entry_id)

            # Add key to keyword index
            if key.lower() not in index["keyword_index"]:
                index["keyword_index"][key.lower()] = []
            if entry_id not in index["keyword_index"][key.lower()]:
                index["keyword_index"][key.lower()].append(entry_id)

            index["last_updated"] = datetime.now().isoformat()
            self._save_json(self.index_path, index)

        except Exception as e:
            logger.error(f"Failed to update index: {e}")

    # ==================== RETRIEVAL ====================

    def retrieve(self, query: str = None, category: str = None,
                 keywords: List[str] = None, level: str = "summary",
                 limit: int = 10) -> List[Dict]:
        """
        Retrieve knowledge with token-efficient loading.
        """
        try:
            storage = self._load_json(self.storage_path)
            index = self._load_json(self.index_path)

            if not storage or not storage.get("entries"):
                return []

            # Find candidate entry IDs
            candidate_ids = set()
            entries = storage["entries"]

            if category and category in index.get("categories", {}):
                candidate_ids.update(index["categories"][category])

            if keywords:
                for keyword in keywords:
                    keyword = keyword.lower()
                    if keyword in index.get("keyword_index", {}):
                        candidate_ids.update(index["keyword_index"][keyword])

            if query:
                query_lower = query.lower()
                if query_lower in index.get("keyword_index", {}):
                    candidate_ids.update(index["keyword_index"][query_lower])
                # Fallback fuzzy lookup over key/summary/full content.
                if not candidate_ids:
                    for entry_id, entry in entries.items():
                        key_text = str(entry.get("key", "")).lower()
                        hierarchy = entry.get("hierarchy", {})
                        summary_text = str(hierarchy.get("summary", "")).lower()
                        full_text = str(hierarchy.get("full", "")).lower()
                        if query_lower in key_text or query_lower in summary_text or query_lower in full_text:
                            candidate_ids.add(entry_id)

            # If no filters, return recent entries
            if not candidate_ids and not category and not keywords and not query:
                entry_ids = sorted(
                    entries.keys(),
                    key=lambda entry_id: self._safe_iso_parse(entries[entry_id].get("created", "")).timestamp(),
                    reverse=True,
                )
                candidate_ids = set(entry_ids[:limit])

            # Retrieve and format results
            results = []
            ordered_ids = sorted(
                [entry_id for entry_id in candidate_ids if entry_id in entries],
                key=lambda entry_id: (
                    int(entries[entry_id].get("importance", 5)),
                    self._safe_iso_parse(entries[entry_id].get("created", "")).timestamp(),
                ),
                reverse=True,
            )[:limit]

            for entry_id in ordered_ids:
                entry = entries[entry_id]

                # Update access stats
                entry["access_count"] = entry.get("access_count", 0) + 1
                entry["last_accessed"] = datetime.now().isoformat()

                # Return based on level
                hierarchy = entry.get("hierarchy", {})
                result = {
                    "id": entry["id"],
                    "key": entry["key"],
                    "category": entry.get("category", "general"),
                    "importance": entry.get("importance", 5),
                    "content": hierarchy.get(level, hierarchy.get("summary", ""))
                }
                results.append(result)

            # Save updated access stats
            self._save_json(self.storage_path, storage)

            return results

        except Exception as e:
            logger.error(f"Failed to retrieve: {e}")
            return []

    def get_by_key(self, key: str, level: str = "full") -> Optional[Dict]:
        """Get entry by exact key match."""
        try:
            storage = self._load_json(self.storage_path)
            if not storage:
                return None

            for entry_id, entry in storage.get("entries", {}).items():
                if entry.get("key") == key:
                    entry["access_count"] = entry.get("access_count", 0) + 1
                    entry["last_accessed"] = datetime.now().isoformat()
                    self._save_json(self.storage_path, storage)

                    hierarchy = entry.get("hierarchy", {})
                    return {
                        "id": entry["id"],
                        "key": entry["key"],
                        "category": entry.get("category", "general"),
                        "content": hierarchy.get(level)
                    }
            return None
        except Exception as e:
            logger.error(f"Failed to get by key: {e}")
            return None

    # ==================== PATTERNS ====================

    def record_pattern(self, pattern_type: str, pattern_data: Dict,
                       success_rate: float = 0.0, context: str = None) -> Optional[str]:
        """Record a discovered pattern."""
        try:
            patterns = self._load_json(self.patterns_path)
            if not patterns:
                patterns = {"version": "1.0", "patterns": []}

            pattern = {
                "id": self._generate_id(f"{pattern_type}:{datetime.now().isoformat()}"),
                "type": pattern_type,
                "data": pattern_data,
                "success_rate": max(0.0, min(1.0, success_rate)),
                "context": context,
                "usage_count": 0,
                "created": datetime.now().isoformat(),
                "last_used": None
            }

            patterns["patterns"].append(pattern)
            self._save_json(self.patterns_path, patterns)
            return pattern["id"]

        except Exception as e:
            logger.error(f"Failed to record pattern: {e}")
            return None

    def get_patterns(self, pattern_type: str = None, min_success: float = 0.0) -> List[Dict]:
        """Get patterns, optionally filtered."""
        try:
            patterns = self._load_json(self.patterns_path)
            if not patterns:
                return []

            results = []
            for pattern in patterns.get("patterns", []):
                if pattern_type and pattern.get("type") != pattern_type:
                    continue
                if pattern.get("success_rate", 0) < min_success:
                    continue
                results.append(pattern)

            results.sort(key=lambda x: x.get("success_rate", 0), reverse=True)
            return results

        except Exception as e:
            logger.error(f"Failed to get patterns: {e}")
            return []

    # ==================== LESSONS ====================

    def learn_lesson(self, lesson_type: str = "insight", description: str = "",
                     context: str = "", impact: str = "medium") -> Optional[str]:
        """Record a learned lesson."""
        try:
            lessons = self._load_json(self.lessons_path)
            if not lessons:
                lessons = {"version": "1.0", "lessons": []}

            valid_impacts = ["low", "medium", "high", "critical"]
            impact = impact if impact in valid_impacts else "medium"

            lesson = {
                "id": self._generate_id(f"{lesson_type}:{datetime.now().isoformat()}"),
                "type": lesson_type,
                "description": description[:500] if len(description) > 500 else description,
                "context": context[:200] if len(context) > 200 else context,
                "impact": impact,
                "created": datetime.now().isoformat(),
                "applied_count": 0
            }

            lessons["lessons"].append(lesson)
            self._save_json(self.lessons_path, lessons)
            return lesson["id"]

        except Exception as e:
            logger.error(f"Failed to learn lesson: {e}")
            return None

    def get_lessons(self, lesson_type: str = None, impact: str = None) -> List[Dict]:
        """Get lessons, optionally filtered."""
        try:
            lessons = self._load_json(self.lessons_path)
            if not lessons:
                return []

            results = []
            for lesson in lessons.get("lessons", []):
                if lesson_type and lesson.get("type") != lesson_type:
                    continue
                if impact and lesson.get("impact") != impact:
                    continue
                results.append(lesson)

            impact_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            results.sort(key=lambda x: impact_order.get(x.get("impact", "medium"), 99))
            return results

        except Exception as e:
            logger.error(f"Failed to get lessons: {e}")
            return []

    # ==================== MAINTENANCE ====================

    def cleanup(self, max_age_days: int = 90, min_access: int = 1) -> int:
        """Remove stale or unused entries. Returns count of removed entries."""
        try:
            storage = self._load_json(self.storage_path)
            index = self._load_json(self.index_path)

            if not storage or not storage.get("entries"):
                return 0

            now = datetime.now()
            to_remove = []

            for entry_id, entry in storage["entries"].items():
                created = self._safe_iso_parse(entry.get("created", ""))
                age_days = (now - created).days
                access_count = entry.get("access_count", 0)

                if age_days > max_age_days and access_count < min_access:
                    to_remove.append(entry_id)

            # Remove entries
            for entry_id in to_remove:
                del storage["entries"][entry_id]

                # Remove from index
                for category in index.get("categories", {}):
                    if entry_id in index["categories"][category]:
                        index["categories"][category].remove(entry_id)
                for keyword in index.get("keyword_index", {}):
                    if entry_id in index["keyword_index"][keyword]:
                        index["keyword_index"][keyword].remove(entry_id)

            # Drop empty buckets to keep index compact.
            for category in list(index.get("categories", {}).keys()):
                if not index["categories"][category]:
                    del index["categories"][category]
            for keyword in list(index.get("keyword_index", {}).keys()):
                if not index["keyword_index"][keyword]:
                    del index["keyword_index"][keyword]
            index["last_updated"] = datetime.now().isoformat()

            self._save_json(self.storage_path, storage)
            self._save_json(self.index_path, index)

            return len(to_remove)

        except Exception as e:
            logger.error(f"Failed to cleanup: {e}")
            return 0

    def get_stats(self) -> Dict:
        """Get memory statistics."""
        try:
            storage = self._load_json(self.storage_path)
            patterns = self._load_json(self.patterns_path)
            lessons = self._load_json(self.lessons_path)
            index = self._load_json(self.index_path)

            total_size = 0
            for path in [self.storage_path, self.index_path, self.patterns_path, self.lessons_path]:
                if path.exists():
                    total_size += path.stat().st_size

            return {
                "total_entries": len(storage.get("entries", {})),
                "total_patterns": len(patterns.get("patterns", [])),
                "total_lessons": len(lessons.get("lessons", [])),
                "categories": len(index.get("categories", {})),
                "keywords": len(index.get("keyword_index", {})),
                "storage_size_bytes": total_size
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "total_entries": 0,
                "total_patterns": 0,
                "total_lessons": 0,
                "categories": 0,
                "keywords": 0,
                "storage_size_bytes": 0
            }


# ==================== CONVENIENCE FUNCTIONS ====================

def get_memory() -> MemoryManager:
    """Get singleton memory manager instance."""
    return MemoryManager()


def remember(key: str, content: Any, **kwargs) -> Optional[str]:
    """Quick store function."""
    return get_memory().store(key, content, **kwargs)


def recall(query: str = None, **kwargs) -> List[Dict]:
    """Quick retrieve function."""
    return get_memory().retrieve(query=query, **kwargs)


def learn_pattern(pattern_type: str, pattern_data: Dict, **kwargs) -> Optional[str]:
    """Quick pattern recording."""
    return get_memory().record_pattern(pattern_type, pattern_data, **kwargs)


def learn_lesson(description: str, context: str, lesson_type: str = "insight", **kwargs) -> Optional[str]:
    """Quick lesson learning."""
    return get_memory().learn_lesson(
        lesson_type=lesson_type,
        description=description,
        context=context,
        **kwargs
    )


# ==================== TEST ====================

if __name__ == "__main__":
    print("📚 Memory Manager Test")
    print("=" * 50)

    mm = MemoryManager()

    # Test store
    id1 = mm.store(
        key="test_first_principles",
        content="Break down problems to fundamental truths",
        category="thinking",
        keywords=["reasoning", "problem solving"],
        importance=9
    )
    print(f"Store result: {id1}")

    # Test retrieve
    results = mm.retrieve(keywords=["reasoning"])
    print(f"Retrieve: {len(results)} results")

    # Test stats
    print(f"Stats: {mm.get_stats()}")
    print("✅ Memory Manager OK")
