from __future__ import annotations

import os
import hashlib
import json
import subprocess
from pathlib import Path
import sys

import pytest

from agentic_harness.core.release_validation import (
    REPRESENTATIVE_SOURCES,
    trusted_ci_passed,
    validate_default_branch_ancestry,
    validate_release_identity,
    validate_representative_receipt,
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


def test_release_identity_script_runs_without_site_packages(tmp_path: Path) -> None:
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
    release_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    script = Path(__file__).resolve().parents[1] / "agentic_harness/core/release_validation.py"
    env = {
        **os.environ,
        "RELEASE_TAG": "v0.7.0",
        "RELEASE_SHA": release_sha,
    }

    result = subprocess.run(
        [sys.executable, "-S", str(script), "identity", "--project-dir", str(tmp_path)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr


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


def test_representative_receipt_binds_release_identity_and_sources(tmp_path: Path) -> None:
    results = tmp_path / "evaluation" / "results" / "representative"
    results.mkdir(parents=True)
    checksums = {}
    for relative in REPRESENTATIVE_SOURCES:
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"{relative}\n", encoding="utf-8")
        checksums[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = [
        {"arm": arm, "accepted": True, "verifier_pass": True, "false_success": False,
         "caught_false_claim": False, "recovered": False, "attempts": 1,
         "elapsed_seconds": 0.1, "case": "true_completion"}
        for arm in ("baseline", "harness") for _ in range(24)
    ]
    (results / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    summary = {"task_count": 24, "record_count": 48, "repetitions": 1,
               "pristine_arm_mismatches": 0,
               "arms": {"baseline": {"runs": 24}, "harness": {"runs": 24}}}
    (results / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    receipt = {
        "git_commit": "a" * 40,
        "git_dirty": False,
        "harness_version": "0.7.2",
        "source_checksums": checksums,
    }
    (results / "environment.json").write_text(json.dumps(receipt), encoding="utf-8")

    validate_representative_receipt(tmp_path, expected_sha="a" * 40, expected_version="0.7.2")


@pytest.mark.parametrize("field,value", [
    ("git_commit", "b" * 40),
    ("git_dirty", True),
    ("harness_version", "0.7.1"),
])
def test_representative_receipt_rejects_stale_metadata(
    tmp_path: Path, field: str, value: object
) -> None:
    results = tmp_path / "evaluation" / "results" / "representative"
    results.mkdir(parents=True)
    (results / "environment.json").write_text(json.dumps({
        "git_commit": "a" * 40, "git_dirty": False, "harness_version": "0.7.2",
        "source_checksums": {},
    } | {field: value}), encoding="utf-8")
    (results / "raw.jsonl").write_text("", encoding="utf-8")
    (results / "summary.json").write_text(json.dumps({}), encoding="utf-8")

    with pytest.raises(ValueError):
        validate_representative_receipt(
            tmp_path, expected_sha="a" * 40, expected_version="0.7.2"
        )
