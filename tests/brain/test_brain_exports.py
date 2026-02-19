import src.brain as brain


def test_brain_learn_exports_are_not_ambiguous():
    assert callable(brain.brain_learn)
    assert callable(brain.hub_learn)
    assert callable(brain.learn)
    assert brain.learn is brain.hub_learn
