"""
API-Based Knowledge Sources
Fetches knowledge from APIs instead of web scraping (which gets blocked).

This module provides reliable knowledge acquisition from:
- ArXiv (AI/ML papers)
- GitHub (trending repos)
- Hacker News (tech discussions)
- Dev.to (articles)
"""

import json
import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import threading

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class APISource:
    """Represents an API-based knowledge source."""

    def __init__(
        self,
        name: str,
        url: str,
        category: str,
        priority: int = 7,
        params: Dict = None,
        headers: Dict = None,
        scan_frequency: str = "daily",
        enabled: bool = True
    ):
        self.name = name
        self.url = url
        self.category = category
        self.priority = priority
        self.params = params or {}
        self.headers = headers or {}
        self.scan_frequency = scan_frequency
        self.enabled = enabled
        self.last_scanned = None
        self.error_count = 0


class APIKnowledgeFetcher:
    """
    Fetches knowledge from various APIs.
    More reliable than web scraping.
    """

    def __init__(self, config_path: str = None):
        self._lock = threading.RLock()

        if config_path:
            self.config_path = Path(config_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.config_path = project_root / "data" / "config" / "api_sources.json"
            except:
                self.config_path = Path.cwd() / "data" / "config" / "api_sources.json"

        self.sources: List[APISource] = []
        self.thresholds = {
            "proposal_score": 5.0,
            "auto_approve_score": 6.0,
            "learning_value_min": 0.5,
            "filter_min_score": 5.5
        }

        self._load_config()

        # Session for connection pooling
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (compatible; NEXUS-Learning-Bot/1.0)',
                'Accept': 'application/json'
            })

    def _load_config(self):
        """Load API sources configuration."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)

                # Load sources
                for src in config.get("api_sources", []):
                    if src.get("enabled", True):
                        self.sources.append(APISource(
                            name=src["name"],
                            url=src["url"],
                            category=src.get("category", "general"),
                            priority=src.get("priority", 7),
                            params=src.get("params", {}),
                            headers=src.get("headers", {}),
                            scan_frequency=src.get("scan_frequency", "daily"),
                            enabled=True
                        ))

                # Load thresholds
                self.thresholds.update(config.get("thresholds", {}))

            except Exception as e:
                print(f"Warning: Could not load API sources config: {e}")

        # Add default sources if none loaded
        if not self.sources:
            self._add_default_sources()

    def _add_default_sources(self):
        """Add default API sources."""
        self.sources = [
            APISource(
                name="ArXiv AI Papers",
                url="http://export.arxiv.org/api/query",
                category="ai_ml",
                priority=9,
                params={
                    "search_query": "cat:cs.AI OR cat:cs.LG",
                    "max_results": 10,
                    "sortBy": "submittedDate"
                }
            ),
            APISource(
                name="GitHub Trending AI",
                url="https://api.github.com/search/repositories",
                category="tools",
                priority=8,
                params={
                    "q": "ai OR llm stars:>50",
                    "sort": "stars",
                    "per_page": 10
                }
            ),
            APISource(
                name="Hacker News",
                url="https://hacker-news.firebaseio.com/v0/topstories.json",
                category="strategy",
                priority=7
            )
        ]

    def fetch_arxiv(self, source: APISource) -> List[Dict]:
        """Fetch papers from ArXiv API."""
        items = []

        if not REQUESTS_AVAILABLE:
            return items

        try:
            params = source.params.copy()
            params["search_query"] = params.get("search_query", "cat:cs.AI")
            params["start"] = params.get("start", 0)
            params["max_results"] = params.get("max_results", 10)

            response = self.session.get(source.url, params=params, timeout=15)

            if response.status_code == 200:
                # Parse XML response (ArXiv returns XML)
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.content)

                # Define namespace
                ns = {'atom': 'http://www.w3.org/2005/Atom'}

                for entry in root.findall('atom:entry', ns):
                    title = entry.find('atom:title', ns)
                    summary = entry.find('atom:summary', ns)
                    link = entry.find('atom:id', ns)
                    authors = entry.findall('atom:author', ns)

                    if title is not None:
                        author_names = [a.find('atom:name', ns).text for a in authors if a.find('atom:name', ns) is not None]

                        items.append({
                            "title": title.text.strip() if title.text else "Unknown",
                            "description": (summary.text[:500] if summary is not None and summary.text else "")[:200],
                            "source": source.name,
                            "source_priority": source.priority,
                            "category": source.category,
                            "url": link.text if link is not None else "",
                            "authors": author_names[:3],
                            "applicable_to": ["ORION", "NOVA"] if "ai" in source.category else ["ORION"],
                            "potential_improvement": "New AI/ML techniques and methods",
                            "fetched_at": datetime.now().isoformat()
                        })

        except Exception as e:
            print(f"ArXiv fetch error: {e}")

        return items

    def fetch_github(self, source: APISource) -> List[Dict]:
        """Fetch trending repos from GitHub API."""
        items = []

        if not REQUESTS_AVAILABLE:
            return items

        try:
            headers = source.headers.copy()
            headers["Accept"] = "application/vnd.github.v3+json"

            response = self.session.get(source.url, params=source.params, headers=headers, timeout=15)

            if response.status_code == 200:
                data = response.json()

                for repo in data.get("items", [])[:10]:
                    items.append({
                        "title": repo.get("full_name", "unknown/repo"),
                        "description": repo.get("description", "")[:200],
                        "source": source.name,
                        "source_priority": source.priority,
                        "category": source.category,
                        "url": repo.get("html_url", ""),
                        "stars": repo.get("stargazers_count", 0),
                        "language": repo.get("language", "Unknown"),
                        "applicable_to": ["NOVA", "FLUX"] if repo.get("language") in ["Python", "JavaScript", "TypeScript"] else ["ORION"],
                        "potential_improvement": f"New tool/library: {repo.get('description', '')[:50]}",
                        "fetched_at": datetime.now().isoformat()
                    })

        except Exception as e:
            print(f"GitHub fetch error: {e}")

        return items

    def fetch_hackernews(self, source: APISource) -> List[Dict]:
        """Fetch top stories from Hacker News."""
        items = []

        if not REQUESTS_AVAILABLE:
            return items

        try:
            # Get top story IDs
            response = self.session.get(source.url, timeout=10)

            if response.status_code == 200:
                story_ids = response.json()[:10]  # Top 10 stories

                for story_id in story_ids:
                    try:
                        story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                        story_response = self.session.get(story_url, timeout=10)

                        if story_response.status_code == 200:
                            story = story_response.json()

                            if story and story.get("type") == "story":
                                items.append({
                                    "title": story.get("title", "Unknown"),
                                    "description": f"HN Score: {story.get('score', 0)}, Comments: {story.get('descendants', 0)}",
                                    "source": source.name,
                                    "source_priority": source.priority,
                                    "category": source.category,
                                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                                    "score": story.get("score", 0),
                                    "applicable_to": ["ORION"],
                                    "potential_improvement": "Industry trends and discussions",
                                    "fetched_at": datetime.now().isoformat()
                                })
                    except Exception:
                        continue

        except Exception as e:
            print(f"Hacker News fetch error: {e}")

        return items

    def fetch_source(self, source: APISource) -> List[Dict]:
        """Fetch items from a specific source."""
        if not source.enabled:
            return []

        if "arxiv" in source.url.lower():
            return self.fetch_arxiv(source)
        elif "github" in source.url.lower():
            return self.fetch_github(source)
        elif "hacker-news" in source.url.lower() or "firebaseio" in source.url.lower():
            return self.fetch_hackernews(source)
        else:
            # Generic JSON API fetch
            return self._fetch_generic(source)

    def _fetch_generic(self, source: APISource) -> List[Dict]:
        """Generic JSON API fetcher."""
        items = []

        if not REQUESTS_AVAILABLE:
            return items

        try:
            response = self.session.get(
                source.url,
                params=source.params,
                headers=source.headers,
                timeout=15
            )

            if response.status_code == 200:
                data = response.json()

                # Handle different response formats
                articles = []
                if isinstance(data, list):
                    articles = data
                elif isinstance(data, dict):
                    articles = data.get("articles", data.get("items", data.get("data", [])))

                for article in articles[:10]:
                    title = article.get("title", article.get("name", "Unknown"))
                    items.append({
                        "title": title,
                        "description": article.get("description", article.get("body", ""))[:200],
                        "source": source.name,
                        "source_priority": source.priority,
                        "category": source.category,
                        "url": article.get("url", article.get("link", "")),
                        "applicable_to": ["ORION", "NOVA"],
                        "potential_improvement": f"New knowledge: {title[:50]}",
                        "fetched_at": datetime.now().isoformat()
                    })

        except Exception as e:
            print(f"Generic fetch error for {source.name}: {e}")

        return items

    def fetch_all(self) -> List[Dict]:
        """Fetch from all enabled sources."""
        all_items = []

        with self._lock:
            for source in self.sources:
                if source.enabled:
                    try:
                        items = self.fetch_source(source)
                        all_items.extend(items)
                        source.last_scanned = datetime.now()
                        source.error_count = 0
                        time.sleep(0.5)  # Rate limiting
                    except Exception as e:
                        source.error_count += 1
                        print(f"Error fetching {source.name}: {e}")

        return all_items

    def calculate_score(self, item: Dict) -> float:
        """Calculate knowledge item score."""
        base_score = float(item.get("source_priority", 5))

        # Bonus for having description
        if item.get("description"):
            base_score += 0.5

        # Bonus for having URL
        if item.get("url"):
            base_score += 0.3

        # Category bonus
        category = item.get("category", "")
        if "ai" in category.lower() or "ml" in category.lower():
            base_score += 1.0

        # Cap at 10
        return min(base_score, 10.0)


# Singleton instance
_fetcher: Optional[APIKnowledgeFetcher] = None


def get_api_fetcher() -> APIKnowledgeFetcher:
    """Get singleton API fetcher instance."""
    global _fetcher
    if _fetcher is None:
        _fetcher = APIKnowledgeFetcher()
    return _fetcher


def fetch_knowledge_from_apis() -> List[Dict]:
    """Convenience function to fetch from all APIs."""
    return get_api_fetcher().fetch_all()
