from __future__ import annotations

import json
import subprocess

from agentic_harness.adapters.github_actions import GitHubActionsAdapter
from agentic_harness.adapters.local_llm import LocalLLMAdapter
from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.adapters.tmux import TmuxWorker
from agentic_harness.core.state import Goal


def test_shell_worker_returns_structured_result(tmp_path) -> None:
    worker = ShellWorker(
        ["python", "-c", "import os; print(os.environ['AGENTIC_HARNESS_OBJECTIVE'])"],
        cwd=tmp_path,
    )

    result = worker.run(Goal("run shell objective"))

    assert result.success is True
    assert result.summary == "run shell objective"
    assert result.returncode == 0


def test_shell_worker_reports_failure(tmp_path) -> None:
    worker = ShellWorker(["python", "-c", "import sys; print('bad'); sys.exit(3)"], cwd=tmp_path)

    result = worker.run(Goal("run shell"))

    assert result.success is False
    assert result.summary == "bad"
    assert result.returncode == 3


def test_tmux_worker_builds_project_local_session_command(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("implement feature", id="abcdef1234567890")
    worker = TmuxWorker("echo {goal_id}: {objective}", cwd=tmp_path)

    result = worker.run(goal)

    assert result.success is True
    assert calls[0][0] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "agentic-harness-abcdef123456",
        "echo abcdef1234567890: implement feature",
    ]
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_github_actions_adapter_builds_dispatch_payload() -> None:
    goal = Goal("ship", id="goal-1")
    adapter = GitHubActionsAdapter("owner", "repo", "workflow.yml", ref="dev")

    assert adapter.dispatch_url() == (
        "https://api.github.com/repos/owner/repo/actions/workflows/workflow.yml/dispatches"
    )
    assert adapter.dispatch_payload(goal) == {
        "ref": "dev",
        "inputs": {"goal_id": "goal-1", "objective": "ship"},
    }
    assert adapter.run(goal).success is False


def test_github_actions_adapter_reports_dispatch_only_success(monkeypatch) -> None:
    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = GitHubActionsAdapter("owner", "repo", "workflow.yml", token="token")

    result = adapter.run(Goal("ship"))

    assert result.success is True
    assert "dispatch accepted" in result.summary
    assert "workflow completion not verified" in result.summary


def test_github_actions_adapter_can_wait_for_completed_workflow(monkeypatch) -> None:
    calls = []

    class Response:
        def __init__(self, status: int, payload: dict[str, object] | None = None) -> None:
            self.status = status
            self._payload = payload or {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if request.get_method() == "POST":
            return Response(204)
        return Response(
            200,
            {
                "workflow_runs": [
                    {
                        "id": 123,
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.com/owner/repo/actions/runs/123",
                    }
                ]
            },
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = GitHubActionsAdapter(
        "owner",
        "repo",
        "workflow.yml",
        token="token",
        wait_for_completion=True,
        poll_interval=0,
    )

    result = adapter.run(Goal("ship"))

    assert result.success is True
    assert result.summary == "GitHub Actions workflow completed: success"
    assert result.artifacts == ["https://github.com/owner/repo/actions/runs/123"]
    assert any("/runs" in url for url in calls)
    runs_url = next(url for url in calls if "/runs" in url)
    assert "event=workflow_dispatch" in runs_url
    assert "created=%3E%3D" in runs_url


def test_github_actions_wait_does_not_report_older_completed_run(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "workflow_runs": [
                        {"id": 124, "status": "in_progress", "conclusion": None},
                        {
                            "id": 123,
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/owner/repo/actions/runs/123",
                        },
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))
    adapter = GitHubActionsAdapter(
        "owner",
        "repo",
        "workflow.yml",
        token="token",
        wait_for_completion=True,
        poll_interval=0,
        timeout=0,
    )

    result = adapter._wait_for_completion()

    assert result.success is False
    assert result.returncode == 124
    assert result.artifacts == []


def test_local_llm_adapter_builds_openai_compatible_payload() -> None:
    goal = Goal("do work")
    adapter = LocalLLMAdapter("http://127.0.0.1:4000/v1/chat/completions", "local-model")

    payload = adapter.request_payload(goal)

    assert payload["model"] == "local-model"
    assert payload["messages"][1] == {"role": "user", "content": "do work"}
    assert payload["stream"] is False
