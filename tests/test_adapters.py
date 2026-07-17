from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error

import pytest

from agentic_harness.adapters.coding_agent import CodingAgentWorker
from agentic_harness.core.events import TaskEventStore
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
    goal = Goal("run shell objective")

    result = worker.run(goal)

    assert result.success is True
    assert result.summary == "run shell objective"
    assert result.returncode == 0
    assert result.artifacts == [f".agentic-harness/runs/{goal.id}/shell-worker.log"]


def test_shell_worker_writes_transcript(tmp_path) -> None:
    worker = ShellWorker(
        ["python", "-c", "print('worker stdout')"],
        cwd=tmp_path,
    )
    goal = Goal("run shell", id="goal-123")

    result = worker.run(goal)

    transcript = tmp_path / ".agentic-harness" / "runs" / "goal-123" / "shell-worker.log"
    assert result.success is True
    assert result.artifacts == [".agentic-harness/runs/goal-123/shell-worker.log"]
    assert "worker stdout" in transcript.read_text(encoding="utf-8")


def test_shell_worker_redacts_secret_like_transcript_content(tmp_path) -> None:
    worker = ShellWorker(
        [
            "python",
            "-c",
            "print('API_KEY=super-secret-value'); print('Bearer abcdefghijklmnop')",
        ],
        cwd=tmp_path,
    )
    goal = Goal("run shell", id="goal-redact")

    result = worker.run(goal)

    transcript = tmp_path / ".agentic-harness" / "runs" / "goal-redact" / "shell-worker.log"
    text = transcript.read_text(encoding="utf-8")
    assert result.success is True
    assert "super-secret-value" not in text
    assert "abcdefghijklmnop" not in text
    assert "API_KEY=<redacted>" in text
    assert "Bearer <redacted>" in text


def test_shell_worker_reports_failure(tmp_path) -> None:
    worker = ShellWorker(["python", "-c", "import sys; print('bad'); sys.exit(3)"], cwd=tmp_path)

    result = worker.run(Goal("run shell"))

    assert result.success is False
    assert result.summary == "bad"
    assert result.returncode == 3


def test_shell_worker_reports_timeout(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["slow"], timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = ShellWorker(["slow"], cwd=tmp_path, timeout=5)

    result = worker.run(Goal("run too long"))

    assert result.success is False
    assert result.returncode == 124
    assert "timed out after 5s" in result.summary


def test_shell_worker_reports_missing_executable(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("missing-tool")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = ShellWorker(["missing-tool"], cwd=tmp_path)

    result = worker.run(Goal("run missing tool"))

    assert result.success is False
    assert result.returncode == 127
    assert "missing-tool" in result.summary


def test_coding_agent_worker_formats_command_and_writes_transcript(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "fixed tests\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(
        ["codex", "exec", "--full-auto", "{objective}", "--id", "{goal_id}"],
        cwd=tmp_path,
        transcript_path=".agentic-harness/runs/{goal_id}/coding-agent.log",
    )
    goal = Goal("fix failing tests", id="goal-123")

    result = worker.run(goal)

    transcript = tmp_path / ".agentic-harness" / "runs" / "goal-123" / "coding-agent.log"
    assert result.success is True
    assert result.summary == "fixed tests"
    assert result.artifacts == [".agentic-harness/runs/goal-123/coding-agent.log"]
    assert transcript.read_text(encoding="utf-8") == (
        "$ codex exec --full-auto fix failing tests --id goal-123\n"
        "\n"
        "[stdout]\n"
        "fixed tests\n"
        "\n"
        "[stderr]\n"
    )
    assert calls[0][0] == [
        "codex",
        "exec",
        "--full-auto",
        "fix failing tests",
        "--id",
        "goal-123",
    ]
    assert calls[0][1]["cwd"] == str(tmp_path)
    events = TaskEventStore(tmp_path, goal.id).read()
    assert [event["tool"]["status"] for event in events] == ["started", "completed"]
    assert all(event["tool"]["name"] == "coding_agent" for event in events)


def test_coding_agent_worker_launches_the_resolved_executable(
    monkeypatch,
    tmp_path,
) -> None:
    resolved = r"C:\Tools\codex.cmd"
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "agentic_harness.adapters.coding_agent.resolve_command_executable",
        lambda command: [resolved, *command[1:]],
    )

    def fake_run(command: list[str], **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "done\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CodingAgentWorker(
        ["codex", "exec", "{objective}"],
        cwd=tmp_path,
    ).run(Goal("finish safely"))

    assert result.success is True
    assert calls == [[resolved, "exec", "finish safely"]]


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows cmd.exe semantics")
def test_coding_agent_windows_cmd_shim_does_not_execute_objective_metacharacters(
    tmp_path,
) -> None:
    receiver = tmp_path / "receiver.py"
    received = tmp_path / "received.txt"
    injected = tmp_path / "injected.txt"
    receiver.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(received)!r}).write_text(sys.argv[1], encoding='utf-8')\n"
        "print('worker complete')\n",
        encoding="utf-8",
    )
    shim = tmp_path / "coding-agent.cmd"
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" "{receiver}" %*\r\n',
        encoding="utf-8",
    )
    objective = f"fix&echo injected>{injected}"

    result = CodingAgentWorker([str(shim), "{objective}"], cwd=tmp_path).run(
        Goal(objective)
    )

    assert result.success is True
    assert received.read_text(encoding="utf-8") == objective
    assert not injected.exists()


def test_coding_agent_worker_uses_durable_continuation_instruction_for_autonomy(
    monkeypatch, tmp_path
) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "checkpoint saved\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec", "{objective}"], cwd=tmp_path)
    goal = Goal("the immutable full objective", id="goal-autonomy")
    goal.metadata["autonomy"] = {"strict_completion": True}
    goal.metadata["continuation_instruction"] = (
        "Preserve the immutable full objective. Continue from checkpoint two. "
        "Return HARNESS_RESULT_JSON."
    )

    worker.run(goal)

    assert calls[0][0][-1] == goal.metadata["continuation_instruction"]
    assert calls[0][1]["env"]["AGENTIC_HARNESS_OBJECTIVE"] == goal.objective
    assert (
        calls[0][1]["env"]["AGENTIC_HARNESS_INSTRUCTION"]
        == goal.metadata["continuation_instruction"]
    )


def test_coding_agent_worker_extracts_structured_harness_outcome(monkeypatch, tmp_path) -> None:
    outcome = {
        "status": "complete",
        "summary": "implemented and verified",
        "requirement_status": [
            {
                "id": "tests",
                "status": "satisfied",
                "evidence": ["review:1"],
            }
        ],
        "blockers": [],
    }

    def fake_run(cmd, **kwargs):
        stdout = "work log\nHARNESS_RESULT_JSON=" + json.dumps(outcome) + "\n"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec", "{objective}"], cwd=tmp_path)

    result = worker.run(Goal("structured completion", id="goal-structured"))

    assert result.success is True
    assert result.outcome == outcome


def test_coding_agent_worker_extracts_json_wrapped_harness_outcome(
    monkeypatch, tmp_path
) -> None:
    outcome = {
        "status": "completed",
        "summary": "implemented and verified",
        "requirement_status": [],
        "blockers": [],
    }

    def fake_run(cmd, **kwargs):
        stdout = json.dumps({"HARNESS_RESULT_JSON": outcome}, indent=2) + "\n"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec", "{objective}"], cwd=tmp_path)

    result = worker.run(Goal("wrapped completion", id="goal-wrapped"))

    assert result.success is True
    assert result.outcome == outcome
    assert result.summary == "implemented and verified"


def test_coding_agent_worker_extracts_multiline_marker_and_skips_malformed_latest(
    monkeypatch, tmp_path
) -> None:
    outcome = {
        "status": "complete",
        "summary": "valid earlier result",
        "requirement_status": [],
        "blockers": [],
    }

    def fake_run(cmd, **kwargs):
        stdout = (
            "HARNESS_RESULT_JSON="
            + json.dumps(outcome, indent=2)
            + "\nHARNESS_RESULT_JSON={broken\n"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec", "{objective}"], cwd=tmp_path)

    result = worker.run(Goal("multiline completion", id="goal-multiline"))

    assert result.outcome == outcome
    assert result.summary == "valid earlier result"


def test_coding_agent_worker_redacts_secret_like_transcript_content(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            "fixed with token=super-secret-value\n",
            "Authorization: Bearer abcdefghijklmnop\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec", "{objective}"], cwd=tmp_path)
    goal = Goal("fix with API_KEY=objective-secret-12345", id="goal-redact")

    result = worker.run(goal)

    transcript = tmp_path / ".agentic-harness" / "runs" / "goal-redact" / "coding-agent.log"
    text = transcript.read_text(encoding="utf-8")
    assert result.success is True
    assert "super-secret-value" not in text
    assert "objective-secret-12345" not in text
    assert "abcdefghijklmnop" not in text
    assert "token=<redacted>" in text
    assert "API_KEY=<redacted>" in text
    assert "Bearer <redacted>" in text


def test_coding_agent_worker_rejects_transcript_path_escape(tmp_path) -> None:
    worker = CodingAgentWorker(
        ["python", "-c", "print('ok')"],
        cwd=tmp_path,
        transcript_path="../outside.log",
    )

    try:
        worker.transcript_for(Goal("escape"))
    except ValueError as exc:
        assert "outside project directory" in str(exc)
    else:
        raise AssertionError("expected transcript path escape to fail")


def test_coding_agent_worker_reports_timeout(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=9)

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec"], cwd=tmp_path, timeout=9)

    result = worker.run(Goal("slow coding agent"))

    assert result.success is False
    assert result.returncode == 124
    assert "timed out after 9s" in result.summary


def test_coding_agent_worker_reports_missing_executable(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodingAgentWorker(["codex", "exec"], cwd=tmp_path)

    result = worker.run(Goal("missing coding agent"))

    assert result.success is False
    assert result.returncode == 127
    assert "codex could not start" in result.summary


def test_coding_agent_worker_reports_transcript_write_error(monkeypatch, tmp_path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "fixed\n", "")

    def fake_write_text(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "agentic_harness.adapters.coding_agent.write_private_text",
        fake_write_text,
    )
    worker = CodingAgentWorker(["codex", "exec"], cwd=tmp_path)

    result = worker.run(Goal("write transcript"))

    # Transcript write failure is a logging issue, not a work failure.
    assert result.success is True
    assert result.returncode == 0
    assert "transcript write failed" in result.summary
    assert "disk full" in result.stderr


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
        "echo abcdef1234567890: 'implement feature'",
    ]
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_tmux_worker_shell_quotes_objective() -> None:
    goal = Goal("bad'; touch owned #", id="abcdef1234567890")
    worker = TmuxWorker("python worker.py --goal {goal_id} --objective {objective}")

    command = worker.command_for(goal)

    assert command == (
        "python worker.py --goal abcdef1234567890 --objective 'bad'\"'\"'; touch owned #'"
    )


def test_tmux_worker_reports_missing_tmux(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = TmuxWorker("echo {objective}", cwd=tmp_path)

    result = worker.run(Goal("start tmux"))

    assert result.success is False
    assert result.returncode == 127
    assert "tmux could not start" in result.summary


def test_tmux_worker_reports_exit_code_in_failure_summary(monkeypatch, tmp_path) -> None:
    """When tmux fails with a non-zero exit code, the summary should include the exit code."""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["tmux", "new-session"],
            returncode=1,
            stdout="",
            stderr="session already exists",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = TmuxWorker("echo {objective}", cwd=tmp_path)

    result = worker.run(Goal("tmux failure", id="test-goal-1234567890"))

    assert result.success is False
    assert result.returncode == 1
    assert "exit 1" in result.summary
    assert "agentic-harness-test-goal-12" in result.summary


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


def test_github_actions_adapter_requests_run_details_when_waiting() -> None:
    goal = Goal("ship", id="goal-1")
    adapter = GitHubActionsAdapter(
        "owner",
        "repo",
        "workflow.yml",
        ref="dev",
        wait_for_completion=True,
    )

    assert adapter.dispatch_payload(goal) == {
        "ref": "dev",
        "inputs": {"goal_id": "goal-1", "objective": "ship"},
        "return_run_details": True,
    }


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

    result = adapter.run(Goal("ship", id="goal-456"))

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

    result = adapter.run(Goal("ship", id="goal-456"))

    assert result.success is True
    assert result.summary == "GitHub Actions workflow completed: success"
    assert result.artifacts == ["https://github.com/owner/repo/actions/runs/123"]
    assert any("/runs" in url for url in calls)
    runs_url = next(url for url in calls if "/runs" in url)
    assert "event=workflow_dispatch" in runs_url
    assert "created=%3E%3D" in runs_url


def test_github_actions_adapter_waits_on_returned_run_url(monkeypatch) -> None:
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
        calls.append((request.get_method(), request.full_url, dict(request.headers)))
        if request.get_method() == "POST":
            payloads.append(json.loads(request.data.decode("utf-8")))
        if request.get_method() == "POST":
            return Response(
                200,
                {
                    "workflow_run_id": 456,
                    "run_url": "https://api.github.com/repos/owner/repo/actions/runs/456",
                    "html_url": "https://github.com/owner/repo/actions/runs/456",
                },
            )
        return Response(
            200,
            {
                "id": 456,
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/owner/repo/actions/runs/456",
            },
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    payloads = []
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

    result = adapter.run(Goal("ship", id="goal-456"))

    assert result.success is True
    assert result.summary == "GitHub Actions workflow completed: success"
    assert result.artifacts == ["https://github.com/owner/repo/actions/runs/456"]
    assert payloads == [
        {
            "ref": "main",
            "inputs": {"goal_id": "goal-456", "objective": "ship"},
            "return_run_details": True,
        }
    ]
    assert ("GET", "https://api.github.com/repos/owner/repo/actions/runs/456") == calls[1][:2]
    assert all("/workflows/workflow.yml/runs" not in url for _, url, _ in calls)
    assert calls[0][2]["X-github-api-version"] == "2026-03-10"


def test_github_actions_adapter_builds_run_url_from_returned_run_id(monkeypatch) -> None:
    calls = []

    class Response:
        def __init__(self, status: int, payload: dict[str, object]) -> None:
            self.status = status
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if request.get_method() == "POST":
            return Response(200, {"workflow_run_id": 789})
        return Response(
            200,
            {
                "id": 789,
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/owner/repo/actions/runs/789",
            },
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
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

    result = adapter.run(Goal("ship"))

    assert result.success is True
    assert calls[1] == "https://api.github.com/repos/owner/repo/actions/runs/789"


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


def test_github_actions_wait_respects_deadline_with_large_poll_interval(monkeypatch) -> None:
    """Poll interval should not exceed remaining time until deadline."""

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("time.sleep", fake_sleep)

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
                        {"id": 123, "status": "in_progress", "conclusion": None},
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # Use a large poll interval (300s) but small timeout (10s)
    adapter = GitHubActionsAdapter(
        "owner",
        "repo",
        "workflow.yml",
        token="token",
        wait_for_completion=True,
        poll_interval=300,
        timeout=10,
    )

    # Mock time.monotonic to simulate time passing
    call_count = [0]

    def fake_monotonic():
        call_count[0] += 1
        # First call returns 0, then increment by 1 each time
        return call_count[0] - 1

    monkeypatch.setattr("time.monotonic", fake_monotonic)

    result = adapter._wait_for_completion()

    assert result.success is False
    assert result.returncode == 124
    # Sleep calls should not exceed remaining time
    for sleep_time in sleep_calls:
        assert sleep_time <= 10.0, f"Sleep time {sleep_time} exceeds timeout"


def test_local_llm_adapter_builds_openai_compatible_payload() -> None:
    goal = Goal("do work")
    adapter = LocalLLMAdapter("http://127.0.0.1:4000/v1/chat/completions", "local-model")

    payload = adapter.request_payload(goal)

    assert payload["model"] == "local-model"
    assert payload["messages"][1] == {"role": "user", "content": "do work"}
    assert payload["stream"] is False


def test_local_llm_adapter_returns_structured_failure_for_malformed_payload(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"choices": ["not-a-dict"]}).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    adapter = LocalLLMAdapter("http://127.0.0.1:4000/v1/chat/completions", "local-model")

    result = adapter.run(Goal("do work"))

    assert result.success is False
    assert result.summary == "local LLM returned no content"
    assert result.returncode == 1


def test_local_llm_adapter_retries_on_failure(monkeypatch) -> None:
    """Adapter should retry on transient failures before giving up."""

    call_count = 0

    class FailingResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FailingResponse(),
    )
    adapter = LocalLLMAdapter(
        "http://127.0.0.1:4000/v1/chat/completions",
        "local-model",
        retries=2,
        retry_delay=0.01,
    )

    result = adapter.run(Goal("do work"))

    assert result.success is False
    assert call_count == 3  # initial + 2 retries
    assert "attempt" in result.summary.lower() or "failed" in result.summary.lower()


def test_local_llm_adapter_succeeds_after_retry(monkeypatch) -> None:
    """Adapter should succeed on the second attempt after a transient failure."""
    call_count = 0

    class IntermittentResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.URLError("connection refused")
            return json.dumps(
                {
                    "choices": [{"message": {"content": "done"}}],
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: IntermittentResponse(),
    )
    adapter = LocalLLMAdapter(
        "http://127.0.0.1:4000/v1/chat/completions",
        "local-model",
        retries=2,
        retry_delay=0.01,
    )

    result = adapter.run(Goal("do work"))

    assert result.success is True
    assert result.summary == "done"
    assert call_count == 2  # first failed, second succeeded


def test_local_llm_adapter_no_retry_on_success(monkeypatch) -> None:
    """Adapter should not retry when the first attempt succeeds."""
    call_count = 0

    class SuccessResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            nonlocal call_count
            call_count += 1
            return json.dumps(
                {
                    "choices": [{"message": {"content": "success"}}],
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: SuccessResponse(),
    )
    adapter = LocalLLMAdapter(
        "http://127.0.0.1:4000/v1/chat/completions",
        "local-model",
        retries=3,
        retry_delay=0.01,
    )

    result = adapter.run(Goal("do work"))

    assert result.success is True
    assert call_count == 1  # only one call needed


def test_local_llm_adapter_includes_stderr_on_failure(monkeypatch) -> None:
    """Adapter should include stderr in the failure result for network errors."""

    class FailingResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FailingResponse(),
    )
    adapter = LocalLLMAdapter(
        "http://127.0.0.1:4000/v1/chat/completions",
        "local-model",
        retries=0,
        retry_delay=0.01,
    )

    result = adapter.run(Goal("do work"))

    assert result.success is False
    assert result.returncode == 1
    assert result.stderr is not None
    assert len(result.stderr) > 0


def test_github_actions_adapter_includes_stderr_on_dispatch_failure(monkeypatch) -> None:
    """GitHub Actions adapter should include stderr in failure results."""

    def failing_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        failing_urlopen,
    )
    adapter = GitHubActionsAdapter(
        owner="test",
        repo="test",
        workflow_id="ci.yml",
        token="ghp_test",
    )

    result = adapter.run(Goal("dispatch workflow"))

    assert result.success is False
    assert result.returncode == 1
    assert result.stderr is not None
    assert len(result.stderr) > 0


def test_coding_agent_command_for_handles_unmatched_braces_in_template() -> None:
    """If the template has unmatched braces, command_for must not raise."""
    from agentic_harness.adapters.coding_agent import CodingAgentWorker
    from agentic_harness.core.state import Goal

    worker = CodingAgentWorker(["echo", "template with unmatched {braces"])
    goal = Goal(objective="fix thing")

    # Should not raise; falls back to safe replacement
    command = worker.command_for(goal)

    assert command == ["echo", "template with unmatched {braces"]


def test_coding_agent_command_for_handles_unmatched_braces_in_objective() -> None:
    """If the objective has unmatched braces, command_for must not raise."""
    from agentic_harness.adapters.coding_agent import CodingAgentWorker
    from agentic_harness.core.state import Goal

    worker = CodingAgentWorker(["echo", "hello {objective}"])
    goal = Goal(objective="fix {broken")

    command = worker.command_for(goal)

    assert command == ["echo", "hello fix {broken"]


def test_coding_agent_command_for_handles_mixed_template_and_objective_braces() -> None:
    """Template with extra braces and objective with braces: must not raise."""
    from agentic_harness.adapters.coding_agent import CodingAgentWorker
    from agentic_harness.core.state import Goal

    worker = CodingAgentWorker(["echo", "prefix {objective} suffix {{literal}}"])
    goal = Goal(objective="has {braces} and {more}")

    command = worker.command_for(goal)

    assert command == ["echo", "prefix has {braces} and {more} suffix {literal}"]


def test_coding_agent_command_for_with_no_objective_braces() -> None:
    """Normal case: template with {objective} and clean objective."""
    from agentic_harness.adapters.coding_agent import CodingAgentWorker
    from agentic_harness.core.state import Goal

    worker = CodingAgentWorker(["echo", "hello {objective} world"])
    goal = Goal(objective="test")

    command = worker.command_for(goal)

    assert command == ["echo", "hello test world"]


def test_local_llm_adapter_rejects_negative_timeout() -> None:
    """timeout < 1 must raise ValueError."""
    with pytest.raises(ValueError):
        LocalLLMAdapter("http://localhost:4000/v1", "model", timeout=0)


def test_local_llm_adapter_rejects_negative_retries() -> None:
    """retries < 0 must raise ValueError."""
    with pytest.raises(ValueError):
        LocalLLMAdapter("http://localhost:4000/v1", "model", retries=-1)


def test_local_llm_adapter_rejects_negative_retry_delay() -> None:
    """retry_delay < 0 must raise ValueError to prevent time.sleep(-n)."""
    with pytest.raises(ValueError):
        LocalLLMAdapter("http://localhost:4000/v1", "model", retry_delay=-5.0)


def test_local_llm_adapter_accepts_zero_retry_delay() -> None:
    """retry_delay=0 is valid (no delay between retries)."""
    adapter = LocalLLMAdapter("http://localhost:4000/v1", "model", retry_delay=0.0)
    assert adapter.retry_delay == 0.0


def test_local_llm_adapter_accepts_zero_retries() -> None:
    """retries=0 is valid (no retries, single attempt)."""
    adapter = LocalLLMAdapter("http://localhost:4000/v1", "model", retries=0)
    assert adapter.retries == 0


def test_local_llm_adapter_accepts_valid_defaults() -> None:
    """Default values should be accepted without error."""
    adapter = LocalLLMAdapter("http://localhost:4000/v1", "model")
    assert adapter.timeout == 120
    assert adapter.retries == 2
    assert adapter.retry_delay == 1.0
