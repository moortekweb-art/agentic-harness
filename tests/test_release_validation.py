from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic_harness.core.release_validation import (
    trusted_ci_passed,
    validate_default_branch_ancestry,
    validate_release_identity,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_release_identity_matches_exact_package_tag_and_commit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.7.0"\n',
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Release Test")
    _git(tmp_path, "config", "user.email", "release@example.invalid")
    _git(tmp_path, "add", "pyproject.toml")
    _git(tmp_path, "commit", "-m", "release")
    _git(tmp_path, "tag", "v0.7.0")

    assert validate_release_identity(tmp_path, "v0.7.0")


def test_release_identity_rejects_untrusted_tag_text_before_git_lookup(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.7.0"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match"):
        validate_release_identity(tmp_path, "v0.7.0';raise SystemExit(0);#")


def test_release_identity_rejects_tag_moved_after_trigger(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.7.0"\n',
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Release Test")
    _git(tmp_path, "config", "user.email", "release@example.invalid")
    _git(tmp_path, "add", "pyproject.toml")
    _git(tmp_path, "commit", "-m", "event commit")
    event_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    _git(tmp_path, "tag", "v0.7.0")
    (tmp_path / "later.txt").write_text("later\n", encoding="utf-8")
    _git(tmp_path, "add", "later.txt")
    _git(tmp_path, "commit", "-m", "later commit")
    _git(tmp_path, "tag", "-f", "v0.7.0")

    with pytest.raises(ValueError, match="triggering event SHA"):
        validate_release_identity(tmp_path, "v0.7.0", expected_sha=event_sha)


def test_default_branch_ancestry_uses_remote_tracking_ref(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("ok", encoding="utf-8")
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Release Test")
    _git(tmp_path, "config", "user.email", "release@example.invalid")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-m", "release")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")

    validate_default_branch_ancestry(tmp_path, "main")


def test_trusted_ci_requires_exact_successful_default_branch_push() -> None:
    sha = "a" * 40
    trusted = {
        "head_sha": sha,
        "conclusion": "success",
        "event": "push",
        "head_branch": "main",
    }
    variants = [
        {**trusted, "head_sha": "b" * 40},
        {**trusted, "conclusion": "failure"},
        {**trusted, "event": "pull_request"},
        {**trusted, "head_branch": "feature"},
    ]

    assert trusted_ci_passed({"workflow_runs": [trusted]}, sha=sha, default_branch="main")
    assert not trusted_ci_passed(
        {"workflow_runs": variants},
        sha=sha,
        default_branch="main",
    )
