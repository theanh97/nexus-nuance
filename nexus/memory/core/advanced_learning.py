"""
Advanced Learning Engine - Human Learning Principles Implementation
Part of The Dream Team Self-Learning Infrastructure

Implements scientific learning methods:
1. Spaced Repetition - Review at optimal intervals
2. Active Recall - Test knowledge retrieval
3. Chunking - Break info into digestible pieces
4. Interleaving - Mix related concepts
5. Elaboration - Connect to existing knowledge
6. Metacognition - Self-awareness of learning
7. Deliberate Practice - Focus on weaknesses
8. Dual Coding - Combine verbal and visual
"""

import json
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
import random
import hashlib
import logging


logger = logging.getLogger(__name__)


class LearningMethod(Enum):
    SPACED_REPETITION = "spaced_repetition"
    ACTIVE_RECALL = "active_recall"
    CHUNKING = "chunking"
    INTERLEAVING = "interleaving"
    ELABORATION = "elaboration"
    METACOGNITION = "metacognition"
    DELIBERATE_PRACTICE = "deliberate_practice"
    DUAL_CODING = "dual_coding"


@dataclass
class KnowledgeItem:
    """A piece of knowledge to be learned."""
    id: str
    content: str
    category: str
    importance: int  # 1-10
    created: datetime
    last_reviewed: Optional[datetime] = None
    review_count: int = 0
    ease_factor: float = 2.5  # For spaced repetition
    interval_days: int = 1
    next_review: Optional[datetime] = None
    mastery_level: float = 0.0  # 0-100
    tags: List[str] = field(default_factory=list)


@dataclass
class LearningSession:
    """A learning session with specific goals."""
    started: datetime
    method: LearningMethod
    items_reviewed: int = 0
    items_learned: int = 0
    items_mastered: int = 0
    questions_asked: int = 0
    correct_answers: int = 0
    duration_minutes: float = 0


class SpacedRepetitionScheduler:
    """
    Implements the SM-2 algorithm for spaced repetition.
    Schedules reviews at optimal intervals for long-term retention.
    """

    def __init__(self):
        self.min_ease_factor = 1.3
        self.default_ease_factor = 2.5

    def calculate_next_review(
        self,
        item: KnowledgeItem,
        quality: int  # 0-5, where 5 = perfect, 0 = complete failure
    ) -> Tuple[datetime, float, int]:
        """
        Calculate next review time using SM-2 algorithm.

        Args:
            item: The knowledge item
            quality: Response quality (0-5)

        Returns:
            (next_review_date, new_ease_factor, new_interval_days)
        """
        if quality < 3:
            # Reset on failure
            return (
                datetime.now() + timedelta(minutes=10),  # Review soon
                max(self.min_ease_factor, item.ease_factor - 0.2),
                1
            )

        # Update ease factor
        new_ease = item.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        new_ease = max(self.min_ease_factor, new_ease)

        # Calculate interval
        if item.review_count == 0:
            interval = 1
        elif item.review_count == 1:
            interval = 6
        else:
            interval = math.ceil(item.interval_days * new_ease)

        next_review = datetime.now() + timedelta(days=interval)

        return next_review, new_ease, interval


class ActiveRecallTester:
    """
    Implements active recall testing.
    Tests knowledge retrieval without looking at notes.
    """

    def generate_question(self, item: KnowledgeItem) -> Dict:
        """
        Generate a question to test recall of this knowledge.
        """
        content = item.content

        # Simple question generation strategies
        questions = []

        # Strategy 1: Fill in the blank
        words = content.split()
        if len(words) > 5:
            key_word = random.choice([w for w in words if len(w) > 4])
            blanked = content.replace(key_word, "_____")
            questions.append({
                "type": "fill_blank",
                "question": f"Complete: {blanked}",
                "answer": key_word,
                "hint": f"It's a {len(key_word)}-letter word"
            })

        # Strategy 2: Concept explanation
        questions.append({
            "type": "explain",
            "question": f"Explain in your own words: {content[:100]}...",
            "answer": content,
            "hint": "Key concepts from the original"
        })

        # Strategy 3: Application
        questions.append({
            "type": "apply",
            "question": f"How would you apply this knowledge: {content[:80]}?",
            "answer": "Any practical application",
            "hint": "Think of a real-world scenario"
        })

        return random.choice(questions)

    def evaluate_answer(self, question: Dict, user_answer: str) -> Tuple[int, str]:
        """
        Evaluate the answer quality (0-5).
        """
        correct_answer = question["answer"].lower()
        user_answer_lower = user_answer.lower()

        # Simple similarity scoring
        if correct_answer in user_answer_lower or user_answer_lower in correct_answer:
            return 5, "Perfect recall!"

        # Check for key terms
        key_terms = set(correct_answer.split())
        user_terms = set(user_answer_lower.split())
        overlap = len(key_terms & user_terms) / max(len(key_terms), 1)

        if overlap > 0.7:
            return 4, "Excellent recall with minor gaps"
        elif overlap > 0.5:
            return 3, "Good recall, but missing some details"
        elif overlap > 0.3:
            return 2, "Partial recall, needs reinforcement"
        else:
            return 1, "Failed recall, review needed"


class ChunkingEngine:
    """
    Implements chunking - breaking complex info into digestible pieces.
    Optimal chunk size: 3-5 items (working memory capacity)
    """

    MAX_CHUNK_SIZE = 5

    def chunk_content(self, content: str) -> List[Dict]:
        """
        Break content into digestible chunks.
        """
        # Split by sentences or logical breaks
        sentences = [s.strip() for s in content.split('.') if s.strip()]

        chunks = []
        current_chunk = []
        current_size = 0

        for sentence in sentences:
            words = len(sentence.split())

            if current_size + words > 50 or len(current_chunk) >= self.MAX_CHUNK_SIZE:
                if current_chunk:
                    chunks.append({
                        "content": '. '.join(current_chunk) + '.',
                        "size": len(current_chunk),
                        "importance": 1.0 / len(chunks) if chunks else 1.0
                    })
                current_chunk = [sentence]
                current_size = words
            else:
                current_chunk.append(sentence)
                current_size += words

        # Don't forget the last chunk
        if current_chunk:
            chunks.append({
                "content": '. '.join(current_chunk) + '.',
                "size": len(current_chunk),
                "importance": 1.0 / (len(chunks) + 1) if chunks else 1.0
            })

        return chunks


class InterleavingScheduler:
    """
    Implements interleaving - mixing related concepts during learning.
    Improves discrimination and long-term retention.
    """

    def __init__(self):
        self.mix_ratio = 0.3  # 30% from other categories

    def create_interleaved_session(
        self,
        items: List[KnowledgeItem],
        focus_category: str,
        session_size: int = 10
    ) -> List[KnowledgeItem]:
        """
        Create a session with interleaved items from different categories.
        """
        focus_items = [i for i in items if i.category == focus_category]
        other_items = [i for i in items if i.category != focus_category]

        # Shuffle both lists
        random.shuffle(focus_items)
        random.shuffle(other_items)

        # Calculate mix
        other_count = int(session_size * self.mix_ratio)
        focus_count = session_size - other_count

        # Build interleaved session
        session = []
        focus_idx = 0
        other_idx = 0

        # Interleave: focus, other, focus, other...
        for i in range(session_size):
            if i % 3 == 2 and other_idx < other_count and other_items:
                # Every 3rd item from other category
                session.append(other_items[other_idx % len(other_items)])
                other_idx += 1
            elif focus_idx < focus_count and focus_items:
                session.append(focus_items[focus_idx % len(focus_items)])
                focus_idx += 1
            elif other_items:
                session.append(other_items[other_idx % len(other_items)])
                other_idx += 1

        return session


class ElaborationEngine:
    """
    Implements elaboration - connecting new knowledge to existing knowledge.
    Creates deeper understanding through associations.
    """

    def generate_elaborations(
        self,
        new_item: KnowledgeItem,
        existing_items: List[KnowledgeItem],
        max_elaborations: int = 3
    ) -> List[Dict]:
        """
        Generate elaboration prompts connecting new knowledge to existing.
        """
        elaborations = []

        # Find related items by tag or category
        related = [
            item for item in existing_items
            if item.id != new_item.id and (
                item.category == new_item.category or
                set(item.tags) & set(new_item.tags)
            )
        ]

        # Sort by relevance
        related.sort(key=lambda x: len(set(x.tags) & set(new_item.tags)), reverse=True)

        for i, related_item in enumerate(related[:max_elaborations]):
            elaborations.append({
                "type": "connection",
                "prompt": f"How does '{new_item.content[:50]}...' relate to '{related_item.content[:50]}...'?",
                "hint": f"Both are about {new_item.category}",
                "connection_to": related_item.id
            })

        # Add "why" elaboration
        elaborations.append({
            "type": "why",
            "prompt": f"Why is this knowledge important: {new_item.content[:80]}?",
            "hint": "Think about the impact and value"
        })

        # Add "what if" elaboration
        elaborations.append({
            "type": "what_if",
            "prompt": f"What if this wasn't true: {new_item.content[:60]}?",
            "hint": "Consider the opposite scenario"
        })

        return elaborations


class MetacognitionMonitor:
    """
    Implements metacognition - self-awareness of learning process.
    Helps identify knowledge gaps and learning effectiveness.
    """

    def assess_understanding(self, item: KnowledgeItem) -> Dict:
        """
        Generate self-assessment questions.
        """
        return {
            "confidence_question": f"On a scale of 1-5, how confident are you about: {item.content[:60]}?",
            "clarity_question": "Do you feel you understand this completely, partially, or not at all?",
            "gap_question": "What part of this knowledge is still unclear?",
            "application_question": "Can you think of a situation where you would use this?",
        }

    def calculate_learning_metrics(self, sessions: List[LearningSession]) -> Dict:
        """
        Calculate metacognitive metrics about learning.
        """
        if not sessions:
            return {}

        total_items = sum(s.items_reviewed for s in sessions)
        total_mastered = sum(s.items_mastered for s in sessions)
        total_correct = sum(s.correct_answers for s in sessions)
        total_questions = sum(s.questions_asked for s in sessions)

        return {
            "total_sessions": len(sessions),
            "total_items_reviewed": total_items,
            "mastery_rate": total_mastered / max(total_items, 1),
            "accuracy_rate": total_correct / max(total_questions, 1),
            "avg_session_duration": sum(s.duration_minutes for s in sessions) / len(sessions),
            "learning_velocity": total_items / max(len(sessions), 1),
        }


class DeliberatePracticeEngine:
    """
    Implements deliberate practice - focused practice on weaknesses.
    """
    def __init__(self):
        self.focus_threshold = 0.7  # Items below 70% mastery need focus

    def identify_weaknesses(
        self,
        items: List[KnowledgeItem]
    ) -> List[KnowledgeItem]:
        """
        Identify items that need focused practice.
        """
        return [
            item for item in items
            if item.mastery_level < self.focus_threshold * 100
        ]

    def create_practice_session(
        self,
        weak_items: List[KnowledgeItem],
        session_duration_minutes: int = 30
    ) -> Dict:
        """
        Create a focused practice session on weak areas.
        """
        # Sort by weakness (most weak first)
        sorted_items = sorted(weak_items, key=lambda x: x.mastery_level)

        # Estimate items per session (5 min per item)
        items_per_session = session_duration_minutes // 5

        selected = sorted_items[:items_per_session]

        return {
            "focus_area": "weakness_improvement",
            "items": selected,
            "estimated_duration": len(selected) * 5,
            "goals": [
                f"Improve mastery of {len(selected)} weak items",
                f"Target: 80%+ accuracy on each item",
                "Practice until confident"
            ],
            "methods": [
                "spaced_repetition",
                "active_recall",
                "elaboration"
            ]
        }


class AdvancedLearningEngine:
    """
    Master orchestrator for all learning methods.
    Implements the daily learning ritual: Collect → Digest → Filter → Apply
    """

    def __init__(self, data_path: str = None):
        self.scheduler = SpacedRepetitionScheduler()
        self.tester = ActiveRecallTester()
        self.chunker = ChunkingEngine()
        self.interleaver = InterleavingScheduler()
        self.elaborator = ElaborationEngine()
        self.metacognition = MetacognitionMonitor()
        self.practice_engine = DeliberatePracticeEngine()

        if data_path:
            self.data_path = Path(data_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.data_path = project_root / "data" / "memory"
            except Exception:
                self.data_path = Path.cwd() / "data" / "memory"
        self.data_path.mkdir(parents=True, exist_ok=True)

        self.state_path = self.data_path / "advanced_learning_state.json"
        self.max_items = int(os.getenv("ADVANCED_LEARNING_MAX_ITEMS", "800"))
        self.max_sessions = int(os.getenv("ADVANCED_LEARNING_MAX_SESSIONS", "500"))

        self.knowledge_items: List[KnowledgeItem] = []
        self.learning_sessions: List[LearningSession] = []
        self._load_state()

    def _safe_parse_time(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def _serialize_knowledge_item(self, item: KnowledgeItem) -> Dict:
        return {
            "id": item.id,
            "content": item.content,
            "category": item.category,
            "importance": item.importance,
            "created": item.created.isoformat(),
            "last_reviewed": item.last_reviewed.isoformat() if item.last_reviewed else None,
            "review_count": item.review_count,
            "ease_factor": item.ease_factor,
            "interval_days": item.interval_days,
            "next_review": item.next_review.isoformat() if item.next_review else None,
            "mastery_level": item.mastery_level,
            "tags": item.tags,
        }

    def _deserialize_knowledge_item(self, data: Dict) -> Optional[KnowledgeItem]:
        try:
            created = self._safe_parse_time(data.get("created")) or datetime.now()
            return KnowledgeItem(
                id=str(data.get("id", "")) or f"kn_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                content=str(data.get("content", "")),
                category=str(data.get("category", "general")),
                importance=int(data.get("importance", 5)),
                created=created,
                last_reviewed=self._safe_parse_time(data.get("last_reviewed")),
                review_count=int(data.get("review_count", 0)),
                ease_factor=float(data.get("ease_factor", 2.5)),
                interval_days=int(data.get("interval_days", 1)),
                next_review=self._safe_parse_time(data.get("next_review")),
                mastery_level=float(data.get("mastery_level", 0.0)),
                tags=[str(t) for t in data.get("tags", []) if isinstance(t, str)],
            )
        except Exception:
            return None

    def _serialize_session(self, session: LearningSession) -> Dict:
        return {
            "started": session.started.isoformat(),
            "method": session.method.value,
            "items_reviewed": session.items_reviewed,
            "items_learned": session.items_learned,
            "items_mastered": session.items_mastered,
            "questions_asked": session.questions_asked,
            "correct_answers": session.correct_answers,
            "duration_minutes": session.duration_minutes,
        }

    def _deserialize_session(self, data: Dict) -> Optional[LearningSession]:
        try:
            method_value = str(data.get("method", LearningMethod.SPACED_REPETITION.value))
            method = next(
                (m for m in LearningMethod if m.value == method_value),
                LearningMethod.SPACED_REPETITION,
            )
            return LearningSession(
                started=self._safe_parse_time(data.get("started")) or datetime.now(),
                method=method,
                items_reviewed=int(data.get("items_reviewed", 0)),
                items_learned=int(data.get("items_learned", 0)),
                items_mastered=int(data.get("items_mastered", 0)),
                questions_asked=int(data.get("questions_asked", 0)),
                correct_answers=int(data.get("correct_answers", 0)),
                duration_minutes=float(data.get("duration_minutes", 0.0)),
            )
        except Exception:
            return None

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Could not load advanced learning state: %s", exc)
            return

        knowledge_items = []
        for raw in data.get("knowledge_items", []):
            if not isinstance(raw, dict):
                continue
            item = self._deserialize_knowledge_item(raw)
            if item:
                knowledge_items.append(item)
        self.knowledge_items = knowledge_items

        sessions = []
        for raw in data.get("learning_sessions", []):
            if not isinstance(raw, dict):
                continue
            session = self._deserialize_session(raw)
            if session:
                sessions.append(session)
        self.learning_sessions = sessions

    def _save_state(self) -> None:
        try:
            self.data_path.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now().isoformat(),
                "knowledge_items": [self._serialize_knowledge_item(item) for item in self.knowledge_items],
                "learning_sessions": [self._serialize_session(session) for session in self.learning_sessions[-self.max_sessions:]],
            }
            tmp_path = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.state_path)
        except Exception as exc:
            logger.warning("Could not persist advanced learning state: %s", exc)

    # ==================== DAILY LEARNING RITUAL ====================

    def daily_collect(self, sources: List[Dict]) -> Dict:
        """
        Hour 0-1: Collect information from sources.
        """
        collected = []

        for source in sources:
            # In real implementation, fetch from actual sources
            items = source.get("items", [])
            collected.extend(items)

        return {
            "phase": "collect",
            "timestamp": datetime.now().isoformat(),
            "total_collected": len(collected),
            "items": collected
        }

    def daily_digest(self, items: List[Dict]) -> Dict:
        """
        Hour 1-2: Digest and understand the information.
        """
        digested = []

        for item in items:
            # Chunk into digestible pieces
            chunks = self.chunker.chunk_content(item.get("content", ""))

            digested.append({
                "original": item,
                "chunks": chunks,
                "key_points": [c["content"][:100] for c in chunks],
                "category": item.get("category", "general"),
                "importance": item.get("importance", 5)
            })

        return {
            "phase": "digest",
            "timestamp": datetime.now().isoformat(),
            "total_digested": len(digested),
            "items": digested
        }

    def daily_filter(self, items: List[Dict], min_score: float = 6.0) -> Dict:
        """
        Hour 2-3: Filter and keep only high-value items.
        """
        scored_items = []

        for item in items:
            # Calculate value score
            score = self._calculate_value_score(item)
            item["score"] = score
            scored_items.append(item)

        # Filter by score
        filtered = [item for item in scored_items if item["score"] >= min_score]
        filtered.sort(key=lambda x: x["score"], reverse=True)

        return {
            "phase": "filter",
            "timestamp": datetime.now().isoformat(),
            "total_scored": len(scored_items),
            "total_kept": len(filtered),
            "filter_rate": len(filtered) / max(len(scored_items), 1),
            "items": filtered
        }

    def daily_apply(self, items: List[Dict]) -> Dict:
        """
        Hour 3-4: Apply the knowledge.
        """
        applied = []
        proposals = []

        for item in items:
            # Generate improvement proposals
            proposal = {
                "id": f"prop_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hash(item.get('content', '')) % 10000:04d}",
                "source": item,
                "created": datetime.now().isoformat(),
                "status": "pending",
                "action": item.get("potential_improvement", "Review and integrate")
            }
            proposals.append(proposal)
            applied.append(item)

        return {
            "phase": "apply",
            "timestamp": datetime.now().isoformat(),
            "total_applied": len(applied),
            "proposals_generated": len(proposals),
            "proposals": proposals
        }

    def run_daily_cycle(self, sources: List[Dict]) -> Dict:
        """
        Run the complete daily learning cycle.
        """
        results = {}

        # Collect
        collect_result = self.daily_collect(sources)
        results["collect"] = collect_result

        # Digest
        digest_result = self.daily_digest(collect_result["items"])
        results["digest"] = digest_result

        # Filter
        filter_result = self.daily_filter(digest_result["items"])
        results["filter"] = filter_result

        # Apply
        apply_result = self.daily_apply(filter_result["items"])
        results["apply"] = apply_result

        return {
            "cycle": "daily",
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "collected": collect_result["total_collected"],
                "digested": digest_result["total_digested"],
                "filtered": filter_result["total_kept"],
                "applied": apply_result["total_applied"]
            },
            "phases": results
        }

    # ==================== SPACED REPETITION ====================

    def get_items_for_review(self) -> List[KnowledgeItem]:
        """
        Get items that are due for review.
        """
        now = datetime.now()
        due_items = [
            item for item in self.knowledge_items
            if item.next_review is None or item.next_review <= now
        ]
        due_items.sort(
            key=lambda item: (
                item.next_review.timestamp() if item.next_review else 0,
                -item.importance,
                item.created.timestamp(),
            )
        )
        return due_items

    def review_item(self, item: KnowledgeItem, quality: int) -> Dict:
        """
        Review an item and update its schedule.
        """
        quality = max(0, min(5, int(quality)))
        next_review, new_ease, new_interval = self.scheduler.calculate_next_review(item, quality)

        item.last_reviewed = datetime.now()
        item.review_count += 1
        item.ease_factor = new_ease
        item.interval_days = new_interval
        item.next_review = next_review

        # Update mastery level
        if quality >= 4:
            item.mastery_level = min(100, item.mastery_level + 10)
        elif quality <= 2:
            item.mastery_level = max(0, item.mastery_level - 20)

        self._save_state()

        return {
            "item_id": item.id,
            "quality": quality,
            "next_review": next_review.isoformat(),
            "mastery_level": item.mastery_level,
            "interval_days": new_interval
        }

    # ==================== HELPER METHODS ====================

    def _calculate_value_score(self, item: Dict) -> float:
        """Calculate the value score for an item."""
        score = 0

        # Importance weight
        score += item.get("importance", 5) * 0.5

        # Relevance to current goals (simplified)
        score += 3

        # Timeliness
        score += 2

        return min(10, score)

    def add_knowledge(self, content: str, category: str, importance: int = 5, tags: List[str] = None) -> KnowledgeItem:
        """Add a new knowledge item."""
        normalized_content = " ".join((content or "").split())
        normalized_category = (category or "general").strip().lower()
        normalized_tags = sorted(set(tags or []))

        for existing in self.knowledge_items:
            if (
                existing.content.strip().lower() == normalized_content.lower()
                and existing.category.strip().lower() == normalized_category
            ):
                existing.importance = max(existing.importance, int(importance))
                existing.tags = sorted(set(existing.tags) | set(normalized_tags))
                self._save_state()
                return existing

        fingerprint = hashlib.md5(
            f"{normalized_category}|{normalized_content.lower()}".encode("utf-8")
        ).hexdigest()[:12]
        item = KnowledgeItem(
            id=f"kn_{fingerprint}",
            content=normalized_content,
            category=normalized_category,
            importance=max(1, min(10, int(importance))),
            created=datetime.now(),
            tags=normalized_tags
        )
        self.knowledge_items.append(item)

        if self.max_items > 0 and len(self.knowledge_items) > self.max_items:
            self.knowledge_items.sort(
                key=lambda x: (x.importance, x.mastery_level, x.created.timestamp()),
                reverse=True,
            )
            self.knowledge_items = self.knowledge_items[:self.max_items]
            self.knowledge_items.sort(key=lambda x: x.created.timestamp())

        self._save_state()
        return item

    def get_learning_stats(self) -> Dict:
        """Get learning statistics."""
        return self.metacognition.calculate_learning_metrics(self.learning_sessions)


# ==================== CONVENIENCE FUNCTIONS ====================

_engine = None

def get_learning_engine() -> AdvancedLearningEngine:
    """Get singleton learning engine."""
    global _engine
    if _engine is None:
        _engine = AdvancedLearningEngine()
    return _engine


def run_daily_learning(sources: List[Dict] = None) -> Dict:
    """Run the daily learning cycle."""
    engine = get_learning_engine()
    return engine.run_daily_cycle(sources or [])


# ==================== TEST ====================

if __name__ == "__main__":
    print("🧠 Advanced Learning Engine")
    print("=" * 50)

    engine = AdvancedLearningEngine()

    # Add some test knowledge
    engine.add_knowledge(
        "First Principles Thinking: Break problems down to fundamental truths",
        "thinking",
        importance=9,
        tags=["reasoning", "problem-solving"]
    )

    engine.add_knowledge(
        "Spaced Repetition: Review at increasing intervals for long-term retention",
        "learning",
        importance=8,
        tags=["memory", "retention"]
    )

    # Test review scheduling
    items = engine.get_items_for_review()
    print(f"Items for review: {len(items)}")

    if items:
        result = engine.review_item(items[0], quality=4)
        print(f"Review result: {result}")

    # Test daily cycle
    test_sources = [{"items": [{"content": "Test knowledge item", "category": "test"}]}]
    cycle_result = engine.run_daily_cycle(test_sources)
    print(f"Daily cycle: {cycle_result['summary']}")

    print("✅ Advanced Learning Engine OK")
