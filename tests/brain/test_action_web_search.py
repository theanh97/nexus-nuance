from src.brain.action_executor import ActionExecutor


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_web_search_parses_results(monkeypatch):
    html = """
    <html><body>
      <a class="result__a" href="https://example.com/1">Result One</a>
      <a class="result__a" href="https://example.com/2">Result Two</a>
    </body></html>
    """

    def _fake_urlopen(req, timeout=20):
        return _FakeResponse(html)

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    executor = ActionExecutor()
    output, data = executor._action_web_search({"query": "nexus"})
    assert "Search results for" in output
    assert data["status"] == "ok"
    assert len(data["results"]) == 2


def test_web_search_unavailable(monkeypatch):
    def _raise(req, timeout=20):
        raise RuntimeError("offline")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _raise)

    executor = ActionExecutor()
    output, data = executor._action_web_search({"query": "nexus"})
    assert "unavailable" in output.lower()
    assert data["status"] == "unavailable"
