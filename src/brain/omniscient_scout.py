"""
OMNISCIENT SCOUT - ALL-KNOWING KNOWLEDGE ACQUISITION SYSTEM
============================================================
Học từ MỌI nguồn, MỌI lĩnh vực - Không giới hạn

SOURCES:
├── Technology
│   ├── GitHub Trending
│   ├── Hacker News
│   ├── Reddit r/programming
│   ├── Stack Overflow
│   ├── Dev.to
│   └── Medium Tech
│
├── AI/ML
│   ├── Papers With Code
│   ├── Hugging Face
│   ├── arXiv AI
│   ├── OpenAI Blog
│   ├── Anthropic Blog
│   └── Google AI Blog
│
├── Business
│   ├── TechCrunch
│   ├── VentureBeat
│   ├── Business Insider
│   └── Forbes Tech
│
├── Science
│   ├── Nature
│   ├── Science Daily
│   ├── arXiv
│   └── PubMed
│
├── Social
│   ├── Twitter/X Trends
│   ├── LinkedIn Tech
│   └── Product Hunt
│
├── Developer Tools
│   ├── npm trending
│   ├── PyPI new releases
│   ├── Docker Hub
│   └── VS Code Extensions
│
└── Local/System
    ├── Codebase patterns
    ├── Error logs
    ├── Performance metrics
    └── User interactions
"""

import json
import os
import sys
import time
import threading
import subprocess
import hashlib
import re
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"


@dataclass
class Source:
    """A knowledge source"""
    name: str
    category: str
    url: str
    scan_interval_minutes: int
    parser_type: str  # json, html, rss, api
    enabled: bool = True
    last_scan: str = None
    last_error: str = None
    total_findings: int = 0


class OmniscientScout:
    """
    ALL-KNOWING SCOUT
    Quét và học từ MỌI nguồn có thể
    """

    def __init__(self, brain=None):
        self.brain = brain
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.sources_file = self.data_dir / "sources.json"
        self.findings_file = self.data_dir / "findings.jsonl"

        # Sources configuration
        self.sources: Dict[str, Source] = {}
        self.findings_cache: List[Dict] = []
        self.executor = ThreadPoolExecutor(max_workers=10)

        # Initialize all sources
        self._init_sources()
        self._load_state()

    def _init_sources(self):
        """Initialize ALL knowledge sources"""

        # ==================== TECHNOLOGY ====================
        tech_sources = [
            Source("github_trending", "technology", "https://github.com/trending", 15, "html"),
            Source("github_ai", "technology", "https://github.com/topics/artificial-intelligence", 15, "html"),
            Source("github_automation", "technology", "https://github.com/topics/automation", 15, "html"),
            Source("github_agents", "technology", "https://github.com/topics/autonomous-agents", 15, "html"),
            Source("hacker_news", "technology", "https://news.ycombinator.com", 5, "html"),
            Source("reddit_programming", "technology", "https://www.reddit.com/r/programming", 10, "html"),
            Source("reddit_machinelearning", "technology", "https://www.reddit.com/r/MachineLearning", 10, "html"),
            Source("dev_to", "technology", "https://dev.to", 15, "html"),
            Source("stack_overflow", "technology", "https://stackoverflow.com/questions", 30, "html"),
        ]

        # ==================== AI/ML ====================
        ai_sources = [
            Source("papers_with_code", "ai_ml", "https://paperswithcode.com", 30, "html"),
            Source("huggingface_models", "ai_ml", "https://huggingface.co/models", 30, "html"),
            Source("arxiv_ai", "ai_ml", "https://arxiv.org/list/cs.AI/recent", 60, "html"),
            Source("arxiv_ml", "ai_ml", "https://arxiv.org/list/cs.LG/recent", 60, "html"),
            Source("openai_blog", "ai_ml", "https://openai.com/blog", 120, "html"),
            Source("anthropic_blog", "ai_ml", "https://www.anthropic.com/news", 120, "html"),
            Source("google_ai", "ai_ml", "https://ai.googleblog.com", 120, "html"),
            Source("deepmind", "ai_ml", "https://deepmind.com/blog", 120, "html"),
        ]

        # ==================== BUSINESS ====================
        business_sources = [
            Source("techcrunch", "business", "https://techcrunch.com", 30, "html"),
            Source("venturebeat_ai", "business", "https://venturebeat.com/category/ai", 30, "html"),
            Source("business_insider_tech", "business", "https://www.businessinsider.com/tech", 60, "html"),
            Source("forbes_tech", "business", "https://www.forbes.com/technology", 60, "html"),
            Source("wired", "business", "https://www.wired.com", 60, "html"),
        ]

        # ==================== SCIENCE ====================
        science_sources = [
            Source("nature_ai", "science", "https://www.nature.com/subjects/machine-learning", 120, "html"),
            Source("science_daily", "science", "https://www.sciencedaily.com/news/computers_math/artificial_intelligence", 120, "html"),
            Source("arxiv_cs", "science", "https://arxiv.org/list/cs/recent", 60, "html"),
        ]

        # ==================== DEVELOPER TOOLS ====================
        dev_sources = [
            Source("npm_trending", "devtools", "https://www.npmjs.com/browse/depended", 60, "html"),
            Source("pypi_new", "devtools", "https://pypi.org/rss/updates.xml", 30, "rss"),
            Source("docker_hub", "devtools", "https://hub.docker.com/search", 120, "html"),
            Source("vscode_extensions", "devtools", "https://marketplace.visualstudio.com/vscode", 120, "html"),
        ]

        # ==================== PRODUCT ====================
        product_sources = [
            Source("product_hunt", "product", "https://www.producthunt.com", 60, "html"),
            Source("indie_hackers", "product", "https://www.indiehackers.com", 120, "html"),
        ]

        # ==================== SOCIAL ====================
        social_sources = [
            # Note: These would need API keys in production
            # Source("twitter_trends", "social", "api", 5, "api"),
            # Source("linkedin_tech", "social", "api", 30, "api"),
        ]

        # Add all sources
        all_sources = (
            tech_sources + ai_sources + business_sources +
            science_sources + dev_sources + product_sources + social_sources
        )

        for source in all_sources:
            self.sources[source.name] = source

    def _load_state(self):
        """Load saved state"""
        if self.sources_file.exists():
            try:
                with open(self.sources_file, 'r') as f:
                    data = json.load(f)
                    for name, state in data.get("sources", {}).items():
                        if name in self.sources:
                            self.sources[name].last_scan = state.get("last_scan")
                            self.sources[name].last_error = state.get("last_error")
                            self.sources[name].total_findings = state.get("total_findings", 0)
            except:
                pass

    def _save_state(self):
        """Save current state"""
        state = {
            "sources": {
                name: {
                    "last_scan": s.last_scan,
                    "last_error": s.last_error,
                    "total_findings": s.total_findings
                }
                for name, s in self.sources.items()
            },
            "last_updated": datetime.now().isoformat()
        }
        with open(self.sources_file, 'w') as f:
            json.dump(state, f, indent=2)

    # ==================== SCANNING ====================

    def scan_source(self, source_name: str) -> List[Dict]:
        """Scan a specific source"""
        if source_name not in self.sources:
            return []

        source = self.sources[source_name]

        # Check if should scan
        if source.last_scan:
            last = datetime.fromisoformat(source.last_scan)
            if datetime.now() - last < timedelta(minutes=source.scan_interval_minutes):
                return []  # Too soon

        findings = []

        try:
            if source.parser_type == "html":
                findings = self._scan_html(source)
            elif source.parser_type == "rss":
                findings = self._scan_rss(source)
            elif source.parser_type == "api":
                findings = self._scan_api(source)

            source.last_scan = datetime.now().isoformat()
            source.last_error = None
            source.total_findings += len(findings)

            # Store findings
            for finding in findings:
                self._store_finding(source, finding)

        except Exception as e:
            source.last_error = str(e)

        self._save_state()
        return findings

    def _scan_html(self, source: Source) -> List[Dict]:
        """Scan HTML source"""
        findings = []

        try:
            # Simulated HTML scanning (in production would use requests + BeautifulSoup)
            # For now, generate relevant findings based on source

            simulated_findings = {
                "github_trending": [
                    {"title": "AI Agent Framework Rising", "type": "trend", "relevance": 0.9},
                    {"title": "New Browser Automation Tool", "type": "library", "relevance": 0.85},
                ],
                "hacker_news": [
                    {"title": "Breakthrough in Autonomous Systems", "type": "news", "relevance": 0.9},
                    {"title": "Open Source LLM Discussion", "type": "discussion", "relevance": 0.8},
                ],
                "papers_with_code": [
                    {"title": "New SOTA in Agent Reasoning", "type": "paper", "relevance": 0.95},
                    {"title": "Multi-agent Collaboration Methods", "type": "paper", "relevance": 0.9},
                ],
                "arxiv_ai": [
                    {"title": "Self-Improving AI Systems", "type": "paper", "relevance": 0.95},
                    {"title": "Autonomous Code Generation", "type": "paper", "relevance": 0.9},
                ],
                "techcrunch": [
                    {"title": "AI Startup Funding Round", "type": "business", "relevance": 0.7},
                    {"title": "Enterprise AI Adoption Trends", "type": "trend", "relevance": 0.75},
                ],
                "product_hunt": [
                    {"title": "AI Productivity Tool Launch", "type": "product", "relevance": 0.8},
                    {"title": "No-code AI Platform", "type": "product", "relevance": 0.75},
                ],
            }

            findings = simulated_findings.get(source.name, [
                {"title": f"Update from {source.name}", "type": "update", "relevance": 0.6}
            ])

            # Add metadata
            for f in findings:
                f["source"] = source.name
                f["category"] = source.category
                f["url"] = source.url
                f["scanned_at"] = datetime.now().isoformat()

        except Exception as e:
            findings = [{"error": str(e), "source": source.name}]

        return findings

    def _scan_rss(self, source: Source) -> List[Dict]:
        """Scan RSS feed"""
        findings = []

        try:
            # RSS parsing would go here
            findings = [
                {"title": f"RSS Update from {source.name}", "type": "update", "relevance": 0.7}
            ]
        except:
            pass

        return findings

    def _scan_api(self, source: Source) -> List[Dict]:
        """Scan API endpoint"""
        findings = []

        # API scanning would require credentials
        # For now, return placeholder

        return findings

    def _store_finding(self, source: Source, finding: Dict):
        """Store finding"""
        finding["id"] = hashlib.md5(f"{source.name}{finding.get('title', '')}{datetime.now()}".encode()).hexdigest()[:12]
        finding["stored_at"] = datetime.now().isoformat()

        with open(self.findings_file, 'a') as f:
            f.write(json.dumps(finding) + "\n")

        self.findings_cache.append(finding)

        # Also learn to brain if available
        if self.brain:
            try:
                self.brain.learn_knowledge(
                    source=finding.get("source", "scout"),
                    type=finding.get("type", "general"),
                    title=finding.get("title", "Finding"),
                    content=json.dumps(finding),
                    url=finding.get("url"),
                    relevance=finding.get("relevance", 0.7)
                )
            except:
                pass

    # ==================== BATCH SCANNING ====================

    def scan_all(self) -> Dict[str, List[Dict]]:
        """Scan ALL sources"""
        results = {}

        futures = {}
        for name, source in self.sources.items():
            if source.enabled:
                future = self.executor.submit(self.scan_source, name)
                futures[future] = name

        for future in as_completed(futures):
            name = futures[future]
            try:
                findings = future.result(timeout=60)
                results[name] = findings
            except Exception as e:
                results[name] = [{"error": str(e)}]

        return results

    def scan_category(self, category: str) -> Dict[str, List[Dict]]:
        """Scan all sources in a category"""
        results = {}

        for name, source in self.sources.items():
            if source.category == category and source.enabled:
                findings = self.scan_source(name)
                results[name] = findings

        return results

    # ==================== CONTINUOUS SCANNING ====================

    def start_continuous_scanning(self):
        """Start continuous background scanning"""

        def scan_loop():
            while True:
                try:
                    # Scan sources based on their intervals
                    for name, source in self.sources.items():
                        if not source.enabled:
                            continue

                        should_scan = False
                        if source.last_scan is None:
                            should_scan = True
                        else:
                            last = datetime.fromisoformat(source.last_scan)
                            if datetime.now() - last >= timedelta(minutes=source.scan_interval_minutes):
                                should_scan = True

                        if should_scan:
                            self.scan_source(name)
                            time.sleep(1)  # Rate limiting

                    time.sleep(60)  # Check every minute

                except Exception as e:
                    time.sleep(60)

        thread = threading.Thread(target=scan_loop, daemon=True)
        thread.start()
        return thread

    # ==================== QUERIES ====================

    def get_source_stats(self) -> Dict:
        """Get statistics for all sources"""
        stats = {
            "total_sources": len(self.sources),
            "enabled_sources": sum(1 for s in self.sources.values() if s.enabled),
            "total_findings": sum(s.total_findings for s in self.sources.values()),
            "by_category": defaultdict(int),
            "sources": {}
        }

        for name, source in self.sources.items():
            stats["by_category"][source.category] += 1
            stats["sources"][name] = {
                "category": source.category,
                "enabled": source.enabled,
                "last_scan": source.last_scan,
                "total_findings": source.total_findings,
                "last_error": source.last_error
            }

        return stats

    def get_recent_findings(self, limit: int = 50) -> List[Dict]:
        """Get recent findings"""
        return self.findings_cache[-limit:]


# ==================== CONVENIENCE FUNCTIONS ====================

_scout: Optional[OmniscientScout] = None


def get_scout() -> OmniscientScout:
    """Get singleton scout instance"""
    global _scout
    if _scout is None:
        _scout = OmniscientScout()
    return _scout


def scan_all_sources() -> Dict:
    """Scan all sources"""
    return get_scout().scan_all()


def get_source_stats() -> Dict:
    """Get source statistics"""
    return get_scout().get_source_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("OMNISCIENT SCOUT - ALL-KNOWING KNOWLEDGE ACQUISITION SYSTEM")
    print("=" * 70)
    print()

    scout = get_scout()

    # Print all sources
    stats = scout.get_source_stats()
    print(f"Total Sources: {stats['total_sources']}")
    print(f"Categories: {dict(stats['by_category'])}")
    print()

    print("SOURCES BY CATEGORY:")
    print("-" * 70)

    current_category = None
    for name, info in stats["sources"].items():
        if info["category"] != current_category:
            current_category = info["category"]
            print(f"\n[{current_category.upper()}]")

        interval = scout.sources[name].scan_interval_minutes
        print(f"  • {name}: every {interval}min")

    print()
    print("-" * 70)
    print("Starting continuous scanning...")
    print()

    # Start continuous scanning
    scout.start_continuous_scanning()

    # Run for a bit to show it works
    import time
    for i in range(5):
        time.sleep(60)
        recent = scout.get_recent_findings(5)
        print(f"[{datetime.now().isoformat()}] Findings: {len(recent)}")
