"""
KNOWLEDGE DIGESTION & RAG SYSTEM
=================================
Hấp thụ thông tin giá trị - Loại bỏ noise - Tối ưu memory

PROBLEMS SOLVED:
1. Memory phình ra khi học liên tục
2. Cần trích xuất GIÁ TRỊ CỐT LÕI
3. Cần RAG để retrieve thông tin hiệu quả
4. Cần database cập nhật liên tục

ARCHITECTURE:
┌─────────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE PIPELINE                           │
│                                                                 │
│  RAW DATA → INGEST → PROCESS → EXTRACT → COMPRESS → STORE     │
│                              │                                  │
│                         ┌────▼────┐                             │
│                         │  RAG    │                             │
│                         │ ENGINE  │                             │
│                         └────┬────┘                             │
│                              │                                  │
│                    RETRIEVE ← QUERY                            │
└─────────────────────────────────────────────────────────────────┘
"""

import json
import os
import sys
import time
import threading
import hashlib
import re
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict, Counter
import random

try:
    from core.llm_caller import call_llm
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"
VECTOR_DIR = DATA_DIR / "vectors"


@dataclass
class KnowledgeChunk:
    """A chunk of processed knowledge"""
    id: str
    content: str
    summary: str  # Compressed version
    keywords: List[str]
    importance_score: float  # 0-1
    access_count: int
    last_accessed: str
    created_at: str
    source: str
    embedding: List[float] = None  # For RAG


@dataclass
class KnowledgeGraph:
    """Knowledge graph for relationships"""
    nodes: Dict[str, Dict]  # id -> node info
    edges: List[Tuple[str, str, str]]  # (from, to, relationship)


class KnowledgeDigestionEngine:
    """
    Tiêu hóa kiến thức - Hấp thụ giá trị - Loại bỏ noise
    """

    def __init__(self, brain=None):
        self.brain = brain
        self.data_dir = DATA_DIR
        self.vector_dir = VECTOR_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.vector_dir.mkdir(parents=True, exist_ok=True)

        # Storage
        self.chunks_file = self.data_dir / "knowledge_chunks.jsonl"
        self.graph_file = self.data_dir / "knowledge_graph.json"
        self.stats_file = self.data_dir / "digestion_stats.json"

        # In-memory
        self.chunks: Dict[str, KnowledgeChunk] = {}
        self.graph = KnowledgeGraph(nodes={}, edges=[])
        self.keyword_index: Dict[str, Set[str]] = defaultdict(set)
        self.importance_threshold = 0.3

        # Stats
        self.stats = {
            "total_processed": 0,
            "total_compressed": 0,
            "total_pruned": 0,
            "space_saved_bytes": 0,
        }

        self._load()
        self._start_digestion_thread()

    def _load(self):
        """Load existing data"""
        if self.chunks_file.exists():
            with open(self.chunks_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        chunk = KnowledgeChunk(**data)
                        self.chunks[chunk.id] = chunk
                        for kw in chunk.keywords:
                            self.keyword_index[kw].add(chunk.id)
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass

        if self.graph_file.exists():
            with open(self.graph_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.graph.nodes = data.get("nodes", {})
                self.graph.edges = [tuple(e) for e in data.get("edges", [])]

    def _save(self):
        """Save data"""
        with open(self.chunks_file, 'w', encoding='utf-8') as f:
            for chunk in self.chunks.values():
                f.write(json.dumps(vars(chunk), default=str) + "\n")

        with open(self.graph_file, 'w', encoding='utf-8') as f:
            json.dump({
                "nodes": self.graph.nodes,
                "edges": self.graph.edges
            }, f, indent=2)

        with open(self.stats_file, 'w', encoding='utf-8') as f:
            json.dump(self.stats, f, indent=2)

    # ==================== INGESTION ====================

    def ingest(self, content: str, source: str = "unknown", metadata: Dict = None) -> KnowledgeChunk:
        """Ingest raw knowledge"""
        # Create chunk
        chunk_id = hashlib.md5(f"{content}{datetime.now()}".encode()).hexdigest()[:12]

        # Extract keywords
        keywords = self._extract_keywords(content)

        # Calculate importance
        importance = self._calculate_importance(content, keywords, source)

        # Create summary (compression)
        summary = self._create_summary(content)

        chunk = KnowledgeChunk(
            id=chunk_id,
            content=content[:2000],  # Limit size
            summary=summary,
            keywords=keywords[:10],
            importance_score=importance,
            access_count=0,
            last_accessed=datetime.now().isoformat(),
            created_at=datetime.now().isoformat(),
            source=source
        )

        # Only keep if importance > threshold
        if importance >= self.importance_threshold:
            self.chunks[chunk_id] = chunk
            for kw in chunk.keywords:
                self.keyword_index[kw].add(chunk_id)

            # Update graph
            self._update_graph(chunk)

            self.stats["total_processed"] += 1
            self.stats["space_saved_bytes"] += len(content) - len(summary)

        self._save()
        return chunk

    def _extract_keywords(self, content: str) -> List[str]:
        """Extract keywords from content"""
        # Simple keyword extraction
        words = re.findall(r'\b[a-zA-Z]{3,}\b', content.lower())

        # Filter common words
        stop_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her', 'was', 'one', 'our', 'out'}
        words = [w for w in words if w not in stop_words]

        # Count and get top keywords
        word_counts = Counter(words)
        return [w for w, _ in word_counts.most_common(15)]

    def _calculate_importance(self, content: str, keywords: List[str], source: str) -> float:
        """Calculate importance score"""
        score = 0.5  # Base score

        # High-value keywords boost
        high_value_keywords = {
            "breakthrough", "revolutionary", "new", "important", "critical",
            "essential", "best", "top", "key", "fundamental", "advanced"
        }
        for kw in keywords:
            if kw in high_value_keywords:
                score += 0.1

        # Source quality boost
        high_quality_sources = {"github", "arxiv", "nature", "openai", "anthropic"}
        for hqs in high_quality_sources:
            if hqs in source.lower():
                score += 0.1
                break

        # Length penalty (too short = less info)
        if len(content) < 100:
            score -= 0.2
        elif len(content) > 5000:
            score -= 0.1  # Too long might be noise

        return min(1.0, max(0.0, score))

    def _create_summary(self, content: str) -> str:
        """Create compressed summary using LLM or heuristic fallback."""
        if _LLM_AVAILABLE:
            try:
                result = call_llm(
                    prompt=f'Summarize this knowledge content in 1-2 concise sentences, preserving key facts:\n{content[:1000]}',
                    task_type='general',
                    max_tokens=150,
                    temperature=0.2,
                )
                if isinstance(result, str) and result.strip():
                    summary = result.strip()
                    return summary[:500] if len(summary) > 500 else summary
            except Exception:
                pass

        # Fallback: extract first 2 sentences
        sentences = content.split('. ')
        if len(sentences) >= 2:
            summary = sentences[0] + '. ' + sentences[1]
        else:
            summary = content
        return summary[:500] if len(summary) > 500 else summary

    def _update_graph(self, chunk: KnowledgeChunk):
        """Update knowledge graph"""
        # Add node
        self.graph.nodes[chunk.id] = {
            "summary": chunk.summary,
            "importance": chunk.importance_score,
            "source": chunk.source
        }

        # Find related nodes and add edges
        for kw in chunk.keywords:
            for other_id in self.keyword_index.get(kw, []):
                if other_id != chunk.id:
                    # Check if edge already exists
                    edge = (chunk.id, other_id, "related")
                    if edge not in self.graph.edges:
                        self.graph.edges.append(edge)

    # ==================== COMPRESSION & PRUNING ====================

    def compress_old_chunks(self, days_old: int = 30) -> int:
        """Compress old chunks to save space"""
        compressed = 0
        threshold = datetime.now() - timedelta(days=days_old)

        for chunk_id, chunk in list(self.chunks.items()):
            created = datetime.fromisoformat(chunk.created_at)
            if created < threshold and chunk.access_count < 3:
                # Low access + old = compress
                chunk.content = chunk.summary  # Replace with summary
                compressed += 1
                self.stats["total_compressed"] += 1

        self._save()
        return compressed

    def prune_low_value(self) -> int:
        """Remove lowest value chunks"""
        pruned = 0

        # Sort by importance * access count
        scored = [
            (chunk_id, chunk.importance_score * (1 + chunk.access_count * 0.1))
            for chunk_id, chunk in self.chunks.items()
        ]
        scored.sort(key=lambda x: x[1])

        # Remove bottom 10%
        to_remove = max(1, len(scored) // 10)
        for chunk_id, _ in scored[:to_remove]:
            if self.chunks[chunk_id].importance_score < 0.4:
                # Remove from indexes
                for kw in self.chunks[chunk_id].keywords:
                    self.keyword_index[kw].discard(chunk_id)
                del self.chunks[chunk_id]
                pruned += 1
                self.stats["total_pruned"] += 1

        self._save()
        return pruned

    # ==================== RAG - RETRIEVAL ====================

    def retrieve(self, query: str, limit: int = 10) -> List[KnowledgeChunk]:
        """Retrieve relevant knowledge using RAG-like approach"""
        # Extract query keywords
        query_keywords = self._extract_keywords(query)

        # Score chunks by keyword overlap
        scores: Dict[str, float] = defaultdict(float)

        for kw in query_keywords:
            for chunk_id in self.keyword_index.get(kw, []):
                chunk = self.chunks[chunk_id]
                # Score = keyword match + importance + recency
                recency_boost = 1.0
                if chunk.last_accessed:
                    last = datetime.fromisoformat(chunk.last_accessed)
                    days_ago = (datetime.now() - last).days
                    recency_boost = 1.0 / (1 + days_ago / 30)

                scores[chunk_id] += (
                    chunk.importance_score * 0.4 +
                    (1 + chunk.access_count * 0.05) * 0.3 +
                    recency_boost * 0.3
                )

        # Sort and return top
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:limit]

        results = []
        for chunk_id in sorted_ids:
            chunk = self.chunks[chunk_id]
            chunk.access_count += 1
            chunk.last_accessed = datetime.now().isoformat()
            results.append(chunk)

        self._save()
        return results

    def semantic_search(self, query: str, limit: int = 10) -> List[KnowledgeChunk]:
        """Semantic search (simplified - would use embeddings in production)"""
        # For now, use keyword-based
        return self.retrieve(query, limit)

    # ==================== KNOWLEDGE SYNTHESIS ====================

    def synthesize(self, topic: str) -> Dict:
        """Synthesize knowledge about a topic"""
        chunks = self.retrieve(topic, limit=20)

        if not chunks:
            return {"topic": topic, "summary": "No knowledge found", "sources": []}

        # Combine summaries
        summaries = [c.summary for c in chunks if c.summary]
        combined = " ".join(summaries)

        # Extract key points
        all_keywords = []
        for c in chunks:
            all_keywords.extend(c.keywords)

        top_keywords = Counter(all_keywords).most_common(10)

        return {
            "topic": topic,
            "summary": combined[:1000],
            "key_concepts": [k for k, _ in top_keywords],
            "sources": list(set(c.source for c in chunks)),
            "confidence": min(1.0, len(chunks) / 10)
        }

    # ==================== SELF-REVIEW ====================

    def self_review(self) -> Dict:
        """Review own knowledge base health"""
        review = {
            "timestamp": datetime.now().isoformat(),
            "total_chunks": len(self.chunks),
            "total_keywords": len(self.keyword_index),
            "graph_nodes": len(self.graph.nodes),
            "graph_edges": len(self.graph.edges),
            "avg_importance": 0,
            "avg_access": 0,
            "recommendations": []
        }

        if self.chunks:
            review["avg_importance"] = sum(c.importance_score for c in self.chunks.values()) / len(self.chunks)
            review["avg_access"] = sum(c.access_count for c in self.chunks.values()) / len(self.chunks)

        # Recommendations
        if review["avg_importance"] < 0.5:
            review["recommendations"].append("Increase importance threshold")

        if review["avg_access"] < 1:
            review["recommendations"].append("Knowledge not being utilized - improve retrieval")

        if len(self.chunks) > 10000:
            review["recommendations"].append("Consider compression - over 10000 chunks")

        return review

    # ==================== BACKGROUND MAINTENANCE ====================

    def _digestion_loop(self):
        """Background maintenance loop"""
        while True:
            try:
                # Compress old chunks daily
                compressed = self.compress_old_chunks(days_old=7)
                if compressed > 0:
                    print(f"[DIGESTION] Compressed {compressed} old chunks")

                # Prune low value weekly
                if datetime.now().hour == 3:  # 3 AM
                    pruned = self.prune_low_value()
                    if pruned > 0:
                        print(f"[DIGESTION] Pruned {pruned} low-value chunks")

                # Self review
                review = self.self_review()
                if review["recommendations"]:
                    print(f"[DIGESTION] Review: {review['recommendations']}")

                time.sleep(3600)  # Check hourly

            except Exception as e:
                print(f"[DIGESTION] Error: {e}")
                time.sleep(300)

    def _start_digestion_thread(self):
        """Start background thread"""
        thread = threading.Thread(target=self._digestion_loop, daemon=True)
        thread.start()

    # ==================== STATS ====================

    def get_stats(self) -> Dict:
        """Get digestion statistics"""
        return {
            **self.stats,
            "total_chunks": len(self.chunks),
            "total_keywords": len(self.keyword_index),
            "graph_size": len(self.graph.nodes),
            "avg_importance": sum(c.importance_score for c in self.chunks.values()) / max(len(self.chunks), 1)
        }


# ==================== CONVENIENCE ====================

_digestion: Optional[KnowledgeDigestionEngine] = None


def get_digestion() -> KnowledgeDigestionEngine:
    """Get singleton digestion engine"""
    global _digestion
    if _digestion is None:
        _digestion = KnowledgeDigestionEngine()
    return _digestion


def ingest_knowledge(content: str, source: str = "unknown") -> KnowledgeChunk:
    """Ingest knowledge"""
    return get_digestion().ingest(content, source)


def retrieve_knowledge(query: str, limit: int = 10) -> List[KnowledgeChunk]:
    """Retrieve knowledge"""
    return get_digestion().retrieve(query, limit)


def synthesize_knowledge(topic: str) -> Dict:
    """Synthesize knowledge about topic"""
    return get_digestion().synthesize(topic)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("KNOWLEDGE DIGESTION & RAG SYSTEM")
    print("=" * 70)

    engine = get_digestion()

    # Test ingestion
    print("\nIngesting test knowledge...")
    engine.ingest("Python is a programming language", "test")
    engine.ingest("Machine learning uses algorithms to learn from data", "test")
    engine.ingest("AI agents can autonomously execute tasks without human intervention", "test")

    # Test retrieval
    print("\nRetrieving knowledge about 'AI'...")
    results = engine.retrieve("AI programming")
    for r in results[:3]:
        print(f"  - {r.summary}")

    # Test synthesis
    print("\nSynthesizing knowledge about 'machine learning'...")
    synthesis = engine.synthesize("machine learning")
    print(f"  Summary: {synthesis['summary'][:100]}...")
    print(f"  Concepts: {synthesis['key_concepts']}")

    # Stats
    print("\nStats:", engine.get_stats())

    print("\nDigestion engine running in background...")
