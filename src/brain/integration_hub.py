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

from core.nexus_logger import get_logger

logger = get_logger(__name__)

try:
    from core.llm_caller import call_llm, quick_generate, code_generate, get_available_models
    _llm_caller_available = True
except ImportError:
    _llm_caller_available = False

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
        self._llm_caller = None

        self._lazy_lock = threading.RLock()
        self._query_cache: Dict[str, Dict] = {}
        self._cache_ttl = int(os.getenv("NEXUS_CACHE_TTL", "300"))

        # Status
        self.status = {
            "initialized": False,
            "systems": {},
            "errors": {},
            "last_activity": None,
            "total_tasks": 0,
            "total_knowledge": 0
        }

        self._load_status()

    def _load_status(self):
        """Load status"""
        if self.status_file.exists():
            with open(self.status_file, 'r', encoding='utf-8') as f:
                self.status.update(json.load(f))

    def _save_status(self):
        """Save status"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                **self.status,
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    # ==================== LAZY LOADING ====================

    def _load_component(self, name: str, loader):
        """Load component with explicit degraded/error tracking."""
        try:
            component = loader()
            self.status["systems"][name] = "active" if component is not None else "unavailable"
            if component is None:
                self.status["errors"][name] = "loader_returned_none"
            else:
                self.status["errors"].pop(name, None)
            return component
        except (MemoryError, RecursionError):
            raise
        except Exception as e:
            self.status["systems"][name] = "error"
            self.status["errors"][name] = f"{type(e).__name__}: {e}"
            return None

    @property
    def brain(self):
        if self._brain is None:
            with self._lazy_lock:
                if self._brain is None:
                    def _loader():
                        from .nexus_brain import get_brain
                        return get_brain()
                    self._brain = self._load_component("brain", _loader)
        return self._brain

    @property
    def scout(self):
        if self._scout is None:
            with self._lazy_lock:
                if self._scout is None:
                    def _loader():
                        from .omniscient_scout import get_scout
                        return get_scout()
                    self._scout = self._load_component("scout", _loader)
        return self._scout

    @property
    def discovery(self):
        if self._discovery is None:
            with self._lazy_lock:
                if self._discovery is None:
                    def _loader():
                        from .auto_evolution import get_discovery
                        return get_discovery()
                    self._discovery = self._load_component("discovery", _loader)
        return self._discovery

    @property
    def digestion(self):
        if self._digestion is None:
            with self._lazy_lock:
                if self._digestion is None:
                    def _loader():
                        from .knowledge_digestion import get_digestion
                        return get_digestion()
                    self._digestion = self._load_component("digestion", _loader)
        return self._digestion

    @property
    def rag(self):
        if self._rag is None:
            with self._lazy_lock:
                if self._rag is None:
                    def _loader():
                        from .cutting_edge_tech import get_advanced_rag
                        return get_advanced_rag()
                    self._rag = self._load_component("rag", _loader)
        return self._rag

    @property
    def memory(self):
        if self._memory is None:
            with self._lazy_lock:
                if self._memory is None:
                    def _loader():
                        from .cutting_edge_tech import get_hierarchical_memory
                        return get_hierarchical_memory()
                    self._memory = self._load_component("memory", _loader)
        return self._memory

    @property
    def review(self):
        if self._review is None:
            with self._lazy_lock:
                if self._review is None:
                    def _loader():
                        from .meta_evolution import get_review
                        return get_review()
                    self._review = self._load_component("review", _loader)
        return self._review

    @property
    def rnd(self):
        if self._rnd is None:
            with self._lazy_lock:
                if self._rnd is None:
                    def _loader():
                        from .rd_lab import get_rnd
                        return get_rnd()
                    self._rnd = self._load_component("rnd", _loader)
        return self._rnd

    @property
    def agent(self):
        if self._agent is None:
            with self._lazy_lock:
                if self._agent is None:
                    def _loader():
                        from .react_agent import get_agent
                        return get_agent()
                    self._agent = self._load_component("agent", _loader)
        return self._agent

    @property
    def integrator(self):
        if self._integrator is None:
            with self._lazy_lock:
                if self._integrator is None:
                    def _loader():
                        from .cutting_edge_tech import get_integrator
                        return get_integrator()
                    self._integrator = self._load_component("integrator", _loader)
        return self._integrator

    @property
    def llm_caller(self):
        if self._llm_caller is None:
            with self._lazy_lock:
                if self._llm_caller is None:
                    def _loader():
                        if not _llm_caller_available:
                            return None
                        return {
                            "call_llm": call_llm,
                            "quick_generate": quick_generate,
                            "code_generate": code_generate,
                            "get_available_models": get_available_models,
                        }

                    self._llm_caller = self._load_component("llm_caller", _loader)
        return self._llm_caller

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
            ("llm_caller", self.llm_caller),
        ]

        for name, system in systems_to_init:
            try:
                if system is not None:
                    results["systems"][name] = "initialized"
                    self.status["systems"][name] = "active"
                else:
                    results["systems"][name] = "unavailable"
                    self.status["systems"][name] = "unavailable"
            except (MemoryError, RecursionError):
                raise
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

        # Step 0: Skill-based recommendation
        if self.brain and hasattr(self.brain, "skill_tracker"):
            try:
                best_skill = self.brain.skill_tracker.get_best_skill_for_task(task)
                if best_skill:
                    rec = self.brain.skill_tracker.get_skill_recommendation(best_skill)
                    result["skill_recommendation"] = rec
                    result["steps"].append({"step": "skill_check", "skill": best_skill, "approach": rec.get("suggested_approach", "standard")})
            except (MemoryError, RecursionError):
                raise
            except Exception:
                pass

        # Step 1: Add to memory
        if self.memory:
            try:
                self.memory.add_memory(task, memory_type="task", importance=0.7)
                result["steps"].append({"step": "memory_add", "status": "done"})
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["steps"].append({"step": "memory_add", "status": "error", "error": str(e)})

        # Step 2: Search for relevant knowledge
        if self.rag:
            try:
                knowledge = self.rag.hybrid_search(task, top_k=5)
                if knowledge is None:
                    result["steps"].append({
                        "step": "rag_search",
                        "status": "error",
                        "error": "hybrid_search_returned_none"
                    })
                else:
                    knowledge_count = len(knowledge)
                    result["knowledge_retrieved"] = knowledge_count
                    result["steps"].append({"step": "rag_search", "results": knowledge_count})
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["steps"].append({"step": "rag_search", "status": "error", "error": str(e)})

        # Step 2.5: Use llm_caller to enhance task understanding
        if self.llm_caller:
            try:
                llm_analysis = self.llm_caller["quick_generate"](
                    f"Analyze this task and suggest approach: {task[:500]}"
                )
                result["llm_analysis"] = llm_analysis
                result["steps"].append({"step": "llm_analysis", "status": "done"})
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["steps"].append({"step": "llm_analysis", "status": "error", "error": str(e)})

        # Step 3: Execute with ReAct agent
        if use_react and self.agent:
            try:
                agent_result = self.agent.execute_task(task)
                result["agent_result"] = agent_result
                if agent_result is None:
                    result["steps"].append({
                        "step": "react_execution",
                        "status": "error",
                        "error": "agent_execute_returned_none"
                    })
                elif isinstance(agent_result, dict):
                    result["steps"].append({
                        "step": "react_execution",
                        "cycles": agent_result.get("cycles", 0),
                        "success": agent_result.get("success", False)
                    })
                else:
                    result["steps"].append({
                        "step": "react_execution",
                        "status": "error",
                        "error": f"agent_result_not_dict:{type(agent_result).__name__}"
                    })
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["steps"].append({"step": "react_execution", "status": "error", "error": str(e)})

        # Step 4: Learn from result
        if self.digestion:
            try:
                agent_result = result.get("agent_result")
                if isinstance(agent_result, dict):
                    final_result = agent_result.get("final_result", task)
                else:
                    final_result = task
                content = str(final_result)
                self.digestion.ingest(content, source="task_execution")
                result["steps"].append({"step": "knowledge_ingestion", "status": "done"})
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["steps"].append({"step": "knowledge_ingestion", "status": "error", "error": str(e)})

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
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["systems_updated"].append(f"brain_error:{type(e).__name__}")

        self.status["total_knowledge"] += 1
        self._save_status()

        return result

    def query(self, query: str) -> Dict:
        """
        Query knowledge from all systems

        Flow:
        1. Check cache
        2. Search RAG
        3. Retrieve from memory
        4. Search digestion
        5. Synthesize results
        """
        # Check cache
        import hashlib as _hashlib
        cache_key = _hashlib.md5(query.encode()).hexdigest()
        cached = self._query_cache.get(cache_key)
        if cached:
            age = (datetime.now() - datetime.fromisoformat(cached["timestamp"])).total_seconds()
            if age < self._cache_ttl:
                cached["result"]["cache_hit"] = True
                return cached["result"]

        result = {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "sources": {}
        }

        # RAG search
        if self.rag:
            rag_results = self.rag.hybrid_search(query, top_k=10)
            if rag_results is None:
                result["sources"]["rag"] = []
            else:
                result["sources"]["rag"] = [
                    {"content": r["content"][:200], "score": r["score"]}
                    for r in rag_results
                ]

        # Memory search
        if self.memory:
            memory_results = self.memory.retrieve(query, top_k=10)
            if memory_results is None:
                result["sources"]["memory"] = []
            else:
                result["sources"]["memory"] = [
                    {"content": r["content"][:200], "tier": r.get("tier", "unknown")}
                    for r in memory_results
                ]

        # Digestion search
        if self.digestion:
            digestion_results = self.digestion.retrieve(query, limit=10)
            if digestion_results is None:
                result["sources"]["digestion"] = []
            else:
                result["sources"]["digestion"] = [
                    {"summary": r.summary[:200]}
                    for r in digestion_results
                ]

        # Store in cache (keep max 100 entries)
        if len(self._query_cache) > 100:
            oldest = min(self._query_cache, key=lambda k: self._query_cache[k]["timestamp"])
            del self._query_cache[oldest]
        self._query_cache[cache_key] = {"result": result, "timestamp": datetime.now().isoformat()}

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
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                status["details"]["scout_error"] = str(e)

        if self.memory:
            try:
                status["details"]["memory"] = self.memory.get_stats()
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                status["details"]["memory_error"] = str(e)

        if self.agent:
            try:
                status["details"]["agent"] = self.agent.get_stats()
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                status["details"]["agent_error"] = str(e)

        if self.rnd:
            try:
                status["details"]["rnd"] = self.rnd.get_stats()
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                status["details"]["rnd_error"] = str(e)

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
            try:
                research = self.rnd.run_research_cycle()
                if research is None:
                    result["improvements"].append({
                        "system": "rnd",
                        "error": "run_research_cycle_returned_none"
                    })
                elif isinstance(research, dict):
                    result["improvements"].append({
                        "system": "rnd",
                        "new_trends": research.get("new_trends", 0),
                        "new_innovations": research.get("new_innovations", 0)
                    })
                else:
                    result["improvements"].append({
                        "system": "rnd",
                        "error": f"run_research_cycle_returned_{type(research).__name__}"
                    })
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["improvements"].append({"system": "rnd", "error": str(e)})

        # Run discovery
        if self.discovery:
            try:
                discovery = self.discovery.run_discovery_cycle()
                if discovery is None:
                    result["improvements"].append({
                        "system": "discovery",
                        "error": "run_discovery_cycle_returned_none"
                    })
                elif isinstance(discovery, dict):
                    result["improvements"].append({
                        "system": "discovery",
                        "new_sources": discovery.get("new_sources", 0),
                        "new_categories": discovery.get("new_categories", 0)
                    })
                else:
                    result["improvements"].append({
                        "system": "discovery",
                        "error": f"run_discovery_cycle_returned_{type(discovery).__name__}"
                    })
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["improvements"].append({"system": "discovery", "error": str(e)})

        # Run self-review
        if self.review:
            try:
                review = self.review.run_full_review()
                if review is None:
                    result["improvements"].append({
                        "system": "review",
                        "error": "run_full_review_returned_none"
                    })
                elif isinstance(review, dict):
                    result["improvements"].append({
                        "system": "review",
                        "health_score": review.get("overall_health", 0)
                    })
                else:
                    result["improvements"].append({
                        "system": "review",
                        "error": f"run_full_review_returned_{type(review).__name__}"
                    })
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["improvements"].append({"system": "review", "error": str(e)})

        # Compress memory
        if self.digestion:
            try:
                compressed = self.digestion.compress_old_chunks(days_old=7)
                pruned = self.digestion.prune_low_value()
                result["improvements"].append({
                    "system": "digestion",
                    "compressed": compressed,
                    "pruned": pruned
                })
            except (MemoryError, RecursionError):
                raise
            except Exception as e:
                result["improvements"].append({"system": "digestion", "error": str(e)})

        self._save_status()
        return result

    def run_maintenance(self) -> Dict:
        """
        Run periodic maintenance: prune caches, clean old data, verify integrity.
        """
        result = {"timestamp": datetime.now().isoformat(), "actions": []}

        # 1. Prune query cache (remove expired entries)
        now = datetime.now()
        expired_keys = []
        for key, entry in self._query_cache.items():
            try:
                age = (now - datetime.fromisoformat(entry["timestamp"])).total_seconds()
                if age > self._cache_ttl:
                    expired_keys.append(key)
            except (ValueError, KeyError):
                expired_keys.append(key)
        for key in expired_keys:
            del self._query_cache[key]
        result["actions"].append({"type": "cache_prune", "removed": len(expired_keys), "remaining": len(self._query_cache)})

        # 2. Trim action history (keep last 500)
        try:
            from .action_executor import get_executor
            executor = get_executor()
            before = len(executor.history)
            if before > 500:
                executor.history = executor.history[-500:]
            result["actions"].append({"type": "history_trim", "before": before, "after": len(executor.history)})
        except (MemoryError, RecursionError):
            raise
        except Exception as e:
            result["actions"].append({"type": "history_trim", "error": str(e)})

        # 3. Clean old data files (> 30 days)
        try:
            data_files_cleaned = 0
            cutoff = time.time() - (30 * 86400)
            for pattern in ["data/brain/report_*.json"]:
                import glob as _glob
                for f in _glob.glob(str(Path(self.data_dir).parent.parent / pattern)):
                    try:
                        if os.path.getmtime(f) < cutoff:
                            os.remove(f)
                            data_files_cleaned += 1
                    except OSError:
                        pass
            result["actions"].append({"type": "old_files_cleanup", "removed": data_files_cleaned})
        except (MemoryError, RecursionError):
            raise
        except Exception as e:
            result["actions"].append({"type": "old_files_cleanup", "error": str(e)})

        # 4. Save clean status
        self._save_status()
        result["actions"].append({"type": "status_save", "ok": True})

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
