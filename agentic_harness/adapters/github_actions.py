"""GitHub Actions integration adapter."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

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

    def dispatch_payload(self, goal: Goal) -> dict[str, object]:
        return {
            "ref": self.ref,
            "inputs": {
                "goal_id": goal.id,
                "objective": goal.objective,
            },
        }

    def dispatch_url(self) -> str:
        return (
            f"{self.api_base}/repos/{self.owner}/{self.repo}"
            f"/actions/workflows/{self.workflow_id}/dispatches"
        )

    def runs_url(self) -> str:
        query = urllib.parse.urlencode({"branch": self.ref, "per_page": 10})
        return (
            f"{self.api_base}/repos/{self.owner}/{self.repo}"
            f"/actions/workflows/{self.workflow_id}/runs?{query}"
        )

    def run(self, goal: Goal) -> WorkerResult:
        if not self.token:
            return WorkerResult(
                success=False,
                summary="GitHub token is required for workflow dispatch",
                returncode=2,
            )
        request = urllib.request.Request(
            self.dispatch_url(),
            data=json.dumps(self.dispatch_payload(goal)).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "agentic-harness",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
        except Exception as exc:  # network adapter boundary: keep failure structured
            return WorkerResult(success=False, summary=str(exc), returncode=1)
        if 200 <= status < 300 and self.wait_for_completion:
            return self._wait_for_completion()
        return WorkerResult(
            success=200 <= status < 300,
            summary=(
                f"GitHub Actions dispatch accepted HTTP {status}; "
                "workflow completion not verified"
                if 200 <= status < 300
                else f"GitHub Actions dispatch returned HTTP {status}"
            ),
            returncode=0 if 200 <= status < 300 else status,
        )

    def _wait_for_completion(self) -> WorkerResult:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() <= deadline:
            request = urllib.request.Request(
                self.runs_url(),
                method="GET",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "agentic-harness",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception as exc:  # network adapter boundary: keep failure structured
                return WorkerResult(success=False, summary=str(exc), returncode=1)
            runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
            run = runs[0] if runs and isinstance(runs[0], dict) else None
            if run and run.get("status") == "completed":
                conclusion = str(run.get("conclusion") or "unknown")
                html_url = run.get("html_url")
                artifacts = [str(html_url)] if html_url else []
                return WorkerResult(
                    success=conclusion == "success",
                    summary=f"GitHub Actions workflow completed: {conclusion}",
                    artifacts=artifacts,
                    returncode=0 if conclusion == "success" else 1,
                )
            if self.poll_interval > 0:
                time.sleep(self.poll_interval)
        return WorkerResult(
            success=False,
            summary="timed out waiting for GitHub Actions workflow completion",
            returncode=124,
        )
