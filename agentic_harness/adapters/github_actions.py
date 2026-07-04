"""GitHub Actions integration adapter."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


@dataclass
class GitHubActionsAdapter:
    """Dispatch a GitHub Actions workflow for a goal."""

    owner: str
    repo: str
    workflow_id: str
    token: str | None = None
    ref: str = "main"
    api_base: str = "https://api.github.com"
    wait_for_completion: bool = False
    poll_interval: float = 5.0
    timeout: int = 300
    api_version: str = "2026-03-10"

    def dispatch_payload(self, goal: Goal) -> dict[str, object]:
        payload: dict[str, object] = {
            "ref": self.ref,
            "inputs": {
                "goal_id": goal.id,
                "objective": goal.objective,
            },
        }
        if self.wait_for_completion:
            payload["return_run_details"] = True
        return payload

    def dispatch_url(self) -> str:
        return (
            f"{self.api_base}/repos/{self.owner}/{self.repo}"
            f"/actions/workflows/{self.workflow_id}/dispatches"
        )

    def runs_url(self, *, created_after: str | None = None) -> str:
        params = {
            "branch": self.ref,
            "event": "workflow_dispatch",
            "per_page": 10,
        }
        if created_after:
            params["created"] = f">={created_after}"
        query = urllib.parse.urlencode(params)
        return (
            f"{self.api_base}/repos/{self.owner}/{self.repo}"
            f"/actions/workflows/{self.workflow_id}/runs?{query}"
        )

    def run_url(self, run_id: int | str) -> str:
        return f"{self.api_base}/repos/{self.owner}/{self.repo}/actions/runs/{run_id}"

    def run(self, goal: Goal) -> WorkerResult:
        if not self.token:
            return WorkerResult(
                success=False,
                summary="GitHub token is required for workflow dispatch",
                returncode=2,
            )
        dispatch_started_at = _utc_timestamp()
        request = urllib.request.Request(
            self.dispatch_url(),
            data=json.dumps(self.dispatch_payload(goal)).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "agentic-harness",
                "X-GitHub-Api-Version": self.api_version,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                response_payload = _read_json_response(response)
        except Exception as exc:  # network adapter boundary: keep failure structured
            return WorkerResult(success=False, summary=str(exc), returncode=1)
        if 200 <= status < 300 and self.wait_for_completion:
            direct_run_url = _dispatch_run_url(response_payload)
            run_id = _dispatch_run_id(response_payload)
            if not direct_run_url and isinstance(run_id, (int, str)):
                direct_run_url = self.run_url(run_id)
            if direct_run_url:
                return self._wait_for_run_url(direct_run_url)
            return self._wait_for_completion(created_after=dispatch_started_at)
        artifacts = _dispatch_artifacts(response_payload)
        return WorkerResult(
            success=200 <= status < 300,
            summary=(
                f"GitHub Actions dispatch accepted HTTP {status}"
                + (
                    f"; workflow run id {_dispatch_run_id(response_payload)}"
                    if _dispatch_run_id(response_payload)
                    else "; workflow completion not verified"
                )
                if 200 <= status < 300
                else f"GitHub Actions dispatch returned HTTP {status}"
            ),
            artifacts=artifacts,
            returncode=0 if 200 <= status < 300 else status,
        )

    def _wait_for_run_url(self, run_url: str) -> WorkerResult:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() <= deadline:
            request = urllib.request.Request(
                run_url,
                method="GET",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "agentic-harness",
                    "X-GitHub-Api-Version": self.api_version,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = _read_json_response(response)
            except Exception as exc:  # network adapter boundary: keep failure structured
                return WorkerResult(success=False, summary=str(exc), returncode=1)
            if payload.get("status") == "completed":
                return _workflow_result(payload)
            if self.poll_interval > 0:
                time.sleep(self.poll_interval)
        return WorkerResult(
            success=False,
            summary="timed out waiting for GitHub Actions workflow completion",
            returncode=124,
        )

    def _wait_for_completion(self, *, created_after: str | None = None) -> WorkerResult:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() <= deadline:
            request = urllib.request.Request(
                self.runs_url(created_after=created_after),
                method="GET",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "agentic-harness",
                    "X-GitHub-Api-Version": self.api_version,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = _read_json_response(response)
            except Exception as exc:  # network adapter boundary: keep failure structured
                return WorkerResult(success=False, summary=str(exc), returncode=1)
            runs_value = payload.get("workflow_runs", [])
            runs = runs_value if isinstance(runs_value, list) else []
            run = runs[0] if runs and isinstance(runs[0], dict) else None
            if run and run.get("status") == "completed":
                return _workflow_result(run)
            if self.poll_interval > 0:
                time.sleep(self.poll_interval)
        return WorkerResult(
            success=False,
            summary="timed out waiting for GitHub Actions workflow completion",
            returncode=124,
        )


def _read_json_response(response: object) -> dict[str, object]:
    read = getattr(response, "read", None)
    if not callable(read):
        return {}
    raw = read()
    if not raw:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _dispatch_run_id(payload: dict[str, object]) -> object | None:
    return payload.get("workflow_run_id")


def _dispatch_run_url(payload: dict[str, object]) -> str:
    run_url = payload.get("run_url")
    if isinstance(run_url, str):
        return run_url
    return ""


def _dispatch_artifacts(payload: dict[str, object]) -> list[str]:
    html_url = payload.get("html_url")
    return [html_url] if isinstance(html_url, str) else []


def _workflow_result(run: dict[str, object]) -> WorkerResult:
    conclusion = str(run.get("conclusion") or "unknown")
    html_url = run.get("html_url")
    artifacts = [html_url] if isinstance(html_url, str) else []
    return WorkerResult(
        success=conclusion == "success",
        summary=f"GitHub Actions workflow completed: {conclusion}",
        artifacts=artifacts,
        returncode=0 if conclusion == "success" else 1,
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
