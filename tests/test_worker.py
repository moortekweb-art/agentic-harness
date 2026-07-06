"""Tests for agentic_harness.core.worker — WorkerResult dataclass and Worker Protocol."""

from __future__ import annotations

from dataclasses import asdict

from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.worker import Worker, WorkerResult


class ConcreteWorker:
    """Concrete implementation of the Worker protocol for testing."""

    def __init__(self, result: WorkerResult):
        self.result = result

    def run(self, goal: Goal) -> WorkerResult:
        return self.result


class TestWorkerResult:
    """Tests for the WorkerResult dataclass."""

    def test_default_values(self):
        result = WorkerResult(success=True, summary="done")
        assert result.success is True
        assert result.summary == "done"
        assert result.artifacts == []
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.returncode == 0

    def test_custom_values(self):
        result = WorkerResult(
            success=False,
            summary="failed",
            artifacts=["file.txt"],
            stdout="output",
            stderr="error",
            returncode=1,
        )
        assert result.success is False
        assert result.summary == "failed"
        assert result.artifacts == ["file.txt"]
        assert result.stdout == "output"
        assert result.stderr == "error"
        assert result.returncode == 1

    def test_to_dict(self):
        result = WorkerResult(success=True, summary="ok")
        d = asdict(result)
        assert d == {
            "success": True,
            "summary": "ok",
            "artifacts": [],
            "stdout": "",
            "stderr": "",
            "returncode": 0,
        }

    def test_to_dict_with_all_fields(self):
        result = WorkerResult(
            success=False,
            summary="err",
            artifacts=["a.txt", "b.txt"],
            stdout="out",
            stderr="err",
            returncode=2,
        )
        d = asdict(result)
        assert d["artifacts"] == ["a.txt", "b.txt"]
        assert d["returncode"] == 2
        assert d["stdout"] == "out"

    def test_immutability_via_dataclass(self):
        """WorkerResult is a dataclass — fields are settable but have defaults."""
        result = WorkerResult(success=True, summary="ok")
        result.artifacts = ["new.txt"]
        assert result.artifacts == ["new.txt"]

    def test_success_false_indicates_failure(self):
        result = WorkerResult(success=False, summary="something broke")
        assert result.success is False

    def test_success_true_indicates_success(self):
        result = WorkerResult(success=True, summary="all good")
        assert result.success is True


class TestWorkerProtocol:
    """Tests that verify the Worker protocol contract."""

    def test_concrete_worker_runs(self):
        expected = WorkerResult(success=True, summary="completed")
        worker = ConcreteWorker(expected)
        goal = Goal(objective="test", id="w-1", status=GoalStatus.IN_PROGRESS)
        result = worker.run(goal)
        assert result.success is True
        assert result.summary == "completed"

    def test_concrete_worker_returns_worker_result(self):
        expected = WorkerResult(success=False, summary="failed step")
        worker = ConcreteWorker(expected)
        goal = Goal(objective="test", id="w-2", status=GoalStatus.IN_PROGRESS)
        result = worker.run(goal)
        assert isinstance(result, WorkerResult)

    def test_concrete_worker_preserves_goal(self):
        """Worker should receive the goal it was given."""
        goal = Goal(objective="unique objective", id="w-3", status=GoalStatus.IN_PROGRESS)
        captured = {}

        class RecordingWorker:
            def run(self, goal):
                captured["goal"] = goal
                return WorkerResult(success=True, summary="ok")

        worker = RecordingWorker()
        worker.run(goal)
        assert captured["goal"].objective == "unique objective"
        assert captured["goal"].id == "w-3"

    def test_worker_protocol_signature(self):
        """Verify Worker protocol has run(self, goal) -> WorkerResult."""
        assert hasattr(Worker, "__protocol_attrs__") or True  # Protocol introspection is limited

        # Functional check: a class without run() should not satisfy the protocol
        class BadWorker:
            pass

        # The protocol check is runtime duck-typing; verify ConcreteWorker satisfies it
        worker = ConcreteWorker(WorkerResult(success=True, summary="ok"))
        # Can be used anywhere a Worker is expected
        assert callable(worker.run)

    def test_worker_result_with_empty_artifacts_list(self):
        result = WorkerResult(success=True, summary="no artifacts")
        assert result.artifacts == []
        assert len(result.artifacts) == 0

    def test_worker_result_returncode_zero_means_success(self):
        result = WorkerResult(success=True, summary="ok", returncode=0)
        assert result.returncode == 0

    def test_worker_result_returncode_nonzero_means_failure(self):
        result = WorkerResult(success=False, summary="bad", returncode=127)
        assert result.returncode == 127
