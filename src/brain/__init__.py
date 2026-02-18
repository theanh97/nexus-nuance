"""
NEXUS BRAIN - Central Intelligence Hub
Self-Evolving, Auto-Expanding, Never-Ending Learning
The World's Most Advanced Self-Learning System
"""

from .nexus_brain import (
    NexusBrain,
    BrainConfig,
    KnowledgeItem,
    MemoryHub,
    SkillTracker,
    get_brain,
    start_brain,
    learn,
    feedback,
    task_executed,
    brain_stats
)
from .omniscient_scout import OmniscientScout, get_scout, scan_all_sources, get_source_stats
from .auto_evolution import AutoDiscoveryEngine, get_discovery, run_discovery, get_discovery_stats
from .knowledge_digestion import KnowledgeDigestionEngine, get_digestion, ingest_knowledge, retrieve_knowledge, synthesize_knowledge
from .meta_evolution import SelfReviewEngine, get_review, run_review, get_review_stats
from .rd_lab import RDDepartment, get_rnd, run_research, get_rnd_stats
try:
    from .rd_lab import get_roadmap
except ImportError:
    get_roadmap = None
from .cutting_edge_tech import (
    CuttingEdgeIntegrator, AdvancedRAGSystem, HierarchicalMemorySystem,
    get_integrator, get_advanced_rag, get_hierarchical_memory
)
from .react_agent import ReActAgent, MultiAgentOrchestrator, get_agent, get_orchestrator, execute_with_react
from .integration_hub import (
    NexusIntegrationHub, get_hub, initialize_nexus, execute_task,
    learn, query_knowledge, nexus_status, self_improve
)

__all__ = [
    # Core
    "NexusBrain",
    "BrainConfig",
    "KnowledgeItem",
    "MemoryHub",
    "SkillTracker",
    # Scout
    "OmniscientScout",
    # Evolution
    "AutoDiscoveryEngine",
    # Digestion & RAG
    "KnowledgeDigestionEngine",
    # Meta-Learning
    "SelfReviewEngine",
    # R&D
    "RDDepartment",
    # Functions
    "get_brain",
    "start_brain",
    "learn",
    "feedback",
    "task_executed",
    "brain_stats",
    "get_scout",
    "scan_all_sources",
    "get_source_stats",
    "get_discovery",
    "run_discovery",
    "get_discovery_stats",
    "get_digestion",
    "ingest_knowledge",
    "retrieve_knowledge",
    "synthesize_knowledge",
    "get_review",
    "run_review",
    "get_review_stats",
    "get_rnd",
    "run_research",
    "get_rnd_stats",
    "get_roadmap"
]
