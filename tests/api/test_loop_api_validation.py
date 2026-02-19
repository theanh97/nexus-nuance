"""Input validation tests for loop_api Pydantic models."""

import pytest
from pydantic import ValidationError

import src.api.loop_api as loop_api


class TestTaskCreateValidation:
    def test_valid_task(self):
        t = loop_api.TaskCreate(name="deploy", description="Deploy service", action="run_shell")
        assert t.priority == "MEDIUM"

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            loop_api.TaskCreate(name="", description="d", action="a")

    def test_rejects_oversized_description(self):
        with pytest.raises(ValidationError):
            loop_api.TaskCreate(name="n", description="x" * 5001, action="a")

    def test_rejects_invalid_priority(self):
        with pytest.raises(ValidationError):
            loop_api.TaskCreate(name="n", description="d", action="a", priority="URGENT")

    def test_accepts_valid_priorities(self):
        for p in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            t = loop_api.TaskCreate(name="n", description="d", action="a", priority=p)
            assert t.priority == p


class TestVerificationTaskValidation:
    def test_valid_url(self):
        t = loop_api.VerificationTask(target="https://example.com")
        assert t.task_type == "url"

    def test_valid_file(self):
        t = loop_api.VerificationTask(target="/tmp/test.py", task_type="file")
        assert t.task_type == "file"

    def test_rejects_empty_target(self):
        with pytest.raises(ValidationError):
            loop_api.VerificationTask(target="")

    def test_rejects_invalid_type(self):
        with pytest.raises(ValidationError):
            loop_api.VerificationTask(target="x", task_type="database")
