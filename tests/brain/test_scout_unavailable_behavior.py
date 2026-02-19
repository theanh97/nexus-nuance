import urllib.error

import src.brain.omniscient_scout as scout_mod


def test_scan_html_returns_unavailable_on_fetch_error(monkeypatch):
    scout = scout_mod.OmniscientScout()
    source = scout.sources["hacker_news"]

    def _raise(*args, **kwargs):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(scout_mod.urllib.request, "urlopen", _raise)
    findings = scout._scan_html(source)

    assert findings
    assert findings[0]["type"] == "unavailable"
    assert findings[0]["source"] == "hacker_news"
