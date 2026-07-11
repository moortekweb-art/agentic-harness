"""Deterministic review contracts."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agentic_harness.core.safety import subprocess_environment
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.state import Goal

CriterionCheck = Callable[[Goal], tuple[bool, str]]


@dataclass(frozen=True)
class ReviewCriterion:
    name: str
    check: CriterionCheck
    description: str = ""
    independent: bool = True


@dataclass
class ReviewResult:
    passed: bool
    criteria: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "criteria": list(self.criteria)}


class DeterministicReviewer:
    """Run typed pass/fail criteria without model judgment."""

    def __init__(self, criteria: list[ReviewCriterion] | None = None) -> None:
        self.criteria = (
            criteria
            if criteria is not None
            else [
                ReviewCriterion(
                    "worker_success",
                    self._worker_success,
                    independent=False,
                )
            ]
        )

    def review(self, goal: Goal) -> ReviewResult:
        if not self.criteria:
            raise ValueError(
                "cannot review with empty criteria list; "
                "add at least one criterion or use a default reviewer"
            )
        results: list[dict[str, object]] = []
        for criterion in self.criteria:
            passed, message = criterion.check(goal)
            results.append(
                {
                    "name": redact_secrets(criterion.name),
                    "description": redact_secrets(criterion.description),
                    "passed": bool(passed),
                    "message": redact_secrets(str(message)),
                    "independent": criterion.independent,
                }
            )
        return ReviewResult(
            passed=all(bool(item["passed"]) for item in results),
            criteria=results,
        )

    def _worker_success(self, goal: Goal) -> tuple[bool, str]:
        success = bool(goal.metadata.get("worker_success"))
        return success, "worker reported success" if success else "worker did not report success"


def artifact_exists(project_dir: str | Path, artifact_path: str) -> ReviewCriterion:
    """Require a recorded artifact to exist below the project directory."""
    root = Path(project_dir).resolve()
    rel = Path(artifact_path)

    def check(goal: Goal) -> tuple[bool, str]:
        try:
            path = (root / rel).resolve()
            path.relative_to(root)
        except ValueError:
            return False, f"artifact is outside project: {artifact_path}"
        if artifact_path not in goal.artifacts:
            return False, f"artifact is not recorded on goal: {artifact_path}"
        if not path.exists():
            return False, f"artifact does not exist: {artifact_path}"
        return True, f"artifact exists: {artifact_path}"

    return ReviewCriterion(
        "artifact_exists",
        check,
        f"Artifact must be recorded and exist: {artifact_path}",
    )


def command_passes(
    command: list[str],
    *,
    cwd: str | Path = ".",
    timeout: int = 60,
    secret_env_names: list[str] | tuple[str, ...] = (),
) -> ReviewCriterion:
    """Require a command to exit successfully."""

    def check(goal: Goal) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                env=subprocess_environment(secret_env_names),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"independent command timed out after {timeout}s"
        except OSError:
            return False, "independent command could not start"
        if proc.returncode == 0:
            return True, "independent command passed"
        return False, f"independent command failed with exit code {proc.returncode}"

    return ReviewCriterion(
        "command_passes",
        check,
        "Configured independent command must pass.",
    )


def file_changed(project_dir: str | Path, path: str) -> ReviewCriterion:
    """Require git to report a path as changed."""

    def check(goal: Goal) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain", "--", path],
                cwd=str(project_dir),
                env=subprocess_environment(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            return False, "git status could not start"
        if proc.returncode != 0:
            return False, f"git status failed with exit code {proc.returncode}"
        changed = bool(proc.stdout.strip())
        return changed, f"file changed: {path}" if changed else f"file clean: {path}"

    return ReviewCriterion("file_changed", check, f"File must be changed: {path}")


def git_clean(project_dir: str | Path = ".") -> ReviewCriterion:
    """Require the git worktree to be clean."""

    def check(goal: Goal) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(project_dir),
                env=subprocess_environment(),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            return False, "git status could not start"
        if proc.returncode != 0:
            return False, f"git status failed with exit code {proc.returncode}"
        clean = not proc.stdout.strip()
        return clean, "git worktree clean" if clean else "git worktree has changes"

    return ReviewCriterion("git_clean", check, "Git worktree must be clean")


def goal_status_is(status: str) -> ReviewCriterion:
    """Require the goal to be in a specific status.

    Useful as a post-review sanity check: if you expect the goal to be DONE
    after review passes, require status == "done" so a buggy transition
    cannot silently leave the goal in REVIEW.
    """

    def check(goal: Goal) -> tuple[bool, str]:
        if goal.status.value == status:
            return True, f"goal status is {status}"
        return (
            False,
            f"goal status is {goal.status.value}, expected {status}",
        )

    return ReviewCriterion(
        "goal_status_is",
        check,
        f"Goal must be in status {status}",
    )
