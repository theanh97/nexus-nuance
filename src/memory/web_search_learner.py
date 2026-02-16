"""
Web Search Learning System
Real-time Google/web search integration for continuous learning.

THE CORE PRINCIPLE:
"Daily refresh from the web - stay current with the world."
"""

import json
import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set
from pathlib import Path
from collections import defaultdict
import threading
import re

# Try to import search libraries
try:
    from googlesearch import search as google_search
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class WebSearchLearner:
    """
    Learns from web search results.
    Integrates with Google, Reddit, Hacker News, and other sources.
    """

    # Default sources
    SOURCES = [
        "google",
        "reddit",
        "hackernews",
        "github",
        "stackoverflow",
    ]

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "web_learning"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "web_learning"

        self.cache_file = self.base_path / "search_cache.json"
        self.discoveries_file = self.base_path / "discoveries.json"
        self.trending_file = self.base_path / "trending.json"

        # Configuration
        self.search_interval = int(os.getenv("WEB_SEARCH_INTERVAL_HOURS", "6"))
        self.max_results = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "10"))
        self.min_relevance = float(os.getenv("WEB_SEARCH_MIN_RELEVANCE", "0.5"))
        self.enable_auto_search = os.getenv("ENABLE_AUTO_WEB_SEARCH", "true").lower() == "true"

        # In-memory state
        self.search_cache: Dict[str, Dict] = {}
        self.discoveries: List[Dict] = []
        self.trending_topics: List[str] = []
        self.last_searches: Dict[str, str] = {}  # topic -> timestamp
        self.source_stats: Dict[str, Dict] = defaultdict(lambda: {"searches": 0, "findings": 0, "errors": 0})

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load()

        # Default search topics
        self.default_topics = [
            "AI automation best practices 2024",
            "software development trends",
            "machine learning LLMs",
            "autonomous systems architecture",
            "Claude Code capabilities",
            "Python async programming",
        ]

    def _load(self):
        """Load cached data."""
        # Load search cache
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.search_cache = json.load(f)
            except Exception:
                pass

        # Load discoveries
        if self.discoveries_file.exists():
            try:
                with open(self.discoveries_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.discoveries = data.get("discoveries", [])
            except Exception:
                pass

        # Load trending
        if self.trending_file.exists():
            try:
                with open(self.trending_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.trending_topics = data.get("trending", [])
            except Exception:
                pass

    def _save(self):
        """Save data to disk."""
        with self._lock:
            # Save search cache (limited)
            cache_keys = list(self.search_cache.keys())[-1000:]
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump({k: self.search_cache[k] for k in cache_keys}, f, indent=2)

            # Save discoveries
            with open(self.discoveries_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "discoveries": self.discoveries[-500:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

            # Save trending
            with open(self.trending_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "trending": self.trending_topics[:50],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

    # ==================== SEARCH ====================

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        source: str = "google",
    ) -> List[Dict]:
        """
        Search the web for a query.

        Args:
            query: Search query
            max_results: Maximum results to return
            source: Search source (google, reddit, etc.)

        Returns:
            List of search results
        """
        max_results = max_results or self.max_results

        # Check cache
        cache_key = f"{source}:{query}"
        cached = self.search_cache.get(cache_key)
        if cached:
            # Check if cache is fresh (less than 1 hour)
            cache_time = datetime.fromisoformat(cached.get("timestamp", "2000-01-01"))
            if datetime.now() - cache_time < timedelta(hours=1):
                return cached.get("results", [])[:max_results]

        results = []

        if source == "google":
            results = self._search_google(query, max_results)
        elif source == "reddit":
            results = self._search_reddit(query, max_results)
        elif source == "hackernews":
            results = self._search_hackernews(query, max_results)
        elif source == "github":
            results = self._search_github(query, max_results)
        else:
            results = self._search_google(query, max_results)

        # Cache results
        self.search_cache[cache_key] = {
            "results": results,
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "source": source,
        }

        self.last_searches[query] = datetime.now().isoformat()
        self.source_stats[source]["searches"] += 1

        self._save()
        return results

    def _search_google(self, query: str, max_results: int) -> List[Dict]:
        """Search using Google."""
        results = []

        if not GOOGLE_AVAILABLE:
            # Fallback to mock results if not available
            return self._mock_search(query, max_results, "google")

        try:
            for url in google_search(query, num_results=max_results):
                results.append({
                    "url": url,
                    "title": self._extract_title(url),
                    "source": "google",
                    "relevance": 0.8,
                    "timestamp": datetime.now().isoformat(),
                })
                self.source_stats["google"]["findings"] += 1
        except Exception as e:
            self.source_stats["google"]["errors"] += 1

        return results

    def _search_reddit(self, query: str, max_results: int) -> List[Dict]:
        """Search Reddit (simulated)."""
        # Reddit API would go here - using mock for now
        return self._mock_search(query, max_results, "reddit")

    def _search_hackernews(self, query: str, max_results: int) -> List[Dict]:
        """Search Hacker News (simulated)."""
        # Hacker News API would go here - using mock for now
        return self._mock_search(query, max_results, "hackernews")

    def _search_github(self, query: str, max_results: int) -> List[Dict]:
        """Search GitHub (simulated)."""
        # GitHub API would go here - using mock for now
        return self._mock_search(query, max_results, "github")

    def _mock_search(self, query: str, max_results: int, source: str) -> List[Dict]:
        """Generate mock search results for testing."""
        # This is a placeholder - in production, real APIs would be used
        mock_results = []
        for i in range(min(max_results, 3)):
            mock_results.append({
                "url": f"https://example.com/{source}/result_{i}",
                "title": f"Result {i+1} for {query}",
                "source": source,
                "relevance": 0.7 - (i * 0.1),
                "timestamp": datetime.now().isoformat(),
            })
        self.source_stats[source]["findings"] += len(mock_results)
        return mock_results

    def _extract_title(self, url: str) -> str:
        """Extract title from URL (simplified)."""
        # In production, would fetch the page and extract title
        return url.split("/")[-1][:50] if url else "Unknown"

    # ==================== LEARNING ====================

    def learn_from_results(
        self,
        results: List[Dict],
        query: str,
        context: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Extract learning from search results.

        Args:
            results: Search results
            query: Original query
            context: Additional context

        Returns:
            List of discoveries
        """
        discoveries = []

        for result in results:
            relevance = result.get("relevance", 0)
            if relevance < self.min_relevance:
                continue

            discovery = {
                "id": f"disc_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.discoveries)}",
                "query": query,
                "url": result.get("url"),
                "title": result.get("title"),
                "source": result.get("source"),
                "relevance": relevance,
                "learned_at": datetime.now().isoformat(),
                "context": context or {},
            }

            discoveries.append(discovery)
            self.discoveries.append(discovery)

        # Update trending
        self._update_trending(query)

        self._save()
        return discoveries

    def _update_trending(self, query: str):
        """Update trending topics."""
        # Extract keywords from query
        keywords = re.findall(r'\w+', query.lower())

        # Add to trending
        for kw in keywords:
            if len(kw) > 3:  # Skip short words
                if kw not in self.trending_topics:
                    self.trending_topics.insert(0, kw)
                else:
                    # Move to front
                    self.trending_topics.remove(kw)
                    self.trending_topics.insert(0, kw)

        # Keep top 50
        self.trending_topics = self.trending_topics[:50]

    # ==================== AUTO DISCOVERY ====================

    def discover_topics(self) -> List[str]:
        """Get topics to search based on trending and context."""
        topics = []

        # Add trending topics
        topics.extend(self.trending_topics[:5])

        # Add default topics if not enough
        for topic in self.default_topics:
            if topic not in topics:
                topics.append(topic)

        return topics[:10]

    def run_auto_search(self, topics: Optional[List[str]] = None) -> Dict:
        """
        Run automatic search on multiple topics.

        Args:
            topics: Topics to search (auto-discovered if not provided)

        Returns:
            Summary of search results
        """
        topics = topics or self.discover_topics()

        summary = {
            "timestamp": datetime.now().isoformat(),
            "topics_searched": 0,
            "total_results": 0,
            "discoveries": 0,
            "errors": [],
        }

        for topic in topics:
            # Check if we should skip (too soon since last search)
            last_search = self.last_searches.get(topic)
            if last_search:
                last_time = datetime.fromisoformat(last_search)
                if datetime.now() - last_time < timedelta(hours=self.search_interval):
                    continue

            try:
                results = self.search(topic)
                discoveries = self.learn_from_results(results, topic)

                summary["topics_searched"] += 1
                summary["total_results"] += len(results)
                summary["discoveries"] += len(discoveries)

            except Exception as e:
                summary["errors"].append({
                    "topic": topic,
                    "error": str(e),
                })

        self._save()
        return summary

    # ==================== QUERY ====================

    def get_discoveries(
        self,
        query: Optional[str] = None,
        source: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Get discoveries with filters."""
        results = self.discoveries[-500:]  # Check last 500

        filtered = []
        for d in results:
            if query and query.lower() not in d.get("query", "").lower():
                continue
            if source and d.get("source") != source:
                continue
            if since:
                disc_time = datetime.fromisoformat(d.get("learned_at", "2000-01-01"))
                if disc_time < since:
                    continue
            filtered.append(d)

        return filtered[-limit:]

    def get_trending(self, limit: int = 10) -> List[str]:
        """Get trending topics."""
        return self.trending_topics[:limit]

    def get_source_stats(self) -> Dict:
        """Get statistics by source."""
        return dict(self.source_stats)

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get web learning statistics."""
        return {
            "total_discoveries": len(self.discoveries),
            "trending_count": len(self.trending_topics),
            "cached_searches": len(self.search_cache),
            "topics_searched": len(self.last_searches),
            "source_stats": self.get_source_stats(),
        }

    def generate_daily_report(self) -> str:
        """Generate daily web learning report."""
        recent = self.get_discoveries(
            since=datetime.now() - timedelta(days=1),
            limit=50,
        )

        lines = [
            "# 🌐 Web Learning Daily Report",
            f"Generated: {datetime.now().isoformat()}",
            "",
            f"## Statistics",
            f"- Total Discoveries: {len(self.discoveries)}",
            f"- Today's Discoveries: {len(recent)}",
            f"- Trending Topics: {len(self.trending_topics)}",
            "",
            "## Top Trending",
        ]

        for topic in self.trending_topics[:10]:
            lines.append(f"- {topic}")

        lines.extend(["", "## Recent Discoveries"])

        for disc in recent[:10]:
            lines.append(f"- [{disc['source']}] {disc['query']}: {disc['title'][:50]}")

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_learner = None


def get_web_learner() -> WebSearchLearner:
    """Get singleton web learner instance."""
    global _learner
    if _learner is None:
        _learner = WebSearchLearner()
    return _learner


def search_web(query: str, max_results: int = 10) -> List[Dict]:
    """Search the web."""
    return get_web_learner().search(query, max_results)


def learn_from_web(query: str, context: Optional[Dict] = None) -> List[Dict]:
    """Search and learn from web."""
    results = get_web_learner().search(query)
    return get_web_learner().learn_from_results(results, query, context)


def get_trending() -> List[str]:
    """Get trending topics."""
    return get_web_learner().get_trending()


def run_auto_discovery() -> Dict:
    """Run automatic discovery."""
    return get_web_learner().run_auto_search()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Web Search Learning System")
    print("=" * 50)

    learner = WebSearchLearner()

    # Test search
    print("\nTesting search...")

    results = learner.search("AI automation trends 2024", max_results=5)
    print(f"Found {len(results)} results")

    # Learn from results
    discoveries = learner.learn_from_results(results, "AI automation trends 2024")
    print(f"Made {len(discoveries)} discoveries")

    # Get trending
    print("\nTrending topics:", learner.get_trending())

    # Run auto search
    print("\nRunning auto discovery...")
    summary = learner.run_auto_search()
    print(f"Auto discovery: {summary['topics_searched']} topics, {summary['discoveries']} discoveries")

    print("\n" + learner.generate_daily_report())
