from __future__ import annotations

from datetime import datetime

import pytest

from agentic_harness.core.state import Goal
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.worker import WorkerResult


class SequenceWorker:
    def __init__(self, results: list[WorkerResult]) -> None:
        self.results = list(results)

    def run(self, goal: Goal) -> WorkerResult:
        return self.results.pop(0)


class FailingWorker:
    def run(self, goal: Goal) -> WorkerResult:
        return WorkerResult(success=False, summary="retry", returncode=1)


class ExitingWorker:
    def run(self, goal: Goal) -> WorkerResult:
        raise SystemExit("simulated process exit")


def test_worker_run_records_a_redacted_receipt_without_raw_payload(tmp_path) -> None:
    summary_secret = "opaque-summary-secret-Z7Q4M9"
    artifact_secret = "sk-artifact-secret-Z7Q4M9"
    worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary=f"completed with api_key={summary_secret}",
                artifacts=[f"reports/{artifact_secret}.log"],
                stdout="raw stdout must not be retained",
                stderr="raw stderr must not be retained",
                returncode=0,
                outcome={"arbitrary": "worker-authored payload must not be retained"},
            )
        ]
    )
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)
    supervisor.start("record one safe attempt")

    goal = supervisor.continue_goal()

    history = goal.metadata["attempt_history"]
    assert isinstance(history, list)
    assert len(history) == 1
    attempt = history[0]
    assert set(attempt) == {
        "attempt",
        "worker_run_id",
        "at",
        "success",
        "returncode",
        "summary",
        "artifacts",
    }
    assert attempt["attempt"] == 1
    assert attempt["worker_run_id"] == goal.metadata["worker_run_id"]
    assert datetime.fromisoformat(attempt["at"])
    assert attempt["success"] is True
    assert attempt["returncode"] == 0
    assert attempt["summary"] == "completed with api_key=<redacted>"
    assert attempt["artifacts"] == ["reports/sk-<redacted>"]
    assert summary_secret not in str(attempt)
    assert artifact_secret not in str(attempt)

    reloaded = supervisor.status()
    assert reloaded is not None
    assert reloaded.metadata["attempt_history"] == history


def test_restart_preserves_history_and_next_run_gets_a_stable_number(tmp_path) -> None:
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=SequenceWorker(
            [
                WorkerResult(success=False, summary="first failure", returncode=7),
                WorkerResult(success=True, summary="second attempt", returncode=0),
            ]
        ),
    )
    supervisor.start("retry once")
    failed = supervisor.continue_goal()
    first_history = list(failed.metadata["attempt_history"])

    restarted = supervisor.restart()

    assert restarted.metadata["attempt_history"] == first_history

    finished = supervisor.continue_goal()
    history = finished.metadata["attempt_history"]

    assert [attempt["attempt"] for attempt in history] == [1, 2]
    assert [attempt["returncode"] for attempt in history] == [7, 0]
    assert history[0]["worker_run_id"] != history[1]["worker_run_id"]


def test_attempt_history_keeps_only_the_latest_100_runs(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    supervisor.start("keep bounded retry evidence")

    for attempt in range(101):
        if attempt:
            supervisor.restart()
        supervisor.continue_goal()

    goal = supervisor.status()
    assert goal is not None
    history = goal.metadata["attempt_history"]

    assert len(history) == 100
    assert history[0]["attempt"] == 2
    assert history[-1]["attempt"] == 101


def test_worker_attempt_is_durable_before_the_worker_can_exit_process(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path, worker=ExitingWorker())
    supervisor.start("preserve evidence before invoking the worker")

    with pytest.raises(SystemExit, match="simulated process exit"):
        supervisor.continue_goal()

    goal = supervisor.status()
    assert goal is not None
    assert goal.status.value == "in_progress"
    history = goal.metadata["attempt_history"]
    assert len(history) == 1
    assert history[0] == {
        "attempt": 1,
        "worker_run_id": goal.metadata["worker_run_id"],
        "at": history[0]["at"],
        "success": None,
        "returncode": None,
        "summary": "Worker attempt started.",
        "artifacts": [],
    }
    assert datetime.fromisoformat(history[0]["at"])
