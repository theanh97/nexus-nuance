"""
AUTO-EVOLVING SOURCE DISCOVERY ENGINE
=====================================
Tự động tìm, đánh giá, thêm sources mới - KHÔNG GIỚI HẠN

PRINCIPLES:
1. Tự research và tìm sources mới
2. Tự đánh giá chất lượng sources
3. Tự thêm/xóa sources dựa trên performance
4. Tự mở rộng sang lĩnh vực mới
5. KHÔNG BAO GIỜ NGỪNG PHÁT TRIỂN

DISCOVERY METHODS:
- Web crawling
- API discovery
- Social media monitoring
- User behavior analysis
- Cross-reference from existing sources
- Trend analysis
"""

import json
import os
import sys
import time
import threading
import random
import re
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
import subprocess

from core.nexus_logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"


@dataclass
class DiscoveredSource:
    """A newly discovered source"""
    url: str
    name: str
    category: str
    discovery_method: str
    discovered_at: str
    quality_score: float  # 0-1
    relevance_score: float  # 0-1
    verified: bool
    added_to_scout: bool
    test_result: Optional[str] = None


@dataclass
class Category:
    """A knowledge category"""
    name: str
    keywords: List[str]
    priority: int  # 1-10
    sources_count: int
    last_discovery: str
    expansion_potential: float  # 0-1


class AutoDiscoveryEngine:
    """
    Tự động phát hiện và thêm sources mới
    Không giới hạn, không ngừng phát triển
    """

    def __init__(self, scout=None, brain=None):
        self.scout = scout
        self.brain = brain
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Storage
        self.discovered_file = self.data_dir / "discovered_sources.json"
        self.categories_file = self.data_dir / "categories.json"
        self.evolution_log = self.data_dir / "evolution.log"

        # State
        self.discovered: List[DiscoveredSource] = []
        self.categories: Dict[str, Category] = {}
        self.discovered_urls: Set[str] = set()

        # Known source patterns
        self.source_patterns = {
            "github": r"github\.com/[\w-]+/[\w-]+",
            "reddit": r"reddit\.com/r/[\w]+",
            "blog": r"[\w-]+\.(medium|substack|blogspot|wordpress)\.com",
            "news": r"[\w-]+\.(techcrunch|theverge|wired|arstechnica)\.com",
            "arxiv": r"arxiv\.org/(abs|list)/[\w.-]+",
            "huggingface": r"huggingface\.co/[\w-]+",
            "papers": r"paperswithcode\.com/[\w/-]+",
            "pyPI": r"pypi\.org/project/[\w-]+",
            "npm": r"npmjs\.com/package/[\w-]+",
        }

        # Seed keywords for discovery
        self.seed_keywords = [
            # AI/ML
            "artificial intelligence", "machine learning", "deep learning",
            "neural network", "llm", "gpt", "transformer", "agents",
            "autonomous", "reinforcement learning", "computer vision",

            # Programming
            "programming", "software engineering", "devops", "cloud",
            "kubernetes", "docker", "ci/cd", "testing", "automation",

            # Business
            "startup", "entrepreneurship", "venture capital", "saas",
            "product management", "growth", "marketing",

            # Science
            "research", "paper", "study", "experiment", "data science",

            # Trends
            "trending", "new release", "beta", "alpha", "launch",
        ]

        # Initialize
        self._init_categories()
        self._load()
        self._start_discovery_thread()

    def _init_categories(self):
        """Initialize knowledge categories"""
        default_categories = {
            "technology": Category(
                name="technology",
                keywords=["programming", "software", "code", "developer", "api", "framework"],
                priority=10,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.9
            ),
            "ai_ml": Category(
                name="ai_ml",
                keywords=["ai", "machine learning", "neural", "llm", "gpt", "agent", "autonomous"],
                priority=10,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.95
            ),
            "business": Category(
                name="business",
                keywords=["startup", "funding", "vc", "revenue", "market", "growth"],
                priority=8,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.8
            ),
            "science": Category(
                name="science",
                keywords=["research", "paper", "study", "experiment", "discovery"],
                priority=9,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.85
            ),
            "devtools": Category(
                name="devtools",
                keywords=["library", "framework", "tool", "package", "extension", "plugin"],
                priority=8,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.9
            ),
            "product": Category(
                name="product",
                keywords=["product", "launch", "feature", "ux", "design", "user"],
                priority=7,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.75
            ),
            # NEW CATEGORIES - Auto-expand into new areas
            "security": Category(
                name="security",
                keywords=["cybersecurity", "hacking", "vulnerability", "encryption", "privacy"],
                priority=9,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.9
            ),
            "blockchain": Category(
                name="blockchain",
                keywords=["blockchain", "crypto", "web3", "defi", "smart contract"],
                priority=6,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.7
            ),
            "gaming": Category(
                name="gaming",
                keywords=["game", "gaming", "unity", "unreal", "game dev"],
                priority=5,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.6
            ),
            "health": Category(
                name="health",
                keywords=["healthtech", "medtech", "biotech", "medical", "health"],
                priority=7,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.8
            ),
            "education": Category(
                name="education",
                keywords=["edtech", "learning", "course", "tutorial", "education"],
                priority=6,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.75
            ),
            "finance": Category(
                name="finance",
                keywords=["fintech", "trading", "investment", "banking", "finance"],
                priority=7,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.75
            ),
            "iot": Category(
                name="iot",
                keywords=["iot", "embedded", "arduino", "raspberry pi", "sensors"],
                priority=6,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.7
            ),
            "robotics": Category(
                name="robotics",
                keywords=["robot", "robotics", "drone", "automation", "mechanical"],
                priority=7,
                sources_count=0,
                last_discovery=datetime.now().isoformat(),
                expansion_potential=0.85
            ),
        }

        self.categories = default_categories

    def _load(self):
        """Load discovered sources"""
        if self.discovered_file.exists():
            try:
                with open(self.discovered_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data.get("discovered", []):
                        source = DiscoveredSource(**item)
                        self.discovered.append(source)
                        self.discovered_urls.add(source.url)
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning(f"Failed to load discovered sources from {self.discovered_file}: {e}")
                pass

        if self.categories_file.exists():
            try:
                with open(self.categories_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                loaded_categories = {}
                for item in data.get("categories", []):
                    category = Category(**item)
                    loaded_categories[category.name] = category
                if loaded_categories:
                    self.categories = loaded_categories
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning(f"Failed to load categories from {self.categories_file}: {e}")
                pass

    def _save(self):
        """Save discovered sources"""
        with open(self.discovered_file, 'w', encoding='utf-8') as f:
            json.dump({
                "discovered": [vars(s) for s in self.discovered],
                "total": len(self.discovered),
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)
        with open(self.categories_file, 'w', encoding='utf-8') as f:
            json.dump({
                "categories": [vars(c) for c in self.categories.values()],
                "total": len(self.categories),
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    def _log(self, message: str, level: str = "INFO"):
        """Log evolution event"""
        timestamp = datetime.now().isoformat()
        line = f"[{timestamp}] [{level}] {message}\n"
        with open(self.evolution_log, 'a', encoding='utf-8') as f:
            f.write(line)
        if level == "ERROR":
            logger.error(message)
        elif level == "WARN":
            logger.warning(message)
        else:
            logger.info(message)

    # ==================== DISCOVERY METHODS ====================

    def discover_from_keywords(self) -> List[DiscoveredSource]:
        """Discover new sources from keywords"""
        discovered = []

        # Generate search queries
        for category_name, category in self.categories.items():
            for keyword in category.keywords[:3]:  # Top 3 keywords per category
                # Simulate discovery (in production would use web search)
                potential_sources = self._search_for_sources(keyword, category_name)
                discovered.extend(potential_sources)

        return discovered

    def _search_for_sources(self, keyword: str, category: str) -> List[DiscoveredSource]:
        """Search for sources related to keyword"""
        sources = []

        # Simulated discoveries based on keyword patterns
        discovery_templates = {
            "github": f"https://github.com/topics/{keyword.replace(' ', '-')}",
            "reddit": f"https://www.reddit.com/r/{keyword.replace(' ', '')}",
            "medium": f"https://medium.com/tag/{keyword.replace(' ', '-')}",
            "dev_to": f"https://dev.to/t/{keyword.replace(' ', '')}",
            "arxiv": f"https://arxiv.org/list/{keyword.split()[0]}.AI/recent",
            "papers": f"https://paperswithcode.com/search?q={keyword.replace(' ', '+')}",
        }

        for source_type, url_template in discovery_templates.items():
            url = url_template.lower()

            if url not in self.discovered_urls:
                source = DiscoveredSource(
                    url=url,
                    name=f"{source_type}_{keyword.replace(' ', '_')}",
                    category=category,
                    discovery_method="keyword_search",
                    discovered_at=datetime.now().isoformat(),
                    quality_score=random.uniform(0.5, 0.9),
                    relevance_score=random.uniform(0.6, 0.95),
                    verified=False,
                    added_to_scout=False
                )
                sources.append(source)
                self.discovered_urls.add(url)

        return sources

    def discover_from_cross_reference(self) -> List[DiscoveredSource]:
        """Discover from cross-referencing existing sources"""
        discovered = []

        # Simulate cross-reference discovery
        cross_ref_patterns = [
            ("github_trending", "github_awesome"),
            ("hacker_news", "hn_show"),
            ("reddit_programming", "reddit_learnprogramming"),
            ("arxiv_ai", "arxiv_cl"),
            ("techcrunch", "techcrunch_ai"),
        ]

        for source1, source2 in cross_ref_patterns:
            # In production, would analyze links from source1 to find source2
            pass

        return discovered

    def discover_from_trends(self) -> List[DiscoveredSource]:
        """Discover from trending topics"""
        discovered = []

        # Trending keywords that might indicate new sources
        trending = [
            "llm agents", "autonomous ai", "browser automation",
            "computer use", "ai coding", "prompt engineering",
            "rag", "vector database", "embedding models",
            "multimodal ai", "vision language models",
        ]

        for trend in trending:
            # Find relevant category
            best_category = "ai_ml"
            for cat_name, cat in self.categories.items():
                if any(kw in trend for kw in cat.keywords):
                    best_category = cat_name
                    break

            # Discover sources for this trend
            sources = self._search_for_sources(trend, best_category)
            discovered.extend(sources)

        return discovered

    def discover_new_categories(self) -> List[str]:
        """Discover entirely new categories to expand into"""
        new_categories = []

        # Potential new areas based on tech trends
        potential_areas = [
            ("quantum", ["quantum computing", "qubit", "quantum algorithm"]),
            ("space", ["spacetech", "satellite", "space exploration"]),
            ("energy", ["cleantech", "renewable energy", "battery tech"]),
            ("materials", ["material science", "nanotech", "new materials"]),
            ("agriculture", ["agtech", "precision farming", "vertical farming"]),
            ("legal", ["legaltech", "regtech", "compliance"]),
            ("hr", ["hrtech", "recruiting", "people analytics"]),
            ("real_estate", ["proptech", "smart buildings", "real estate"]),
        ]

        for area_name, keywords in potential_areas:
            if area_name not in self.categories:
                # Create new category
                self.categories[area_name] = Category(
                    name=area_name,
                    keywords=keywords,
                    priority=5,
                    sources_count=0,
                    last_discovery=datetime.now().isoformat(),
                    expansion_potential=0.6
                )
                new_categories.append(area_name)
                self._log(f"NEW CATEGORY DISCOVERED: {area_name}")

        return new_categories

    # ==================== VERIFICATION ====================

    def verify_source(self, source: DiscoveredSource) -> bool:
        """Verify if source is valid and useful"""
        # Simulate verification
        # In production would:
        # 1. Check if URL is accessible
        # 2. Analyze content quality
        # 3. Check update frequency
        # 4. Evaluate relevance

        quality_threshold = 0.6
        relevance_threshold = 0.5

        is_valid = (
            source.quality_score >= quality_threshold and
            source.relevance_score >= relevance_threshold
        )

        source.verified = is_valid
        source.test_result = "verified" if is_valid else "low_quality"

        return is_valid

    def add_to_scout(self, source: DiscoveredSource):
        """Add verified source to scout"""
        if not self.scout:
            return False

        # Import Scout's Source class
        try:
            from .omniscient_scout import Source
        except (ImportError, AttributeError) as e:
            logger.warning(f"Failed to import Scout Source class: {e}")
            return False

        # Create new source for scout
        new_source = Source(
            name=source.name,
            category=source.category,
            url=source.url,
            scan_interval_minutes=30,  # Default
            parser_type="html",
            enabled=True
        )

        # Add to scout
        self.scout.sources[source.name] = new_source
        source.added_to_scout = True

        self._log(f"SOURCE ADDED TO SCOUT: {source.name}")
        return True

    # ==================== CONTINUOUS EVOLUTION ====================

    def run_discovery_cycle(self) -> Dict:
        """Run one discovery cycle"""
        results = {
            "timestamp": datetime.now().isoformat(),
            "new_sources": 0,
            "new_categories": 0,
            "verified_sources": 0,
            "added_to_scout": 0,
        }

        # Discover new categories
        new_cats = self.discover_new_categories()
        results["new_categories"] = len(new_cats)

        # Discover from keywords
        keyword_sources = self.discover_from_keywords()
        results["new_sources"] += len(keyword_sources)
        self.discovered.extend(keyword_sources)

        # Discover from trends
        trend_sources = self.discover_from_trends()
        results["new_sources"] += len(trend_sources)
        self.discovered.extend(trend_sources)

        # Verify and add to scout
        for source in self.discovered[-20:]:  # Last 20 discovered
            if not source.verified:
                if self.verify_source(source):
                    results["verified_sources"] += 1
                    if self.add_to_scout(source):
                        results["added_to_scout"] += 1

        # Save
        self._save()

        # Log summary
        self._log(f"Discovery cycle: {results['new_sources']} new sources, {results['new_categories']} new categories")

        return results

    def _discovery_loop(self):
        """Background discovery loop"""
        while True:
            try:
                self.run_discovery_cycle()
                time.sleep(3600)  # Run every hour
            except Exception as e:
                self._log(f"Discovery error: {e}", "ERROR")
                time.sleep(300)

    def _start_discovery_thread(self):
        """Start background discovery thread"""
        thread = threading.Thread(target=self._discovery_loop, daemon=True)
        thread.start()

    # ==================== STATS ====================

    def get_stats(self) -> Dict:
        """Get discovery statistics"""
        method_counts = defaultdict(int)
        for source in self.discovered:
            method_counts[source.discovery_method] += 1
        return {
            "total_discovered": len(self.discovered),
            "verified_sources": sum(1 for s in self.discovered if s.verified),
            "added_to_scout": sum(1 for s in self.discovered if s.added_to_scout),
            "categories": len(self.categories),
            "category_list": list(self.categories.keys()),
            "discovery_methods": method_counts
        }


# ==================== CONVENIENCE ====================

_discovery_engine: Optional[AutoDiscoveryEngine] = None


def get_discovery() -> AutoDiscoveryEngine:
    """Get singleton discovery engine"""
    global _discovery_engine
    if _discovery_engine is None:
        _discovery_engine = AutoDiscoveryEngine()
    return _discovery_engine


def run_discovery() -> Dict:
    """Run discovery cycle"""
    return get_discovery().run_discovery_cycle()


def get_discovery_stats() -> Dict:
    """Get discovery stats"""
    return get_discovery().get_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("AUTO-EVOLVING SOURCE DISCOVERY ENGINE")
    print("=" * 70)
    print()

    engine = get_discovery()

    print("Initial Categories:")
    for name in engine.categories.keys():
        print(f"  • {name}")
    print()

    print("Running discovery cycle...")
    results = engine.run_discovery_cycle()

    print()
    print(f"Results:")
    print(f"  New Sources: {results['new_sources']}")
    print(f"  New Categories: {results['new_categories']}")
    print(f"  Verified: {results['verified_sources']}")
    print(f"  Added to Scout: {results['added_to_scout']}")

    print()
    print(f"Stats: {engine.get_stats()}")
    print()
    print("Discovery engine running in background...")
