"""
NEXUS INTEGRATION HUB
====================
Kết nối tất cả cutting-edge systems vào một unified platform

INTEGRATED SYSTEMS:
├── ReAct Agent with Self-Reflection
├── Advanced RAG with Hybrid Search
├── Hierarchical Memory (MemGPT-style)
├── Knowledge Digestion & Compression
├── Auto-Evolution & Discovery
├── Self-Review & Meta-Learning
└── R&D Department

USAGE:
    from brain import NexusIntegrationHub

    hub = NexusIntegrationHub()
    hub.initialize()

    # Execute task with all systems
    result = hub.execute("Research quantum computing advances")
"""

import json
import os
import sys
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"


class NexusIntegrationHub:
    """
    Central Hub integrating all NEXUS systems
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.status_file = self.data_dir / "hub_status.json"
        self.initialized = False

        # System components (lazy loaded)
        self._brain = None
        self._scout = None
        self._discovery = None
        self._digestion = None
        self._rag = None
        self._memory = None
        self._review = None
        self._rnd = None
        self._agent = None
        self._integrator = None

        # Status
        self.status = {
            "initialized": False,
            "systems": {},
            "last_activity": None,
            "total_tasks": 0,
            "total_knowledge": 0
        }

        self._load_status()

    def _load_status(self):
        """Load status"""
        if self.status_file.exists():
            with open(self.status_file, 'r') as f:
                self.status.update(json.load(f))

    def _save_status(self):
        """Save status"""
        with open(self.status_file, 'w') as f:
            json.dump({
                **self.status,
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    # ==================== LAZY LOADING ====================

    @property
    def brain(self):
        if self._brain is None:
            try:
                from .nexus_brain import get_brain
                self._brain = get_brain()
            except:
                pass
        return self._brain

    @property
    def scout(self):
        if self._scout is None:
            try:
                from .omniscient_scout import get_scout
                self._scout = get_scout()
            except:
                pass
        return self._scout

    @property
    def discovery(self):
        if self._discovery is None:
            try:
                from .auto_evolution import get_discovery
                self._discovery = get_discovery()
            except:
                pass
        return self._discovery

    @property
    def digestion(self):
        if self._digestion is None:
            try:
                from .knowledge_digestion import get_digestion
                self._digestion = get_digestion()
            except:
                pass
        return self._digestion

    @property
    def rag(self):
        if self._rag is None:
            try:
                from .cutting_edge_tech import get_advanced_rag
                self._rag = get_advanced_rag()
            except:
                pass
        return self._rag

    @property
    def memory(self):
        if self._memory is None:
            try:
                from .cutting_edge_tech import get_hierarchical_memory
                self._memory = get_hierarchical_memory()
            except:
                pass
        return self._memory

    @property
    def review(self):
        if self._review is None:
            try:
                from .meta_evolution import get_review
                self._review = get_review()
            except:
                pass
        return self._review

    @property
    def rnd(self):
        if self._rnd is None:
            try:
                from .rd_lab import get_rnd
                self._rnd = get_rnd()
            except:
                pass
        return self._rnd

    @property
    def agent(self):
        if self._agent is None:
            try:
                from .react_agent import get_agent
                self._agent = get_agent()
            except:
                pass
        return self._agent

    @property
    def integrator(self):
        if self._integrator is None:
            try:
                from .cutting_edge_tech import get_integrator
                self._integrator = get_integrator()
            except:
                pass
        return self._integrator

    # ==================== INITIALIZATION ====================

    def initialize(self) -> Dict:
        """Initialize all systems"""
        results = {
            "timestamp": datetime.now().isoformat(),
            "systems": {}
        }

        systems_to_init = [
            ("brain", self.brain),
            ("scout", self.scout),
            ("discovery", self.discovery),
            ("digestion", self.digestion),
            ("rag", self.rag),
            ("memory", self.memory),
            ("review", self.review),
            ("rnd", self.rnd),
            ("agent", self.agent),
            ("integrator", self.integrator),
        ]

        for name, system in systems_to_init:
            try:
                if system is not None:
                    results["systems"][name] = "initialized"
                    self.status["systems"][name] = "active"
                else:
                    results["systems"][name] = "unavailable"
                    self.status["systems"][name] = "unavailable"
            except Exception as e:
                results["systems"][name] = f"error: {str(e)}"
                self.status["systems"][name] = "error"

        self.initialized = True
        self.status["initialized"] = True
        self._save_status()

        return results

    # ==================== UNIFIED EXECUTION ====================

    def execute(self, task: str, use_react: bool = True) -> Dict:
        """
        Execute a task using all integrated systems

        Flow:
        1. Add to memory (hierarchical)
        2. Search RAG for relevant knowledge
        3. Execute with ReAct agent (if enabled)
        4. Learn from result
        5. Self-reflect
        """
        result = {
            "task": task,
            "timestamp": datetime.now().isoformat(),
            "steps": []
        }

        # Step 1: Add to memory
        if self.memory:
            self.memory.add_memory(task, memory_type="task", importance=0.7)
            result["steps"].append({"step": "memory_add", "status": "done"})

        # Step 2: Search for relevant knowledge
        if self.rag:
            knowledge = self.rag.hybrid_search(task, top_k=5)
            result["knowledge_retrieved"] = len(knowledge)
            result["steps"].append({"step": "rag_search", "results": len(knowledge)})

        # Step 3: Execute with ReAct agent
        if use_react and self.agent:
            agent_result = self.agent.execute_task(task)
            result["agent_result"] = agent_result
            result["steps"].append({
                "step": "react_execution",
                "cycles": agent_result.get("cycles", 0),
                "success": agent_result.get("success", False)
            })

        # Step 4: Learn from result
        if self.digestion:
            content = str(result.get("agent_result", {}).get("final_result", task))
            self.digestion.ingest(content, source="task_execution")
            result["steps"].append({"step": "knowledge_ingestion", "status": "done"})

        # Step 5: Update stats
        self.status["total_tasks"] += 1
        self.status["last_activity"] = datetime.now().isoformat()
        self._save_status()

        result["completed_at"] = datetime.now().isoformat()
        return result

    def learn(self, content: str, source: str = "user", importance: float = 0.7) -> Dict:
        """
        Learn new knowledge using all systems

        Flow:
        1. Add to memory
        2. Index in RAG
        3. Process through digestion
        """
        result = {
            "content": content[:200],
            "source": source,
            "timestamp": datetime.now().isoformat(),
            "systems_updated": []
        }

        # Add to memory
        if self.memory:
            self.memory.add_memory(content, memory_type="knowledge", importance=importance)
            result["systems_updated"].append("memory")

        # Index in RAG
        if self.rag:
            doc_id = f"doc_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            self.rag.index_document(doc_id, content, {"source": source})
            result["systems_updated"].append("rag")

        # Process through digestion
        if self.digestion:
            self.digestion.ingest(content, source=source)
            result["systems_updated"].append("digestion")

        # Update brain if available
        if self.brain:
            try:
                self.brain.learn_knowledge(
                    source=source,
                    type="general",
                    title=content[:50],
                    content=content
                )
                result["systems_updated"].append("brain")
            except:
                pass

        self.status["total_knowledge"] += 1
        self._save_status()

        return result

    def query(self, query: str) -> Dict:
        """
        Query knowledge from all systems

        Flow:
        1. Search RAG
        2. Retrieve from memory
        3. Search digestion
        4. Synthesize results
        """
        result = {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "sources": {}
        }

        # RAG search
        if self.rag:
            rag_results = self.rag.hybrid_search(query, top_k=10)
            result["sources"]["rag"] = [
                {"content": r["content"][:200], "score": r["score"]}
                for r in rag_results
            ]

        # Memory search
        if self.memory:
            memory_results = self.memory.retrieve(query, top_k=10)
            result["sources"]["memory"] = [
                {"content": r["content"][:200], "tier": r.get("tier", "unknown")}
                for r in memory_results
            ]

        # Digestion search
        if self.digestion:
            digestion_results = self.digestion.retrieve(query, limit=10)
            result["sources"]["digestion"] = [
                {"summary": r.summary[:200]}
                for r in digestion_results
            ]

        return result

    # ==================== STATUS ====================

    def get_status(self) -> Dict:
        """Get comprehensive status"""
        status = {
            **self.status,
            "details": {}
        }

        # Get details from each system
        if self.scout:
            try:
                status["details"]["scout"] = {
                    "sources": len(self.scout.sources)
                }
            except:
                pass

        if self.memory:
            try:
                status["details"]["memory"] = self.memory.get_stats()
            except:
                pass

        if self.agent:
            try:
                status["details"]["agent"] = self.agent.get_stats()
            except:
                pass

        if self.rnd:
            try:
                status["details"]["rnd"] = self.rnd.get_stats()
            except:
                pass

        return status

    def run_self_improvement(self) -> Dict:
        """
        Run self-improvement cycle across all systems
        """
        result = {
            "timestamp": datetime.now().isoformat(),
            "improvements": []
        }

        # Run R&D research
        if self.rnd:
            research = self.rnd.run_research_cycle()
            result["improvements"].append({
                "system": "rnd",
                "new_trends": research.get("new_trends", 0),
                "new_innovations": research.get("new_innovations", 0)
            })

        # Run discovery
        if self.discovery:
            discovery = self.discovery.run_discovery_cycle()
            result["improvements"].append({
                "system": "discovery",
                "new_sources": discovery.get("new_sources", 0),
                "new_categories": discovery.get("new_categories", 0)
            })

        # Run self-review
        if self.review:
            review = self.review.run_full_review()
            result["improvements"].append({
                "system": "review",
                "health_score": review.get("overall_health", 0)
            })

        # Compress memory
        if self.digestion:
            compressed = self.digestion.compress_old_chunks(days_old=7)
            pruned = self.digestion.prune_low_value()
            result["improvements"].append({
                "system": "digestion",
                "compressed": compressed,
                "pruned": pruned
            })

        self._save_status()
        return result


# ==================== CONVENIENCE ====================

_hub: Optional[NexusIntegrationHub] = None


def get_hub() -> NexusIntegrationHub:
    """Get singleton hub"""
    global _hub
    if _hub is None:
        _hub = NexusIntegrationHub()
    return _hub


def initialize_nexus() -> Dict:
    """Initialize all NEXUS systems"""
    return get_hub().initialize()


def execute_task(task: str) -> Dict:
    """Execute task with all systems"""
    return get_hub().execute(task)


def learn(content: str, source: str = "user") -> Dict:
    """Learn new knowledge"""
    return get_hub().learn(content, source)


def query_knowledge(query: str) -> Dict:
    """Query knowledge"""
    return get_hub().query(query)


def nexus_status() -> Dict:
    """Get NEXUS status"""
    return get_hub().get_status()


def self_improve() -> Dict:
    """Run self-improvement"""
    return get_hub().run_self_improvement()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("NEXUS INTEGRATION HUB")
    print("=" * 70)

    hub = get_hub()

    print("\nInitializing all systems...")
    init_result = hub.initialize()

    print("\nSystem Status:")
    for system, status in init_result["systems"].items():
        icon = "✅" if status == "initialized" else "❌"
        print(f"  {icon} {system}: {status}")

    print("\nTesting unified execution...")
    result = hub.execute("Research latest AI agent techniques")

    print(f"\nExecution Result:")
    print(f"  Steps completed: {len(result['steps'])}")
    for step in result["steps"]:
        print(f"    - {step}")

    print("\nNEXUS Status:", hub.get_status())
