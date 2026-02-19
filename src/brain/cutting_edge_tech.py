"""
CUTTING-EDGE TECHNOLOGY INTEGRATION
===================================
Tích hợp các công nghệ tiên tiến nhất vào NEXUS

STATE-OF-THE-ART TECHNOLOGIES (2025-2026):
├── RAG EVOLUTION
│   ├── GraphRAG (Microsoft) - Knowledge Graph-based RAG
│   ├── HyDE - Hypothetical Document Embeddings
│   ├── Multi-Vector Indexing
│   └── Hybrid Search (BM25 + Vector)
│
├── MEMORY ARCHITECTURES
│   ├── MemGPT/Letta - Hierarchical Memory
│   ├── Mem0 - Persistent Memory Layer
│   ├── Episodic vs Semantic Separation
│   └── Infinite Context via Compression
│
├── AGENT FRAMEWORKS
│   ├── ReAct (Reasoning + Acting)
│   ├── Plan-and-Execute
│   ├── Multi-Agent Orchestration
│   └── Self-Reflection Loops
│
├── VECTOR DATABASES
│   ├── Qdrant - High Performance
│   ├── Chroma - Lightweight
│   ├── Milvus - Scalable
│   └── LanceDB - Serverless
│
└── EFFICIENCY TECHNIQUES
    ├── Mixture of Experts (MoE)
    ├── State Space Models (SSM/Mamba)
    ├── Ring Attention (Infinite Context)
    ├── KV Cache Optimization
    └── Speculative Decoding
"""

import json
import os
import sys
import time
import threading
import hashlib
import math
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
import random

try:
    from core.llm_caller import call_llm
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"


def cosine_similarity(vector_a: List[float], vector_b: List[float]) -> float:
    """Compute cosine similarity between 2 vectors.

    Safely handles zero-norm vectors by returning 0.0.
    """
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a > 0 and norm_b > 0:
        return dot / (norm_a * norm_b)
    return 0.0


@dataclass
class TechInnovation:
    """A cutting-edge technology innovation"""
    name: str
    category: str
    description: str
    source: str  # paper, github, company
    maturity: str  # experimental, early, production
    nexus_applicability: float  # 0-1
    implementation_complexity: str  # low, medium, high
    integration_status: str  # planned, implementing, integrated


class CuttingEdgeIntegrator:
    """
    Tích hợp công nghệ tiên tiến nhất vào NEXUS
    """

    def __init__(self, brain=None):
        self.brain = brain
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.innovations_file = self.data_dir / "tech_innovations.json"
        self.innovations: List[TechInnovation] = []

        # Initialize known innovations
        self._init_innovations()
        self._load()

    def _init_innovations(self):
        """Initialize known cutting-edge technologies"""

        innovations = [
            # RAG Evolution
            TechInnovation(
                name="GraphRAG",
                category="rag",
                description="Knowledge Graph-based RAG for better entity relationships and global understanding",
                source="Microsoft Research",
                maturity="early",
                nexus_applicability=0.95,
                implementation_complexity="high",
                integration_status="planned"
            ),
            TechInnovation(
                name="HyDE",
                category="rag",
                description="Hypothetical Document Embeddings - generate hypothetical answers to improve retrieval",
                source="Research Paper",
                maturity="production",
                nexus_applicability=0.85,
                implementation_complexity="medium",
                integration_status="planned"
            ),
            TechInnovation(
                name="Multi-Vector Indexing",
                category="rag",
                description="Store multiple vector representations per document for better retrieval",
                source="LangChain",
                maturity="production",
                nexus_applicability=0.9,
                implementation_complexity="medium",
                integration_status="planned"
            ),
            TechInnovation(
                name="Hybrid Search",
                category="rag",
                description="Combine BM25 keyword search with vector similarity for best results",
                source="Industry Standard",
                maturity="production",
                nexus_applicability=0.95,
                implementation_complexity="low",
                integration_status="planned"
            ),

            # Memory Systems
            TechInnovation(
                name="MemGPT Architecture",
                category="memory",
                description="Hierarchical memory with main context, working memory, and archival storage",
                source="UC Berkeley / Letta",
                maturity="production",
                nexus_applicability=0.95,
                implementation_complexity="high",
                integration_status="planned"
            ),
            TechInnovation(
                name="Mem0",
                category="memory",
                description="Persistent memory layer with automatic memory extraction and update",
                source="Mem0 AI",
                maturity="production",
                nexus_applicability=0.9,
                implementation_complexity="medium",
                integration_status="planned"
            ),
            TechInnovation(
                name="Episodic Memory Separation",
                category="memory",
                description="Separate episodic (events) from semantic (facts) memory for better recall",
                source="Cognitive Science",
                maturity="research",
                nexus_applicability=0.85,
                implementation_complexity="medium",
                integration_status="planned"
            ),
            TechInnovation(
                name="Memory Compression",
                category="memory",
                description="Compress old memories into summaries while keeping recent details",
                source="Anthropic",
                maturity="production",
                nexus_applicability=0.95,
                implementation_complexity="medium",
                integration_status="planned"
            ),

            # Agent Frameworks
            TechInnovation(
                name="ReAct Pattern",
                category="agent",
                description="Reasoning + Acting interleaving for better task execution",
                source="Research Paper",
                maturity="production",
                nexus_applicability=0.95,
                implementation_complexity="low",
                integration_status="planned"
            ),
            TechInnovation(
                name="Plan-and-Execute",
                category="agent",
                description="First plan, then execute - better for complex multi-step tasks",
                source="LangChain",
                maturity="production",
                nexus_applicability=0.9,
                implementation_complexity="medium",
                integration_status="planned"
            ),
            TechInnovation(
                name="Multi-Agent Orchestration",
                category="agent",
                description="Multiple specialized agents working together",
                source="CrewAI, AutoGen",
                maturity="production",
                nexus_applicability=0.95,
                implementation_complexity="high",
                integration_status="planned"
            ),
            TechInnovation(
                name="Self-Reflection Loop",
                category="agent",
                description="Agent reviews own output and improves before responding",
                source="Reflexion Paper",
                maturity="production",
                nexus_applicability=0.9,
                implementation_complexity="medium",
                integration_status="planned"
            ),

            # Efficiency
            TechInnovation(
                name="Mixture of Experts (MoE)",
                category="efficiency",
                description="Route inputs to specialized sub-models for efficiency",
                source="Google, Mistral",
                maturity="production",
                nexus_applicability=0.7,
                implementation_complexity="high",
                integration_status="planned"
            ),
            TechInnovation(
                name="State Space Models (Mamba)",
                category="efficiency",
                description="Linear time complexity alternative to transformers",
                source="CMU",
                maturity="early",
                nexus_applicability=0.6,
                implementation_complexity="high",
                integration_status="planned"
            ),
            TechInnovation(
                name="Ring Attention",
                category="efficiency",
                description="Theoretically infinite context via ring communication",
                source="Berkeley",
                maturity="research",
                nexus_applicability=0.8,
                implementation_complexity="high",
                integration_status="planned"
            ),
            TechInnovation(
                name="KV Cache Optimization",
                category="efficiency",
                description="Optimize key-value cache for faster inference",
                source="vLLM",
                maturity="production",
                nexus_applicability=0.85,
                implementation_complexity="medium",
                integration_status="planned"
            ),
        ]

        self.innovations = innovations

    def _load(self):
        """Load saved innovations"""
        if self.innovations_file.exists():
            with open(self.innovations_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data.get("custom_innovations", []):
                    self.innovations.append(TechInnovation(**item))

    def _save(self):
        """Save innovations"""
        with open(self.innovations_file, 'w', encoding='utf-8') as f:
            json.dump({
                "innovations": [vars(i) for i in self.innovations],
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    def get_priority_implementations(self) -> List[TechInnovation]:
        """Get innovations sorted by priority for NEXUS"""
        # Sort by: applicability * (1/complexity_factor) * maturity_factor
        complexity_factor = {"low": 1.0, "medium": 0.7, "high": 0.4}
        maturity_factor = {"production": 1.0, "early": 0.7, "research": 0.4}

        scored = []
        for inn in self.innovations:
            if inn.integration_status == "integrated":
                continue

            score = (
                inn.nexus_applicability *
                complexity_factor.get(inn.implementation_complexity, 0.5) *
                maturity_factor.get(inn.maturity, 0.5)
            )
            scored.append((score, inn))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [inn for _, inn in scored]

    def generate_implementation_plan(self) -> Dict:
        """Generate implementation plan for top innovations"""
        priority = self.get_priority_implementations()[:5]

        plan = {
            "generated_at": datetime.now().isoformat(),
            "phases": []
        }

        # Phase 1: Quick wins (low complexity, high impact)
        quick_wins = [i for i in priority if i.implementation_complexity == "low"]
        if quick_wins:
            plan["phases"].append({
                "phase": 1,
                "name": "Quick Wins",
                "duration": "1-2 weeks",
                "items": [{"name": i.name, "description": i.description} for i in quick_wins]
            })

        # Phase 2: Medium complexity
        medium = [i for i in priority if i.implementation_complexity == "medium"]
        if medium:
            plan["phases"].append({
                "phase": 2,
                "name": "Core Upgrades",
                "duration": "2-4 weeks",
                "items": [{"name": i.name, "description": i.description} for i in medium]
            })

        # Phase 3: High complexity
        high = [i for i in priority if i.implementation_complexity == "high"]
        if high:
            plan["phases"].append({
                "phase": 3,
                "name": "Major Overhauls",
                "duration": "1-3 months",
                "items": [{"name": i.name, "description": i.description} for i in high]
            })

        return plan


class AdvancedRAGSystem:
    """
    RAG System với cutting-edge techniques:
    - Hybrid Search (BM25 + Vector)
    - Multi-vector indexing
    - Re-ranking
    - Query expansion
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.index_file = self.data_dir / "advanced_rag_index.json"

        # In-memory index
        self.documents: Dict[str, Dict] = {}
        self.bm25_index: Dict[str, Dict] = defaultdict(lambda: defaultdict(int))
        self.vector_index: Dict[str, List[float]] = {}

        self._load()

    def _load(self):
        """Load index"""
        if self.index_file.exists():
            with open(self.index_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.documents = data.get("documents", {})
                loaded_bm25 = data.get("bm25_index", {})
                loaded_vectors = data.get("vector_index", {})
                if loaded_bm25:
                    self.bm25_index = defaultdict(lambda: defaultdict(int))
                    for term, postings in loaded_bm25.items():
                        self.bm25_index[term] = defaultdict(int, {doc_id: int(count) for doc_id, count in postings.items()})
                if loaded_vectors:
                    self.vector_index = {doc_id: [float(v) for v in vec] for doc_id, vec in loaded_vectors.items()}
                if self.documents and (not loaded_bm25 or not loaded_vectors):
                    self._rebuild_indexes()

    def _save(self):
        """Save index"""
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump({
                "documents": self.documents,
                "bm25_index": {term: dict(postings) for term, postings in self.bm25_index.items()},
                "vector_index": self.vector_index,
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    def _rebuild_indexes(self):
        """Rebuild BM25 and vector indexes from documents."""
        self.bm25_index = defaultdict(lambda: defaultdict(int))
        self.vector_index = {}
        for doc_id, doc in self.documents.items():
            content = str(doc.get("content", ""))
            words = re.findall(r'\b\w+\b', content.lower())
            for word in words:
                self.bm25_index[word][doc_id] += 1
            self.vector_index[doc_id] = self._pseudo_embed(content)

    def index_document(self, doc_id: str, content: str, metadata: Dict = None):
        """Index a document with hybrid approach"""
        # Store document
        self.documents[doc_id] = {
            "content": content,
            "metadata": metadata or {},
            "indexed_at": datetime.now().isoformat()
        }

        # Build BM25 index (keyword frequencies)
        words = re.findall(r'\b\w+\b', content.lower())
        for word in words:
            self.bm25_index[word][doc_id] += 1

        # Simulate vector embedding (in production would use actual embedding model)
        # For now, use simple hash-based pseudo-vectors
        self.vector_index[doc_id] = self._pseudo_embed(content)

        self._save()

    def _pseudo_embed(self, text: str) -> List[float]:
        """Generate embedding vector using LLM or hash-based fallback."""
        if _LLM_AVAILABLE:
            try:
                # Use LLM to generate a compact semantic fingerprint
                raw = call_llm(
                    prompt=f'Generate a semantic fingerprint for this text. Return ONLY a JSON array of exactly 128 float values between -1 and 1 that represent the semantic meaning. Text: {text[:500]}',
                    task_type='general',
                    max_tokens=600,
                    temperature=0.0,
                )
                if isinstance(raw, str):
                    raw = raw.strip()
                    if raw.startswith('```'):
                        lines = raw.splitlines()
                        if len(lines) >= 3:
                            raw = '\n'.join(lines[1:-1]).strip()
                    import json as _json
                    parsed = _json.loads(raw)
                    if isinstance(parsed, list) and len(parsed) >= 64:
                        vector = [float(v) for v in parsed[:128]]
                        while len(vector) < 128:
                            vector.append(0.0)
                        norm = math.sqrt(sum(v * v for v in vector)) or 1
                        return [v / norm for v in vector]
            except Exception:
                pass

        # Fallback: TF-based pseudo-vector
        words = text.lower().split()
        vector = [0.0] * 128
        for i, word in enumerate(words[:128]):
            vector[i % 128] += hash(word) % 100 / 100
        norm = math.sqrt(sum(v * v for v in vector)) or 1
        return [v / norm for v in vector]

    def hybrid_search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        Hybrid search combining:
        1. BM25 keyword matching
        2. Vector similarity
        3. Re-ranking
        """
        # BM25 scores
        bm25_scores: Dict[str, float] = defaultdict(float)
        query_words = re.findall(r'\b\w+\b', query.lower())
        for word in query_words:
            for doc_id, count in self.bm25_index[word].items():
                # Simplified BM25
                bm25_scores[doc_id] += count / (1 + count)

        # Vector similarity
        query_vector = self._pseudo_embed(query)
        vector_scores: Dict[str, float] = {}
        for doc_id, doc_vector in self.vector_index.items():
            vector_scores[doc_id] = cosine_similarity(query_vector, doc_vector)

        # Combine scores (hybrid)
        combined_scores: Dict[str, float] = {}
        all_docs = set(bm25_scores.keys()) | set(vector_scores.keys())
        for doc_id in all_docs:
            combined_scores[doc_id] = (
                0.4 * bm25_scores.get(doc_id, 0) +
                0.6 * vector_scores.get(doc_id, 0)
            )

        # Sort and return top-k
        sorted_docs = sorted(combined_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for doc_id, score in sorted_docs:
            if doc_id in self.documents:
                results.append({
                    "doc_id": doc_id,
                    "content": self.documents[doc_id]["content"][:500],
                    "score": score,
                    "bm25_score": bm25_scores.get(doc_id, 0),
                    "vector_score": vector_scores.get(doc_id, 0)
                })

        return results


class HierarchicalMemorySystem:
    """
    MemGPT-inspired hierarchical memory:
    - Level 1: Main Context (immediate)
    - Level 2: Working Memory (recent)
    - Level 3: Archival Memory (compressed)
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.memory_file = self.data_dir / "hierarchical_memory.json"

        # Memory tiers
        self.main_context: List[Dict] = []  # Last 10 items
        self.working_memory: List[Dict] = []  # Last 100 items
        self.archival_memory: List[Dict] = []  # Compressed summaries

        # Thresholds
        self.main_context_limit = 10
        self.working_memory_limit = 100
        self.compression_threshold = 50

        self._load()

    def _load(self):
        """Load memory"""
        if self.memory_file.exists():
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.main_context = data.get("main_context", [])
                self.working_memory = data.get("working_memory", [])
                self.archival_memory = data.get("archival_memory", [])

    def _save(self):
        """Save memory"""
        with open(self.memory_file, 'w', encoding='utf-8') as f:
            json.dump({
                "main_context": self.main_context,
                "working_memory": self.working_memory,
                "archival_memory": self.archival_memory,
                "stats": {
                    "main_count": len(self.main_context),
                    "working_count": len(self.working_memory),
                    "archival_count": len(self.archival_memory)
                },
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    def add_memory(self, content: str, memory_type: str = "event", importance: float = 0.5):
        """Add memory to appropriate tier"""
        memory_item = {
            "id": hashlib.md5(f"{content}{datetime.now()}".encode()).hexdigest()[:8],
            "content": content,
            "type": memory_type,
            "importance": importance,
            "created_at": datetime.now().isoformat(),
            "access_count": 0
        }

        # High importance goes to main context
        if importance >= 0.8:
            self.main_context.append(memory_item)
            if len(self.main_context) > self.main_context_limit:
                # Move oldest to working memory
                moved = self.main_context.pop(0)
                self.working_memory.append(moved)

        # Medium goes to working memory
        else:
            self.working_memory.append(memory_item)

        # Check if compression needed
        if len(self.working_memory) > self.compression_threshold:
            self._compress_working_memory()

        self._save()

    def _compress_working_memory(self):
        """Compress old working memory into archival summaries"""
        # Take oldest 25 items
        to_compress = self.working_memory[:25]
        self.working_memory = self.working_memory[25:]

        # Create summary (in production would use LLM)
        summary = {
            "id": f"summary_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "type": "compressed_summary",
            "content": self._create_summary(to_compress),
            "source_count": len(to_compress),
            "time_range": {
                "start": to_compress[0]["created_at"] if to_compress else None,
                "end": to_compress[-1]["created_at"] if to_compress else None
            },
            "created_at": datetime.now().isoformat()
        }

        self.archival_memory.append(summary)
        self._save()

    def _create_summary(self, items: List[Dict]) -> str:
        """Create summary from items using LLM or heuristic fallback."""
        if _LLM_AVAILABLE:
            try:
                contents = '\n'.join(
                    f'- [{item.get("type", "event")}] {item.get("content", "")[:200]}'
                    for item in items[:15]
                )
                summary = call_llm(
                    prompt=f'Summarize these {len(items)} memory events into a concise paragraph (max 3 sentences):\n{contents}',
                    task_type='general',
                    max_tokens=200,
                    temperature=0.3,
                )
                if isinstance(summary, str) and summary.strip():
                    return summary.strip()[:500]
            except Exception:
                pass

        # Fallback: heuristic summary
        types = defaultdict(int)
        for item in items:
            types[item.get("type", "unknown")] += 1
        summary_parts = [f'{count} {t}' for t, count in types.items()]
        return f'Compressed memory containing: {", ".join(summary_parts)}'

    def retrieve(self, query: str, top_k: int = 10) -> List[Dict]:
        """Retrieve from all memory tiers"""
        results = []

        # Search main context first (highest priority)
        for item in self.main_context:
            if query.lower() in item["content"].lower():
                item["tier"] = "main"
                item["access_count"] += 1
                results.append(item)

        # Then working memory
        for item in self.working_memory:
            if query.lower() in item["content"].lower():
                item["tier"] = "working"
                item["access_count"] += 1
                results.append(item)

        # Finally archival
        for item in self.archival_memory:
            if query.lower() in item["content"].lower():
                item["tier"] = "archival"
                results.append(item)

        self._save()
        return results[:top_k]

    def get_stats(self) -> Dict:
        """Get memory statistics"""
        return {
            "main_context": len(self.main_context),
            "working_memory": len(self.working_memory),
            "archival_memory": len(self.archival_memory),
            "total_items": len(self.main_context) + len(self.working_memory) + len(self.archival_memory)
        }


# ==================== CONVENIENCE ====================

_integrator: Optional[CuttingEdgeIntegrator] = None
_rag: Optional[AdvancedRAGSystem] = None
_memory: Optional[HierarchicalMemorySystem] = None


def get_integrator() -> CuttingEdgeIntegrator:
    global _integrator
    if _integrator is None:
        _integrator = CuttingEdgeIntegrator()
    return _integrator


def get_advanced_rag() -> AdvancedRAGSystem:
    global _rag
    if _rag is None:
        _rag = AdvancedRAGSystem()
    return _rag


def get_hierarchical_memory() -> HierarchicalMemorySystem:
    global _memory
    if _memory is None:
        _memory = HierarchicalMemorySystem()
    return _memory


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("CUTTING-EDGE TECHNOLOGY INTEGRATION")
    print("=" * 70)

    integrator = get_integrator()

    print("\nPriority Implementations:")
    for i, inn in enumerate(integrator.get_priority_implementations()[:10], 1):
        print(f"{i}. {inn.name} ({inn.category})")
        print(f"   Applicability: {inn.nexus_applicability:.0%}")
        print(f"   Complexity: {inn.implementation_complexity}")
        print()

    print("\nImplementation Plan:")
    plan = integrator.generate_implementation_plan()
    for phase in plan["phases"]:
        print(f"\nPhase {phase['phase']}: {phase['name']} ({phase['duration']})")
        for item in phase["items"]:
            print(f"  - {item['name']}")

    # Test RAG
    print("\n" + "=" * 70)
    print("Testing Advanced RAG")
    print("=" * 70)

    rag = get_advanced_rag()
    rag.index_document("doc1", "AI agents can autonomously execute complex tasks without human intervention")
    rag.index_document("doc2", "RAG systems combine retrieval with generation for better accuracy")
    rag.index_document("doc3", "Memory systems help AI maintain context across conversations")

    results = rag.hybrid_search("AI memory system")
    print(f"\nSearch results for 'AI memory system':")
    for r in results:
        print(f"  - {r['doc_id']}: score={r['score']:.3f}")

    # Test Hierarchical Memory
    print("\n" + "=" * 70)
    print("Testing Hierarchical Memory")
    print("=" * 70)

    memory = get_hierarchical_memory()
    memory.add_memory("User prefers autonomous execution", importance=0.9)
    memory.add_memory("System learned new optimization technique", importance=0.6)

    print(f"\nMemory stats: {memory.get_stats()}")
