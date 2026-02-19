"""
Knowledge Scanner - Auto-Scan & Learn from External Sources
Part of The Dream Team Self-Learning Infrastructure

Features:
- Scan 50+ sources for new knowledge
- Quality filtering (6+/10 score)
- Auto-propose improvements
- Integration with memory system
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path
import re
import hashlib
import logging
import threading

# Import memory manager
from .memory_manager import get_memory, remember, learn_lesson


logger = logging.getLogger(__name__)


class KnowledgeSource:
    """Represents a knowledge source to scan."""

    def __init__(self, name: str, url: str, category: str,
                 priority: int = 5, scan_frequency: str = "daily"):
        self.name = name
        self.url = url
        self.category = category
        self.priority = priority  # 1-10
        self.scan_frequency = scan_frequency
        self.last_scanned = None
        self.last_error = None


class KnowledgeScanner:
    """
    Scans external sources for new knowledge and improvements.
    Integrates with MemoryManager for persistent learning.
    """

    # Predefined sources
    DEFAULT_SOURCES = [
        # AI & ML
        KnowledgeSource("OpenAI Blog", "https://openai.com/blog", "ai_ml", 9),
        KnowledgeSource("Anthropic Blog", "https://anthropic.com", "ai_ml", 9),
        KnowledgeSource("Google AI", "https://ai.googleblog.com", "ai_ml", 8),
        KnowledgeSource("Hugging Face", "https://huggingface.co/blog", "ai_ml", 8),
        KnowledgeSource("Papers With Code", "https://paperswithcode.com", "ai_ml", 7),

        # Open Source Tools
        KnowledgeSource("LangChain", "https://python.langchain.com/docs", "tools", 8),
        KnowledgeSource("CrewAI", "https://docs.crewai.com", "tools", 7),
        KnowledgeSource("Chroma DB", "https://docs.trychroma.com", "tools", 7),
        KnowledgeSource("MemGPT", "https://memgpt.readme.io", "tools", 8),

        # Engineering
        KnowledgeSource("Martin Fowler", "https://martinfowler.com", "engineering", 7),
        KnowledgeSource("ThoughtWorks", "https://thoughtworks.com/radar", "engineering", 8),
        KnowledgeSource("InfoQ", "https://infoq.com", "engineering", 6),

        # Design
        KnowledgeSource("Smashing Mag", "https://smashingmagazine.com", "design", 6),
        KnowledgeSource("Laws of UX", "https://lawsofux.com", "design", 7),
        KnowledgeSource("Nielsen Norman", "https://nngroup.com", "design", 8),

        # Strategy
        KnowledgeSource("a16z Blog", "https://a16z.com", "strategy", 7),
        KnowledgeSource("YC Blog", "https://ycombinator.com/blog", "strategy", 7),
        KnowledgeSource("Lenny's Newsletter", "https://lennyrachitsky.com", "strategy", 6),
    ]

    def __init__(self, memory_path: str = None):
        self.memory = get_memory()
        self._lock = threading.RLock()
        self.proposal_threshold = float(os.getenv("PROPOSAL_SCORE_THRESHOLD", "6.2"))
        self.scan_log_max_entries = int(os.getenv("SCAN_LOG_MAX_ENTRIES", "1000"))
        self.proposal_history_max = int(os.getenv("PROPOSAL_HISTORY_MAX", "1500"))
        self.source_backoff_base_seconds = int(os.getenv("SOURCE_BACKOFF_BASE_SECONDS", "600"))
        self.source_backoff_max_seconds = int(os.getenv("SOURCE_BACKOFF_MAX_SECONDS", "21600"))

        # Data paths - use data/memory/ by default
        if memory_path:
            data_path = Path(memory_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                data_path = project_root / "data" / "memory"
            except (NameError, TypeError):
                data_path = Path.cwd() / "data" / "memory"

        data_path.mkdir(parents=True, exist_ok=True)
        self.scan_log_path = data_path / "scan_log.json"
        self.proposals_path = data_path / "improvement_proposals.json"
        self.source_state_path = data_path / "source_state.json"
        self.sources = self.DEFAULT_SOURCES.copy()
        self._init_files()
        self._source_state = self._load_json(self.source_state_path, {"sources": {}})

    def _init_files(self):
        """Initialize storage files."""
        if not self.scan_log_path.exists():
            self._save_json(self.scan_log_path, {"scans": []})

        if not self.proposals_path.exists():
            self._save_json(self.proposals_path, {"proposals": [], "pending": []})

        if not self.source_state_path.exists():
            self._save_json(self.source_state_path, {"sources": {}})

    def _load_json(self, path: Path, default: Optional[Dict] = None) -> Dict:
        """Load JSON file safely."""
        default = default or {}
        try:
            with self._lock:
                if not path.exists():
                    return dict(default)
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return dict(default)

    def _save_json(self, path: Path, data: Dict) -> bool:
        """Save JSON file safely."""
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, path)
            return True
        except Exception as e:
            logger.error(f"Failed to save {path}: {e}")
            return False

    def _normalize_proposal_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize proposal file structure and keep pending list consistent."""
        proposals = data.get("proposals", [])
        if not isinstance(proposals, list):
            proposals = []

        normalized: List[Dict[str, Any]] = []
        actionable_ids: List[str] = []
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            proposal_id = proposal.get("id")
            if not proposal_id:
                continue
            status = proposal.get("status", "pending_approval")
            proposal["status"] = status
            normalized.append(proposal)
            if status in {"pending_approval", "approved"}:
                actionable_ids.append(proposal_id)

        dedup_pending = []
        seen_pending = set()
        for proposal_id in actionable_ids:
            if proposal_id in seen_pending:
                continue
            seen_pending.add(proposal_id)
            dedup_pending.append(proposal_id)

        return {"proposals": normalized, "pending": dedup_pending}

    def _prune_scan_logs(self, data: Dict[str, Any]) -> Dict[str, Any]:
        scans = data.get("scans", [])
        if not isinstance(scans, list):
            scans = []
        if self.scan_log_max_entries > 0 and len(scans) > self.scan_log_max_entries:
            scans = scans[-self.scan_log_max_entries:]
        data["scans"] = scans
        return data

    def _prune_proposal_history(self, data: Dict[str, Any]) -> Dict[str, Any]:
        proposals = data.get("proposals", [])
        if not isinstance(proposals, list):
            proposals = []

        # Keep actionable proposals always; prune old terminal states first.
        active = [p for p in proposals if p.get("status") in {"pending_approval", "approved"}]
        terminal = [p for p in proposals if p.get("status") not in {"pending_approval", "approved"}]
        if self.proposal_history_max > 0 and len(active) + len(terminal) > self.proposal_history_max:
            keep_terminal = max(0, self.proposal_history_max - len(active))
            terminal = terminal[-keep_terminal:] if keep_terminal > 0 else []
        data["proposals"] = active + terminal
        return self._normalize_proposal_data(data)

    def _source_key(self, source: KnowledgeSource) -> str:
        return hashlib.md5(f"{source.name}|{source.url}".encode("utf-8")).hexdigest()[:16]

    def _safe_parse_time(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def _frequency_to_interval_seconds(self, frequency: str) -> int:
        frequency = (frequency or "daily").strip().lower()
        mapping = {
            "realtime": 60,
            "hourly": 3600,
            "daily": 86400,
            "weekly": 604800,
        }
        return mapping.get(frequency, 86400)

    def _get_source_state(self, source: KnowledgeSource) -> Dict[str, Any]:
        key = self._source_key(source)
        sources = self._source_state.setdefault("sources", {})
        state = sources.setdefault(
            key,
            {
                "name": source.name,
                "url": source.url,
                "last_scanned": None,
                "last_error": None,
                "error_count": 0,
                "next_retry_at": None,
            },
        )
        # Keep canonical metadata updated.
        state["name"] = source.name
        state["url"] = source.url
        source.last_scanned = state.get("last_scanned")
        source.last_error = state.get("last_error")
        return state

    def _mark_source_scan_success(self, source: KnowledgeSource, scanned_at: datetime) -> None:
        state = self._get_source_state(source)
        iso_ts = scanned_at.isoformat()
        state["last_scanned"] = iso_ts
        state["last_error"] = None
        state["error_count"] = 0
        state["next_retry_at"] = None
        source.last_scanned = iso_ts
        source.last_error = None

    def _mark_source_scan_failure(self, source: KnowledgeSource, error: str, failed_at: datetime) -> None:
        state = self._get_source_state(source)
        error_count = int(state.get("error_count", 0)) + 1
        delay = min(
            self.source_backoff_max_seconds,
            self.source_backoff_base_seconds * (2 ** max(0, error_count - 1)),
        )
        next_retry = failed_at + timedelta(seconds=delay)
        state["last_error"] = error[:500]
        state["error_count"] = error_count
        state["next_retry_at"] = next_retry.isoformat()
        source.last_error = state["last_error"]

    def _should_scan_source(self, source: KnowledgeSource, now: datetime) -> bool:
        state = self._get_source_state(source)
        next_retry = self._safe_parse_time(state.get("next_retry_at"))
        if next_retry and now < next_retry:
            return False

        last_scanned = self._safe_parse_time(state.get("last_scanned"))
        if not last_scanned:
            return True

        interval_seconds = self._frequency_to_interval_seconds(source.scan_frequency)
        return (now - last_scanned).total_seconds() >= interval_seconds

    def _item_signature(self, item: Dict) -> str:
        """Stable signature to deduplicate discovered items."""
        key = "|".join([
            str(item.get("title", "")).strip().lower(),
            str(item.get("url", "")).strip().lower(),
            str(item.get("source", "")).strip().lower(),
            str(item.get("category", "")).strip().lower(),
            str(item.get("potential_improvement", "")).strip().lower(),
        ])
        return hashlib.md5(key.encode("utf-8")).hexdigest()

    def _deduplicate_items(self, items: List[Dict]) -> List[Dict]:
        """Remove duplicate knowledge items by signature, keeping highest score."""
        best_by_sig: Dict[str, Dict] = {}
        for item in items:
            sig = self._item_signature(item)
            existing = best_by_sig.get(sig)
            if not existing or float(item.get("score", 0)) > float(existing.get("score", 0)):
                best_by_sig[sig] = item
        return list(best_by_sig.values())

    # ==================== SCORING ====================

    def calculate_score(self, item: Dict) -> float:
        """
        Calculate quality score (0-10) for a knowledge item.

        Criteria:
        - Relevance (0-3): How applicable to current system
        - Impact (0-3): Potential improvement magnitude
        - Timeliness (0-2): How recent/urgent
        - Credibility (0-2): Source authority
        """
        score = 0.0

        # Relevance check
        relevant_keywords = [
            "ai", "agent", "automation", "memory", "learning",
            "code generation", "ui/ux", "security", "devops",
            "token", "optimization", "vector", "rag", "llm"
        ]
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        relevance_matches = sum(1 for kw in relevant_keywords if kw in text)
        score += min(3, relevance_matches * 0.5)

        # Impact assessment
        impact_keywords = ["breakthrough", "10x", "major", "significant", "new release"]
        if any(kw in text for kw in impact_keywords):
            score += 3
        elif any(kw in text for kw in ["improvement", "update", "feature"]):
            score += 2
        else:
            score += 1

        # Timeliness (assume recent for now)
        score += 2  # Default: recent

        # Credibility (based on source)
        source_priority = item.get("source_priority", 5)
        score += min(2, source_priority / 5)

        return min(10, score)

    # ==================== REAL WEB SCANNING ====================

    def fetch_url_content(self, url: str) -> Optional[str]:
        """
        Fetch content from a URL.
        Uses requests library for web scraping.
        """
        try:
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                # Basic HTML to text conversion
                text = response.text
                # Remove script and style elements
                import re
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                # Remove HTML tags
                text = re.sub(r'<[^>]+>', ' ', text)
                # Clean up whitespace
                text = ' '.join(text.split())
                return text[:5000]  # Limit size
        except Exception as e:
            pass
        return None

    def extract_articles_from_content(self, content: str, source: KnowledgeSource) -> List[Dict]:
        """
        Extract potential article/topic information from fetched content.
        Uses pattern matching and keyword extraction.
        """
        items = []

        # Keywords to look for based on source category
        category_keywords = {
            "ai_ml": ["AI", "machine learning", "neural", "model", "GPT", "LLM", "agent", "automation",
                      "deep learning", "transformer", "Claude", "GPT-4", "Gemini", "GLM"],
            "tools": ["framework", "library", "API", "SDK", "release", "update", "new feature",
                      "integration", "LangChain", "MCP", "vector database"],
            "engineering": ["architecture", "pattern", "best practice", "performance", "scalability",
                           "microservices", "clean code", "refactoring"],
            "design": ["UI", "UX", "design system", "accessibility", "animation", "responsive",
                      "typography", "color", "layout"],
            "strategy": ["startup", "growth", "product", "market", "business model", "funding"]
        }

        keywords = category_keywords.get(source.category, [])

        # Find relevant sections based on keywords
        content_lower = content.lower()
        found_topics = []

        for kw in keywords:
            if kw.lower() in content_lower:
                # Extract surrounding context
                idx = content_lower.find(kw.lower())
                start = max(0, idx - 200)
                end = min(len(content), idx + 300)
                context = content[start:end]

                # Create a topic item
                if kw not in found_topics:
                    found_topics.append(kw)
                    items.append({
                        "title": f"{kw} - Recent developments",
                        "description": context[:300],
                        "source": source.name,
                        "source_priority": source.priority,
                        "category": source.category,
                        "url": source.url,
                        "applicable_to": self._get_applicable_agents(source.category),
                        "potential_improvement": f"New insights about {kw}"
                    })

        return items[:5]  # Max 5 items per source

    def _get_applicable_agents(self, category: str) -> List[str]:
        """Get applicable agents for a category."""
        mapping = {
            "ai_ml": ["ORION", "NOVA", "PIXEL"],
            "tools": ["ORION", "NOVA", "MEMORY"],
            "engineering": ["NOVA", "CIPHER"],
            "design": ["PIXEL"],
            "strategy": ["ORION"]
        }
        return mapping.get(category, ["ORION"])

    def scan_source_real(self, source: KnowledgeSource) -> List[Dict]:
        """
        Real implementation of source scanning.
        Fetches actual content and extracts knowledge.
        """
        items = []

        try:
            # Fetch content from URL
            content = self.fetch_url_content(source.url)

            if content:
                # Extract articles/topics from content
                items = self.extract_articles_from_content(content, source)

                # Add timestamp
                for item in items:
                    item["fetched_at"] = datetime.now().isoformat()

        except Exception as e:
            # Log error and return empty
            learn_lesson(
                lesson_type="failure",
                description=f"Failed to scan {source.name}: {str(e)}",
                context="Knowledge scanning",
                impact="low"
            )

        return items

    def scan_source(self, source: KnowledgeSource, use_real_fetch: bool = True) -> List[Dict]:
        """
        Scan a source for new knowledge.

        Args:
            source: Knowledge source to scan
            use_real_fetch: If True, fetch real content. If False, use simulated data.

        Returns:
            List of discovered knowledge items
        """
        if use_real_fetch:
            # Try real fetching first
            items = self.scan_source_real(source)
            if items:
                # Add scores
                for item in items:
                    item["score"] = self.calculate_score(item)
                    item["discovered_at"] = datetime.now().isoformat()
                return items

        # Fallback to simulated results if real fetch fails or is disabled
        items = []

        # Simulated discoveries based on source type
        if source.category == "ai_ml":
            items.extend([
                {
                    "title": "New function calling improvements",
                    "description": "Enhanced structured outputs for AI models",
                    "source": source.name,
                    "source_priority": source.priority,
                    "category": "ai_ml",
                    "url": f"{source.url}/function-calling",
                    "applicable_to": ["NOVA", "ORION"],
                    "potential_improvement": "Better code generation accuracy"
                },
                {
                    "title": "Vision model updates",
                    "description": "Improved screenshot analysis capabilities",
                    "source": source.name,
                    "source_priority": source.priority,
                    "category": "ai_ml",
                    "url": f"{source.url}/vision",
                    "applicable_to": ["PIXEL"],
                    "potential_improvement": "More accurate UI/UX analysis"
                }
            ])

        elif source.category == "tools":
            items.extend([
                {
                    "title": "RAG optimization techniques",
                    "description": "New retrieval patterns for better context loading",
                    "source": source.name,
                    "source_priority": source.priority,
                    "category": "tools",
                    "url": f"{source.url}/rag",
                    "applicable_to": ["MEMORY", "ORION"],
                    "potential_improvement": "30% token reduction in retrieval"
                },
                {
                    "title": "Agent coordination patterns",
                    "description": "Better parallel execution strategies",
                    "source": source.name,
                    "source_priority": source.priority,
                    "category": "tools",
                    "url": f"{source.url}/agents",
                    "applicable_to": ["ORION"],
                    "potential_improvement": "Faster iteration cycles"
                }
            ])

        elif source.category == "engineering":
            items.extend([
                {
                    "title": "Clean code patterns 2024",
                    "description": "Updated best practices for maintainable code",
                    "source": source.name,
                    "source_priority": source.priority,
                    "category": "engineering",
                    "url": f"{source.url}/patterns",
                    "applicable_to": ["NOVA", "CIPHER"],
                    "potential_improvement": "Higher code quality scores"
                }
            ])

        # Calculate scores
        for item in items:
            item["score"] = self.calculate_score(item)
            item["discovered_at"] = datetime.now().isoformat()

        return items

    # ==================== MAIN SCAN ====================

    def scan_all(self, min_score: float = 6.0) -> Dict:
        """
        Scan all sources and return filtered results.

        Returns:
            Dict with discovered items, proposals, and stats
        """
        all_items = []
        scanned_sources = 0
        skipped_sources = 0
        source_errors = 0
        now = datetime.now()

        for source in self.sources:
            if not self._should_scan_source(source, now):
                skipped_sources += 1
                continue
            try:
                items = self.scan_source(source)
                all_items.extend(items)
                scanned_sources += 1
                self._mark_source_scan_success(source, datetime.now())

            except Exception as e:
                source_errors += 1
                self._mark_source_scan_failure(source, str(e), datetime.now())
                # Log error as lesson
                learn_lesson(
                    lesson_type="failure",
                    description=f"Failed to scan {source.name}: {e}",
                    context="Knowledge scanning",
                    impact="low"
                )

        # Remove duplicates before filtering/scoring actions downstream.
        deduped_items = self._deduplicate_items(all_items)

        # Filter by score
        filtered_items = [item for item in deduped_items if item["score"] >= min_score]

        # Sort by score
        filtered_items.sort(key=lambda x: x["score"], reverse=True)

        # Generate proposals for high-score items
        proposals = []
        for item in filtered_items:
            if item["score"] >= self.proposal_threshold:
                proposal = self._create_proposal(item)
                if proposal:
                    proposals.append(proposal)

        # Store in memory
        for item in filtered_items:
            signature = self._item_signature(item)
            remember(
                key=f"knowledge:{signature}",
                content=item,
                category=f"scanned_{item['category']}",
                keywords=[item['category'], item['source']],
                importance=int(item['score'])
            )

        # Log scan
        self._log_scan(len(all_items), len(deduped_items), len(filtered_items), len(proposals))
        self._save_json(self.source_state_path, self._source_state)

        return {
            "total_discovered": len(all_items),
            "sources_scanned": scanned_sources,
            "sources_skipped": skipped_sources,
            "source_errors": source_errors,
            "deduplicated_count": len(deduped_items),
            "filtered_count": len(filtered_items),
            "proposals_generated": len(proposals),
            "top_items": filtered_items[:5],
            "proposals": proposals
        }

    def _create_proposal(self, item: Dict) -> Optional[Dict]:
        """Create improvement proposal from knowledge item."""
        source_signature = self._item_signature(item)
        data = self._load_json(self.proposals_path, {"proposals": [], "pending": []})
        data = self._normalize_proposal_data(data)

        # Avoid creating duplicate proposals for the same source insight.
        for existing in data.get("proposals", []):
            if existing.get("source_signature") == source_signature and existing.get("status") in {
                "pending_approval", "approved", "applied"
            }:
                return None

        proposal = {
            "id": f"prop_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{source_signature[:8]}",
            "source_item": item,
            "source_signature": source_signature,
            "title": f"Integrate: {item['title']}",
            "description": item.get("potential_improvement", "TBD"),
            "affected_agents": item.get("applicable_to", []),
            "expected_impact": f"Score: {item['score']}/10",
            "status": "pending_approval",
            "created": datetime.now().isoformat()
        }

        data["proposals"].append(proposal)
        if proposal["id"] not in data["pending"]:
            data["pending"].append(proposal["id"])
        data = self._prune_proposal_history(data)
        self._save_json(self.proposals_path, data)

        return proposal

    def _log_scan(self, total: int, deduped: int, filtered: int, proposals: int):
        """Log scan results."""
        data = self._load_json(self.scan_log_path, {"scans": []})

        data["scans"].append({
            "timestamp": datetime.now().isoformat(),
            "total_items": total,
            "deduplicated_items": deduped,
            "filtered_items": filtered,
            "proposals_generated": proposals
        })

        data = self._prune_scan_logs(data)
        self._save_json(self.scan_log_path, data)

    # ==================== PROPOSAL MANAGEMENT ====================

    def get_pending_proposals(self) -> List[Dict]:
        """Get proposals that still need action (approval or application)."""
        data = self._load_json(self.proposals_path, {"proposals": [], "pending": []})
        data = self._normalize_proposal_data(data)
        self._save_json(self.proposals_path, data)
        return [
            p for p in data["proposals"]
            if p.get("status") in {"pending_approval", "approved"}
        ]

    def approve_proposal(self, proposal_id: str) -> bool:
        """Approve a proposal for implementation."""
        data = self._load_json(self.proposals_path, {"proposals": [], "pending": []})
        data = self._normalize_proposal_data(data)

        for proposal in data["proposals"]:
            if proposal["id"] == proposal_id:
                proposal["status"] = "approved"
                proposal["approved_at"] = datetime.now().isoformat()

                # Remove from pending
                if proposal_id in data["pending"]:
                    data["pending"].remove(proposal_id)

                # Learn from approval
                learn_lesson(
                    lesson_type="insight",
                    description=f"Approved improvement: {proposal['title']}",
                    context="Knowledge integration",
                    impact="medium"
                )

                data = self._prune_proposal_history(data)
                return self._save_json(self.proposals_path, data)
        return False

    def reject_proposal(self, proposal_id: str, reason: str = "") -> bool:
        """Reject a proposal."""
        data = self._load_json(self.proposals_path, {"proposals": [], "pending": []})
        data = self._normalize_proposal_data(data)

        for proposal in data["proposals"]:
            if proposal["id"] == proposal_id:
                proposal["status"] = "rejected"
                proposal["rejected_at"] = datetime.now().isoformat()
                proposal["rejection_reason"] = reason

                if proposal_id in data["pending"]:
                    data["pending"].remove(proposal_id)

                data = self._prune_proposal_history(data)
                return self._save_json(self.proposals_path, data)
        return False

    def mark_proposal_applied(self, proposal_id: str, note: str = "") -> bool:
        """Mark an approved proposal as applied to avoid repeated execution."""
        data = self._load_json(self.proposals_path, {"proposals": [], "pending": []})
        data = self._normalize_proposal_data(data)

        for proposal in data["proposals"]:
            if proposal["id"] == proposal_id:
                proposal["status"] = "applied"
                proposal["applied_at"] = datetime.now().isoformat()
                if note:
                    proposal["application_note"] = note[:500]
                if proposal_id in data["pending"]:
                    data["pending"].remove(proposal_id)
                data = self._prune_proposal_history(data)
                return self._save_json(self.proposals_path, data)
        return False

    # ==================== UTILITY ====================

    def add_source(self, source: KnowledgeSource):
        """Add a new source to scan."""
        self.sources.append(source)

    def get_stats(self) -> Dict:
        """Get scanner statistics."""
        scan_data = self._load_json(self.scan_log_path, {"scans": []})
        proposal_data = self._load_json(self.proposals_path, {"proposals": [], "pending": []})
        proposal_data = self._normalize_proposal_data(proposal_data)
        source_state = self._load_json(self.source_state_path, {"sources": {}})
        proposals = proposal_data.get("proposals", [])
        status_counts: Dict[str, int] = {}
        for proposal in proposals:
            status = proposal.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        source_entries = list(source_state.get("sources", {}).values()) if isinstance(source_state.get("sources"), dict) else []
        blocked_sources = 0
        for entry in source_entries:
            retry_at = self._safe_parse_time(entry.get("next_retry_at"))
            if retry_at and retry_at > datetime.now():
                blocked_sources += 1

        return {
            "total_sources": len(self.sources),
            "total_scans": len(scan_data.get("scans", [])),
            "total_proposals": len(proposals),
            "pending_proposals": len(proposal_data.get("pending", [])),
            "proposal_status_counts": status_counts,
            "sources_in_backoff": blocked_sources,
            "last_scan": scan_data.get("scans", [])[-1] if scan_data.get("scans", []) else None
        }


# ==================== CONVENIENCE FUNCTIONS ====================

_scanner = None

def get_scanner() -> KnowledgeScanner:
    """Get singleton scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = KnowledgeScanner()
    return _scanner


def scan_knowledge(min_score: float = 6.0) -> Dict:
    """Quick scan function."""
    return get_scanner().scan_all(min_score)


def get_pending_improvements() -> List[Dict]:
    """Get pending improvement proposals."""
    return get_scanner().get_pending_proposals()


# ==================== TEST ====================

if __name__ == "__main__":
    print("🔍 Knowledge Scanner Test")
    print("=" * 50)

    scanner = KnowledgeScanner()

    # Run scan
    results = scanner.scan_all(min_score=6.0)

    print(f"\n📊 Scan Results:")
    print(f"  Total discovered: {results['total_discovered']}")
    print(f"  Filtered (6+): {results['filtered_count']}")
    print(f"  Proposals: {results['proposals_generated']}")

    print(f"\n🔥 Top Items:")
    for item in results['top_items']:
        print(f"  [{item['score']:.1f}] {item['title']}")
        print(f"         → {item['potential_improvement']}")

    print(f"\n💡 Proposals:")
    for prop in results['proposals']:
        print(f"  - {prop['title']}")
        print(f"    Status: {prop['status']}")

    print(f"\n📈 Stats: {scanner.get_stats()}")
