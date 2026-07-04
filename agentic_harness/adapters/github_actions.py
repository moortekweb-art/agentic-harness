"""GitHub Actions integration adapter."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


@dataclass
class GitHubActionsAdapter:
    """Dispatch a GitHub Actions workflow for a goal.

    A successful result means GitHub accepted the dispatch request. It does not
    mean the workflow run completed successfully.
    """

    owner: str
    repo: str
    workflow_id: str
    token: str | None = None
    ref: str = "main"
    api_base: str = "https://api.github.com"

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
