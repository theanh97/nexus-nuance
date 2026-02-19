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
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from core.nexus_logger import get_logger

logger = get_logger(__name__)

try:
    from core.llm_caller import call_llm
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

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
        self.executor = ThreadPoolExecutor(max_workers=5)

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
                with open(self.sources_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for name, state in data.get("sources", {}).items():
                        if name in self.sources:
                            self.sources[name].last_scan = state.get("last_scan")
                            self.sources[name].last_error = state.get("last_error")
                            self.sources[name].total_findings = state.get("total_findings", 0)
            except Exception:
                # Keep default source state when state file is malformed.
                logger.warning("Failed to load scout state from %s, using defaults", self.sources_file)
                return

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
        with open(self.sources_file, 'w', encoding='utf-8') as f:
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
            logger.error("Scan failed for source %s: %s", source_name, e)

        self._save_state()
        return findings

    def scan_github_trending(self) -> List[Dict]:
        """Compatibility wrapper for NexusBrain."""
        findings = []
        for source_name in ("github_trending", "github_ai", "github_automation", "github_agents"):
            findings.extend(self.scan_source(source_name))
        return findings

    def scan_tech_news(self) -> List[Dict]:
        """Compatibility wrapper for NexusBrain."""
        findings = []
        for source_name in ("hacker_news", "techcrunch", "venturebeat_ai", "wired"):
            findings.extend(self.scan_source(source_name))
        return findings

    def _scan_html(self, source: Source) -> List[Dict]:
        """Scan HTML source"""
        findings = []

        try:
            req = urllib.request.Request(
                source.url,
                headers={"User-Agent": "NexusScout/1.0 (+https://nexus.local)"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="ignore")

            title_patterns = [
                r'<a[^>]+class="[^"]*titlelink[^"]*"[^>]*>(.*?)</a>',  # HN
                r'<h[1-3][^>]*>(.*?)</h[1-3]>',  # Headings
                r'<title[^>]*>(.*?)</title>',  # Page title fallback
            ]
            raw_titles = []
            for pattern in title_patterns:
                raw_titles.extend(re.findall(pattern, body, flags=re.IGNORECASE | re.DOTALL))
                if len(raw_titles) >= 10:
                    break

            cleaned_titles = []
            for raw in raw_titles:
                text = re.sub(r"<[^>]+>", "", raw)
                text = re.sub(r"\s+", " ", text).strip()
                if text and text not in cleaned_titles:
                    cleaned_titles.append(text)
                if len(cleaned_titles) >= 10:
                    break

            if not cleaned_titles:
                return [{
                    "title": f"No parsable content from {source.name}",
                    "type": "unavailable",
                    "relevance": 0.1,
                    "source": source.name,
                    "category": source.category,
                    "url": source.url,
                    "scanned_at": datetime.now().isoformat(),
                }]

            for title in cleaned_titles:
                ftype = "update"
                if "paper" in title.lower() or "arxiv" in source.name:
                    ftype = "paper"
                elif "release" in title.lower() or "launch" in title.lower():
                    ftype = "release"
                findings.append({
                    "title": title[:240],
                    "type": ftype,
                    "relevance": 0.7,
                    "source": source.name,
                    "category": source.category,
                    "url": source.url,
                    "scanned_at": datetime.now().isoformat(),
                })

        except urllib.error.HTTPError as e:
            findings = [{
                "title": f"Source unavailable: {source.name}",
                "type": "unavailable",
                "relevance": 0.0,
                "error": f"HTTP {e.code}: {e.reason}",
                "source": source.name,
                "category": source.category,
                "url": source.url,
                "scanned_at": datetime.now().isoformat(),
            }]
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            findings = [{
                "title": f"Source unavailable: {source.name}",
                "type": "unavailable",
                "relevance": 0.0,
                "error": f"Network error: {e}",
                "source": source.name,
                "category": source.category,
                "url": source.url,
                "scanned_at": datetime.now().isoformat(),
            }]
        except Exception as e:
            findings = [{
                "title": f"Source unavailable: {source.name}",
                "type": "unavailable",
                "relevance": 0.0,
                "error": str(e),
                "source": source.name,
                "category": source.category,
                "url": source.url,
                "scanned_at": datetime.now().isoformat(),
            }]

        return findings

    def _scan_rss(self, source: Source) -> List[Dict]:
        """Scan RSS feed"""
        findings = []

        try:
            req = urllib.request.Request(
                source.url,
                headers={"User-Agent": "NexusScout/1.0 (+https://nexus.local)"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            root = ET.fromstring(body)
            items = root.findall(".//item")[:10]
            if not items:
                return [{
                    "title": f"No RSS items from {source.name}",
                    "type": "unavailable",
                    "relevance": 0.1,
                    "source": source.name,
                    "category": source.category,
                    "url": source.url,
                    "scanned_at": datetime.now().isoformat(),
                }]

            for item in items:
                title_node = item.find("title")
                title = title_node.text.strip() if title_node is not None and title_node.text else f"Update from {source.name}"
                findings.append({
                    "title": title[:240],
                    "type": "update",
                    "relevance": 0.7,
                    "source": source.name,
                    "category": source.category,
                    "url": source.url,
                    "scanned_at": datetime.now().isoformat(),
                })
        except urllib.error.HTTPError as e:
            findings = [{
                "title": f"RSS unavailable: {source.name}",
                "type": "unavailable",
                "relevance": 0.0,
                "error": f"HTTP {e.code}: {e.reason}",
                "source": source.name,
                "category": source.category,
                "url": source.url,
                "scanned_at": datetime.now().isoformat(),
            }]
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            findings = [{
                "title": f"RSS unavailable: {source.name}",
                "type": "unavailable",
                "relevance": 0.0,
                "error": f"Network error: {e}",
                "source": source.name,
                "category": source.category,
                "url": source.url,
                "scanned_at": datetime.now().isoformat(),
            }]
        except Exception as e:
            findings = [{
                "title": f"RSS unavailable: {source.name}",
                "type": "unavailable",
                "relevance": 0.0,
                "error": str(e),
                "source": source.name,
                "category": source.category,
                "url": source.url,
                "scanned_at": datetime.now().isoformat(),
            }]

        return findings

    def _scan_api(self, source: Source) -> List[Dict]:
        """Scan API endpoint"""
        return [{
            "title": f"API source unsupported without credentials: {source.name}",
            "type": "unsupported",
            "relevance": 0.0,
            "source": source.name,
            "category": source.category,
            "url": source.url,
            "scanned_at": datetime.now().isoformat(),
        }]

    def _store_finding(self, source: Source, finding: Dict):
        """Store finding"""
        finding["id"] = hashlib.md5(f"{source.name}{finding.get('title', '')}{datetime.now()}".encode()).hexdigest()[:12]
        finding["stored_at"] = datetime.now().isoformat()

        with open(self.findings_file, 'a', encoding='utf-8') as f:
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
            except Exception as e:
                finding["brain_ingest_error"] = str(e)
                logger.warning("Brain ingest failed for finding '%s': %s", finding.get("title", "?"), e)

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
                logger.error("Batch scan failed for %s: %s", name, e)
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
                    logger.error("Continuous scan loop error: %s", e)
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

    def score_source_quality(self, source_name: str) -> Dict:
        """Score a source's quality using LLM analysis or heuristics."""
        source = self.sources.get(source_name)
        if not source:
            return {'error': f'Source {source_name} not found'}

        # Basic heuristic scoring
        score = 0.5
        reasons = []

        # Frequency bonus
        if source.total_findings > 10:
            score += 0.1
            reasons.append(f'High finding count ({source.total_findings})')
        elif source.total_findings == 0:
            score -= 0.2
            reasons.append('No findings yet')

        # Recency bonus
        if source.last_scan:
            try:
                from datetime import datetime
                last = datetime.fromisoformat(source.last_scan)
                hours_ago = (datetime.now() - last).total_seconds() / 3600
                if hours_ago < 24:
                    score += 0.1
                    reasons.append('Recently scanned')
                elif hours_ago > 168:
                    score -= 0.1
                    reasons.append('Stale (>7 days)')
            except (ValueError, TypeError):
                pass

        # Error penalty
        if source.last_error:
            score -= 0.2
            reasons.append(f'Has error: {source.last_error[:50]}')

        # LLM quality assessment
        if _LLM_AVAILABLE:
            try:
                llm_result = call_llm(
                    prompt=f'Rate the quality of this data source for an AI learning system (0-1 scale). Source: {source.name}, Category: {source.category}, URL: {source.url}, Findings: {source.total_findings}, Scan interval: {source.scan_interval_minutes}min. Return a single float number.',
                    task_type='general',
                    max_tokens=20,
                    temperature=0.1,
                )
                if isinstance(llm_result, str):
                    text = llm_result.strip()
                    # Extract float from response
                    import re
                    match = re.search(r'(\d+\.?\d*)', text)
                    if match:
                        llm_score = float(match.group(1))
                        if 0.0 <= llm_score <= 1.0:
                            score = score * 0.5 + llm_score * 0.5
                            reasons.append(f'LLM quality score: {llm_score:.2f}')
            except Exception as e:
                logger.debug("LLM quality scoring failed for %s: %s", source_name, e)

        return {
            'source': source_name,
            'quality_score': round(min(1.0, max(0.0, score)), 2),
            'reasons': reasons,
            'category': source.category,
            'total_findings': source.total_findings,
        }

    def get_ranked_sources(self) -> List[Dict]:
        """Get all sources ranked by quality score."""
        scores = []
        for name in self.sources:
            scores.append(self.score_source_quality(name))
        scores.sort(key=lambda x: x.get('quality_score', 0), reverse=True)
        return scores

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
