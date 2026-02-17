from src.core.model_router import ModelRouter, TaskType, TaskComplexity


def test_model_router_reorders_by_performance(monkeypatch):
    router = ModelRouter()
    monkeypatch.setattr(
        router,
        "_base_chain",
        lambda task_type, complexity, importance: ["glm-5", "glm-4.7", "glm-4-flash"],
    )
    monkeypatch.setattr(
        router,
        "is_runtime_callable",
        lambda cfg: True,
    )
    monkeypatch.setattr(
        router,
        "_model_performance_scores",
        lambda task_type: {"glm-4-flash": 0.9, "glm-5": 0.2},
    )
    chain = router.get_fallback_chain(
        task_type=TaskType.TEST_GENERATION,
        complexity=TaskComplexity.SIMPLE,
        importance="normal",
        runtime_only=True,
    )
    assert chain
    assert chain[0].name == "glm-4-flash"
