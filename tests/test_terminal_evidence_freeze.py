from __future__ import annotations

import sys
from pathlib import Path

from pytest import MonkeyPatch

from agentic_harness.cli import write_goal_report
from agentic_harness.core import artifacts as artifacts_module
from agentic_harness.core.review import DeterministicReviewer, command_passes
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.worker import WorkerResult


class FileWritingWorker:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def run(self, goal: Goal) -> WorkerResult:
        (self.project_dir / "during-run.txt").write_text(
            "worker change\n",
            encoding="utf-8",
        )
        return WorkerResult(success=True, summary="worker claimed completion")


class RetryingFileWorker:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.attempt = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.attempt += 1
        (self.project_dir / f"attempt-{self.attempt}.txt").write_text(
            f"attempt {self.attempt}\n",
            encoding="utf-8",
        )
        return WorkerResult(
            success=self.attempt == 2,
            summary=f"attempt {self.attempt}",
            returncode=0 if self.attempt == 2 else 1,
        )


def test_terminal_transition_freezes_changes_before_later_workspace_edits(
    tmp_path: Path,
) -> None:
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=FileWritingWorker(tmp_path),
        reviewer=DeterministicReviewer(
            [
                command_passes(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    cwd=tmp_path,
                )
            ]
        ),
    )
    supervisor.start("freeze the terminal evidence boundary")
    assert supervisor.continue_goal().status is GoalStatus.REVIEW

    done = supervisor.review()

    assert done.status is GoalStatus.DONE
    assert done.metadata["terminal_workspace_changes"] == {
        "total": 1,
        "entries": [{"status": "added", "path": "during-run.txt"}],
        "omitted": 0,
        "truncated": False,
    }

    (tmp_path / "after-terminal.txt").write_text("later edit\n", encoding="utf-8")
    reloaded = supervisor.status()

    assert reloaded is not None
    assert reloaded.metadata["terminal_workspace_changes"] == (
        done.metadata["terminal_workspace_changes"]
    )


def test_restart_recomputes_terminal_changes_across_all_attempts(tmp_path: Path) -> None:
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=RetryingFileWorker(tmp_path),
        reviewer=DeterministicReviewer(
            [
                command_passes(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    cwd=tmp_path,
                )
            ]
        ),
    )
    supervisor.start("include every attempt in final changed files")

    first = supervisor.continue_goal()
    assert first.status is GoalStatus.FAILED
    assert first.metadata["terminal_workspace_changes"]["entries"] == [
        {"status": "added", "path": "attempt-1.txt"}
    ]

    restarted = supervisor.restart()
    assert "terminal_workspace_changes" not in restarted.metadata
    assert supervisor.continue_goal().status is GoalStatus.REVIEW
    done = supervisor.review()

    assert done.metadata["terminal_workspace_changes"]["entries"] == [
        {"status": "added", "path": "attempt-1.txt"},
        {"status": "added", "path": "attempt-2.txt"},
    ]


def test_terminal_scan_failure_prevents_late_report_recapture(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    def unavailable_scan(*args: object, **kwargs: object) -> dict[str, object]:
        raise OSError("injected terminal workspace scan failure")

    monkeypatch.setattr(artifacts_module, "workspace_change_summary", unavailable_scan)
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=FileWritingWorker(tmp_path),
        reviewer=DeterministicReviewer(
            [
                command_passes(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    cwd=tmp_path,
                )
            ]
        ),
    )
    supervisor.start("freeze unavailable terminal evidence")
    assert supervisor.continue_goal().status is GoalStatus.REVIEW
    done = supervisor.review()
    assert done.status is GoalStatus.DONE

    (tmp_path / "after-terminal.txt").write_text("late edit\n", encoding="utf-8")
    reported, report_rel = write_goal_report(
        supervisor,
        tmp_path,
        done,
    )
    report = (tmp_path / report_rel).read_text(encoding="utf-8")

    assert reported.metadata["terminal_workspace_changes"] == {
        "total": 0,
        "entries": [],
        "omitted": 0,
        "truncated": True,
        "evidence_unavailable": True,
    }
    assert "Changed-file evidence: unavailable" in report
    assert "after-terminal.txt" not in report
