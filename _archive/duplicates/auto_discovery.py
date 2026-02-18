"""
Auto-Discovery Engine
Tự động scan 30+ nguồn mỗi ngày để tìm kiến thức mới.

Không đợi user hỏi - CHỦ ĐỘNG tìm kiếm!

Categories:
- AI/ML Research: ArXiv, HuggingFace, Papers With Code
- AI News: OpenAI, Anthropic, Google, DeepMind, Meta blogs
- Developer Tools: GitHub Trending, Product Hunt
- Tech News: Hacker News, Reddit
- Frameworks: LangChain, LlamaIndex, CrewAI, AutoGen, ChromaDB
- Automation: Playwright, Browser-Use, Stagehand, OpenClaw
"""

import json
import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set
from pathlib import Path
import threading
import re

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class AutoDiscoveryEngine:
    """
    Tự động discovery knowledge từ 30+ sources.
    Chạy mỗi ngày mà KHÔNG cần user prompt.
    """

    def __init__(self, config_path: str = None):
        self._lock = threading.RLock()

        # Paths
        if config_path:
            self.config_path = Path(config_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.config_path = project_root / "data" / "config" / "auto_discovery_sources.json"
            except:
                self.config_path = Path.cwd() / "data" / "config" / "auto_discovery_sources.json"

        self.data_path = self.config_path.parent.parent / "discovery"
        self.data_path.mkdir(parents=True, exist_ok=True)

        self.discovered_file = self.data_path / "discovered_items.json"
        self.state_file = self.data_path / "scan_state.json"

        # Config
        self.sources: Dict[str, List[Dict]] = {}
        self.keywords_to_watch: List[str] = []
        self.settings: Dict = {}

        # State
        self.discovered_items: List[Dict] = []
        self.scan_state: Dict = {}
        self.seen_hashes: Set[str] = set()

        # Session
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-AutoDiscovery/2.0)',
                'Accept': 'application/json, text/html, application/xml'
            })

        self._load_config()
        self._load_state()

    def _load_config(self):
        """Load sources configuration."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    config = json.load(f)

                self.sources = config.get("sources", {})
                self.keywords_to_watch = config.get("keywords_to_watch", [])
                self.settings = config.get("auto_discovery_settings", {
                    "max_items_per_source": 20,
                    "min_score_threshold": 5.0,
                    "auto_approve_threshold": 7.0,
                    "deduplication_enabled": True,
                    "keyword_boost": 1.5,
                    "recency_bonus_days": 7
                })
            except Exception as e:
                print(f"Warning: Could not load auto-discovery config: {e}")
                self._create_default_config()

    def _create_default_config(self):
        """Create default sources if not exists."""
        self.sources = {
            "ai_ml_research": [
                {"name": "ArXiv CS.AI", "url": "http://export.arxiv.org/api/query",
                 "type": "api", "category": "ai_ml", "priority": 10, "enabled": True}
            ],
            "tech_news": [
                {"name": "Hacker News", "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
                 "type": "api", "category": "tech_news", "priority": 8, "enabled": True}
            ],
            "developer_tools": [
                {"name": "GitHub Trending", "url": "https://api.github.com/search/repositories",
                 "type": "api", "category": "tools", "priority": 9, "enabled": True}
            ]
        }
        self.keywords_to_watch = ["AI", "LLM", "agent", "RAG", "automation"]

    def _load_state(self):
        """Load previous state."""
        if self.discovered_file.exists():
            try:
                with open(self.discovered_file, 'r') as f:
                    data = json.load(f)
                    self.discovered_items = data.get("items", [])[-5000:]  # Keep last 5000
                    # Build seen hashes
                    for item in self.discovered_items:
                        h = item.get("hash", "")
                        if h:
                            self.seen_hashes.add(h)
            except Exception:
                pass

        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    self.scan_state = json.load(f)
            except Exception:
                pass

    def _save_state(self):
        """Save current state."""
        with self._lock:
            try:
                with open(self.discovered_file, 'w') as f:
                    json.dump({
                        "items": self.discovered_items[-5000:],
                        "last_updated": datetime.now().isoformat(),
                        "total_discovered": len(self.discovered_items)
                    }, f, indent=2)
            except Exception:
                pass

            try:
                with open(self.state_file, 'w') as f:
                    json.dump(self.scan_state, f, indent=2)
            except Exception:
                pass

    def _hash_item(self, title: str, source: str) -> str:
        """Create hash for deduplication."""
        content = f"{title}|{source}".lower()
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _calculate_score(self, item: Dict, source_config: Dict) -> float:
        """Calculate discovery score."""
        base_score = float(source_config.get("priority", 5))

        title = item.get("title", "").lower()
        description = item.get("description", "").lower()
        content = f"{title} {description}"

        # Keyword boost
        keyword_boost = self.settings.get("keyword_boost", 1.5)
        keyword_matches = 0
        for keyword in self.keywords_to_watch:
            if keyword.lower() in content:
                keyword_matches += 1
                base_score += 0.3

        # Recency bonus
        recency_days = self.settings.get("recency_bonus_days", 7)
        created = item.get("created_at") or item.get("published_at") or item.get("fetched_at")
        if created:
            try:
                created_date = datetime.fromisoformat(created.replace("Z", "+00:00").replace("+00:00", ""))
                days_old = (datetime.now() - created_date.replace(tzinfo=None)).days
                if days_old <= recency_days:
                    base_score += (recency_days - days_old) * 0.1
            except Exception:
                pass

        # Engagement bonus (stars, upvotes, etc.)
        if item.get("stars"):
            base_score += min(item["stars"] / 1000, 2.0)
        if item.get("score"):  # HN score
            base_score += min(item["score"] / 100, 1.5)
        if item.get("upvotes"):
            base_score += min(item["upvotes"] / 100, 1.0)

        return min(base_score, 10.0)

    def _should_scan(self, source: Dict) -> bool:
        """Check if source should be scanned based on frequency."""
        if not source.get("enabled", True):
            return False

        freq = source.get("scan_frequency", "daily")
        name = source.get("name", "unknown")

        last_scan = self.scan_state.get(name, {}).get("last_scan")
        if not last_scan:
            return True

        try:
            last_scan_time = datetime.fromisoformat(last_scan)
            now = datetime.now()

            if freq == "hourly":
                return (now - last_scan_time).total_seconds() >= 3600
            elif freq == "daily":
                return (now - last_scan_time).total_seconds() >= 86400
            elif freq == "weekly":
                return (now - last_scan_time).total_seconds() >= 604800
            else:
                return True
        except Exception:
            return True

    # ==================== SOURCE FETCHERS ====================

    def fetch_arxiv(self, source: Dict) -> List[Dict]:
        """Fetch from ArXiv API."""
        items = []
        if not REQUESTS_AVAILABLE:
            return items

        try:
            params = source.get("params", {})
            params.setdefault("search_query", "cat:cs.AI OR cat:cs.LG")
            params.setdefault("max_results", 20)
            params.setdefault("sortBy", "submittedDate")
            params.setdefault("sortOrder", "descending")

            response = self.session.get(source["url"], params=params, timeout=30)

            if response.status_code == 200:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.content)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}

                for entry in root.findall('atom:entry', ns):
                    title_elem = entry.find('atom:title', ns)
                    summary_elem = entry.find('atom:summary', ns)
                    link_elem = entry.find('atom:id', ns)
                    published_elem = entry.find('atom:published', ns)

                    if title_elem is not None:
                        items.append({
                            "title": title_elem.text.strip() if title_elem.text else "",
                            "description": (summary_elem.text[:300] if summary_elem is not None and summary_elem.text else "")[:200],
                            "url": link_elem.text if link_elem is not None else "",
                            "source": source["name"],
                            "category": source.get("category", "ai_ml"),
                            "published_at": published_elem.text if published_elem is not None else None,
                            "fetched_at": datetime.now().isoformat()
                        })
        except Exception as e:
            print(f"ArXiv fetch error: {e}")

        return items

    def fetch_github(self, source: Dict) -> List[Dict]:
        """Fetch from GitHub API."""
        items = []
        if not REQUESTS_AVAILABLE:
            return items

        try:
            params = source.get("params", {})
            headers = {"Accept": "application/vnd.github.v3+json"}

            # Check if it's releases endpoint
            if "releases" in source["url"]:
                response = self.session.get(source["url"], headers=headers, timeout=30)
                if response.status_code == 200:
                    releases = response.json()[:10]
                    for release in releases:
                        items.append({
                            "title": f"{source['name']} {release.get('tag_name', 'release')}",
                            "description": (release.get("body", "") or "")[:200],
                            "url": release.get("html_url", ""),
                            "source": source["name"],
                            "category": source.get("category", "tools"),
                            "published_at": release.get("published_at"),
                            "fetched_at": datetime.now().isoformat()
                        })
            else:
                # Search endpoint
                response = self.session.get(source["url"], params=params, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    for repo in data.get("items", [])[:20]:
                        items.append({
                            "title": repo.get("full_name", ""),
                            "description": (repo.get("description") or "")[:200],
                            "url": repo.get("html_url", ""),
                            "source": "GitHub Trending",
                            "category": source.get("category", "tools"),
                            "stars": repo.get("stargazers_count", 0),
                            "language": repo.get("language", ""),
                            "published_at": repo.get("pushed_at"),
                            "fetched_at": datetime.now().isoformat()
                        })
        except Exception as e:
            print(f"GitHub fetch error: {e}")

        return items

    def fetch_hackernews(self, source: Dict) -> List[Dict]:
        """Fetch from Hacker News."""
        items = []
        if not REQUESTS_AVAILABLE:
            return items

        try:
            # Get story IDs
            if "algolia" in source["url"]:
                # Search endpoint
                params = source.get("params", {})
                response = self.session.get(source["url"], params=params, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    for hit in data.get("hits", [])[:20]:
                        items.append({
                            "title": hit.get("title", ""),
                            "description": "",
                            "url": hit.get("url", ""),
                            "source": "Hacker News",
                            "category": source.get("category", "tech_news"),
                            "score": hit.get("points", 0),
                            "published_at": datetime.fromtimestamp(hit.get("created_at_i", 0)).isoformat() if hit.get("created_at_i") else None,
                            "fetched_at": datetime.now().isoformat()
                        })
            else:
                # Top stories endpoint
                response = self.session.get(source["url"], timeout=30)
                if response.status_code == 200:
                    story_ids = response.json()[:20]
                    for story_id in story_ids:
                        try:
                            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                            story_response = self.session.get(story_url, timeout=10)
                            if story_response.status_code == 200:
                                story = story_response.json()
                                if story and story.get("type") == "story":
                                    items.append({
                                        "title": story.get("title", ""),
                                        "description": f"Score: {story.get('score', 0)}, Comments: {story.get('descendants', 0)}",
                                        "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                                        "source": "Hacker News",
                                        "category": source.get("category", "tech_news"),
                                        "score": story.get("score", 0),
                                        "published_at": datetime.fromtimestamp(story.get("time", 0)).isoformat() if story.get("time") else None,
                                        "fetched_at": datetime.now().isoformat()
                                    })
                        except Exception:
                            continue
        except Exception as e:
            print(f"Hacker News fetch error: {e}")

        return items

    def fetch_reddit(self, source: Dict) -> List[Dict]:
        """Fetch from Reddit."""
        items = []
        if not REQUESTS_AVAILABLE:
            return items

        try:
            headers = {"User-Agent": "NEXUS-AutoDiscovery/2.0"}
            response = self.session.get(source["url"], headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                posts = data.get("data", {}).get("children", [])

                for post in posts[:20]:
                    post_data = post.get("data", {})
                    items.append({
                        "title": post_data.get("title", ""),
                        "description": (post_data.get("selftext") or "")[:200],
                        "url": f"https://reddit.com{post_data.get('permalink', '')}",
                        "source": f"Reddit {source['name']}",
                        "category": source.get("category", "tech_news"),
                        "score": post_data.get("score", 0),
                        "upvotes": post_data.get("ups", 0),
                        "published_at": datetime.fromtimestamp(post_data.get("created_utc", 0)).isoformat() if post_data.get("created_utc") else None,
                        "fetched_at": datetime.now().isoformat()
                    })
        except Exception as e:
            print(f"Reddit fetch error: {e}")

        return items

    def fetch_source(self, source: Dict) -> List[Dict]:
        """Fetch from a specific source based on type."""
        source_type = source.get("type", "api")
        url = source.get("url", "").lower()

        if "arxiv" in url:
            return self.fetch_arxiv(source)
        elif "github" in url:
            return self.fetch_github(source)
        elif "hacker-news" in url or "hn.algolia" in url:
            return self.fetch_hackernews(source)
        elif "reddit" in url:
            return self.fetch_reddit(source)
        else:
            # Generic fetch
            return self._fetch_generic(source)

    def _fetch_generic(self, source: Dict) -> List[Dict]:
        """Generic API fetch."""
        items = []
        if not REQUESTS_AVAILABLE:
            return items

        try:
            params = source.get("params", {})
            headers = source.get("headers", {})
            response = self.session.get(source["url"], params=params, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()

                # Try different response formats
                results = []
                if isinstance(data, list):
                    results = data
                elif isinstance(data, dict):
                    results = data.get("items", data.get("results", data.get("data", data.get("hits", []))))

                for item in results[:20]:
                    title = item.get("title", item.get("name", item.get("full_name", "")))
                    if title:
                        items.append({
                            "title": title,
                            "description": (item.get("description") or item.get("body") or "")[:200],
                            "url": item.get("url", item.get("html_url", item.get("link", ""))),
                            "source": source["name"],
                            "category": source.get("category", "general"),
                            "fetched_at": datetime.now().isoformat()
                        })
        except Exception as e:
            print(f"Generic fetch error for {source.get('name')}: {e}")

        return items

    # ==================== MAIN DISCOVERY ====================

    def run_discovery(self, force_all: bool = False) -> Dict:
        """
        Run auto-discovery from all sources.
        Returns discovered items and statistics.
        """
        results = {
            "started_at": datetime.now().isoformat(),
            "sources_scanned": 0,
            "sources_skipped": 0,
            "items_discovered": 0,
            "items_new": 0,
            "items_filtered": 0,
            "errors": [],
            "top_items": []
        }

        all_items = []
        threshold = self.settings.get("min_score_threshold", 5.0)

        # Scan all source categories
        for category, sources in self.sources.items():
            for source in sources:
                if not source.get("enabled", True):
                    results["sources_skipped"] += 1
                    continue

                if not force_all and not self._should_scan(source):
                    results["sources_skipped"] += 1
                    continue

                try:
                    items = self.fetch_source(source)
                    results["sources_scanned"] += 1

                    # Calculate scores and filter
                    for item in items:
                        item["hash"] = self._hash_item(item.get("title", ""), item.get("source", ""))
                        item["score"] = self._calculate_score(item, source)

                        if item["score"] >= threshold:
                            all_items.append(item)
                        else:
                            results["items_filtered"] += 1

                    # Update scan state
                    self.scan_state[source["name"]] = {
                        "last_scan": datetime.now().isoformat(),
                        "items_found": len(items)
                    }

                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    results["errors"].append(f"{source['name']}: {str(e)}")

        # Deduplicate and sort
        unique_items = []
        for item in sorted(all_items, key=lambda x: x["score"], reverse=True):
            h = item["hash"]
            if h not in self.seen_hashes:
                self.seen_hashes.add(h)
                unique_items.append(item)
                self.discovered_items.append(item)
                results["items_new"] += 1

        results["items_discovered"] = len(all_items)
        results["top_items"] = [
            {"title": i["title"][:60], "score": i["score"], "source": i["source"]}
            for i in unique_items[:20]
        ]
        results["completed_at"] = datetime.now().isoformat()

        self._save_state()

        return results

    def get_stats(self) -> Dict:
        """Get discovery statistics."""
        return {
            "total_sources": sum(len(s) for s in self.sources.values()),
            "total_discovered": len(self.discovered_items),
            "keywords_watched": len(self.keywords_to_watch),
            "last_discovery": self.scan_state.get("_last_run", "never"),
            "categories": list(self.sources.keys())
        }

    def get_recent_discoveries(self, limit: int = 50) -> List[Dict]:
        """Get recent discoveries."""
        return sorted(self.discovered_items[-limit:], key=lambda x: x.get("score", 0), reverse=True)


# Singleton
_engine: Optional[AutoDiscoveryEngine] = None


def get_discovery_engine() -> AutoDiscoveryEngine:
    """Get singleton discovery engine."""
    global _engine
    if _engine is None:
        _engine = AutoDiscoveryEngine()
    return _engine


def run_auto_discovery(force_all: bool = False) -> Dict:
    """Run auto-discovery from all sources."""
    engine = get_discovery_engine()
    result = engine.run_discovery(force_all=force_all)
    engine.scan_state["_last_run"] = datetime.now().isoformat()
    return result


def get_recent_discoveries(limit: int = 50) -> List[Dict]:
    """Get recent discoveries."""
    return get_discovery_engine().get_recent_discoveries(limit)


def get_discovery_stats() -> Dict:
    """Get discovery statistics."""
    return get_discovery_engine().get_stats()
