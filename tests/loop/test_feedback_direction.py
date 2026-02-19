import asyncio

from src.loop.autonomous_loop import AutonomousLoop, TaskPriority


def test_feedback_records_approval_on_success(tmp_path):
    loop = AutonomousLoop(data_dir=str(tmp_path / "loop"))
    before = loop.feedback_manager.get_feedback_summary()
    task = loop.add_task(
        name="learn",
        description="learn from input",
        action="learn_from_input",
        params={"input_type": "test", "content": "ok", "value_score": 0.9},
        priority=TaskPriority.HIGH,
    )

    async def run():
        await loop.execute_task(task)
        await loop.verify_and_learn(task)

    asyncio.run(run())
    after = loop.feedback_manager.get_feedback_summary()

    assert task.result.get("success") is True
    assert after.get("approvals", 0) - before.get("approvals", 0) == 1
    assert after.get("denials", 0) - before.get("denials", 0) == 0
