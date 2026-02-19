"""Tests for budget projection and skill routing features."""

from src.core.model_router import ModelRouter


def test_budget_projection_returns_valid_dict():
    """Budget projection should return a well-formed dict."""
    router = ModelRouter()
    proj = router.get_budget_projection()

    assert isinstance(proj, dict)
    assert "total_spent" in proj
    assert "hourly_rate" in proj
    assert "projected_eod" in proj
    assert "daily_budget" in proj
    assert "remaining_budget" in proj
    assert "status" in proj
    assert proj["status"] in {"healthy", "caution", "over_budget"}
    assert "hours_elapsed" in proj
    assert "hours_remaining" in proj
    assert "model_breakdown" in proj


def test_budget_projection_values_sane():
    """Projected values should be non-negative and consistent."""
    router = ModelRouter()
    proj = router.get_budget_projection()

    assert proj["total_spent"] >= 0
    assert proj["hourly_rate"] >= 0
    assert proj["projected_eod"] >= 0
    assert proj["daily_budget"] >= 0
    assert proj["remaining_budget"] >= 0
    assert 0 <= proj["hours_elapsed"] <= 24
    assert 0 <= proj["hours_remaining"] <= 24


def test_budget_soft_limited_no_crash():
    """_budget_soft_limited should not crash with valid input."""
    router = ModelRouter()
    result = router._budget_soft_limited(1000)
    assert isinstance(result, bool)


def test_skill_tracker_recommendation():
    """SkillTracker.get_skill_recommendation should return valid dict."""
    from src.brain.nexus_brain import SkillTracker, NexusBrain

    class FakeBrain:
        def log(self, msg):
            pass

    tracker = SkillTracker(FakeBrain())
    rec = tracker.get_skill_recommendation("unknown_skill")

    assert isinstance(rec, dict)
    assert "recommendation" in rec
    assert "confidence" in rec
    assert "reason" in rec
    assert "suggested_approach" in rec
    assert rec["recommendation"] == "learn"
    assert rec["confidence"] == 0.0


def test_skill_tracker_after_recording():
    """After recording executions, recommendation should change."""
    from src.brain.nexus_brain import SkillTracker
    import tempfile
    from pathlib import Path

    class FakeBrain:
        def log(self, msg):
            pass

    tracker = SkillTracker(FakeBrain())
    # Use temp file to avoid polluting real data
    tracker.skills_file = Path(tempfile.mktemp(suffix=".json"))

    try:
        for _ in range(15):
            tracker.record_execution("test_skill", 100.0, True)

        rec = tracker.get_skill_recommendation("test_skill")
        assert rec["recommendation"] != "learn"
        assert rec["confidence"] > 0.0
    finally:
        if tracker.skills_file.exists():
            tracker.skills_file.unlink()


def test_skill_best_match():
    """get_best_skill_for_task should find matching skills."""
    from src.brain.nexus_brain import SkillTracker
    import tempfile
    from pathlib import Path

    class FakeBrain:
        def log(self, msg):
            pass

    tracker = SkillTracker(FakeBrain())
    tracker.skills_file = Path(tempfile.mktemp(suffix=".json"))

    try:
        for _ in range(5):
            tracker.record_execution("code_review", 200.0, True)
            tracker.record_execution("test_execution", 150.0, True)

        match = tracker.get_best_skill_for_task("review the code changes")
        assert match == "code_review"

        match2 = tracker.get_best_skill_for_task("execute tests")
        assert match2 == "test_execution"

        # No match
        match3 = tracker.get_best_skill_for_task("something completely different xyz")
        assert match3 is None
    finally:
        if tracker.skills_file.exists():
            tracker.skills_file.unlink()
