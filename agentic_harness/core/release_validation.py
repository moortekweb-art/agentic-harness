"""Testable release gates used by the GitHub publication workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any
import urllib.parse
import urllib.request


MAX_API_RESPONSE_BYTES = 2_000_000
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def validate_release_identity(
    project_dir: Path,
    release_tag: str,
    *,
    expected_sha: str | None = None,
) -> str:
    metadata = tomllib.loads((project_dir / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(metadata["project"]["version"])
    expected = f"v{version}"
    if release_tag != expected:
        raise ValueError(f"release tag {release_tag!r} does not match {expected!r}")
    tagged = _git(project_dir, "rev-list", "-n", "1", release_tag)
    head = _git(project_dir, "rev-parse", "HEAD")
    if tagged != head:
        raise ValueError("checked-out commit does not match the release tag")
    if expected_sha is not None:
        normalized = expected_sha.strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
            raise ValueError("triggering event SHA is not a full Git commit SHA")
        if head != normalized:
            raise ValueError("checked-out commit does not match the triggering event SHA")
    return head


def validate_default_branch_ancestry(project_dir: Path, default_branch: str) -> None:
    subprocess.run(
        ["git", "check-ref-format", "--branch", default_branch],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            "HEAD",
            f"origin/{default_branch}",
        ],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def trusted_ci_passed(payload: Any, *, sha: str, default_branch: str) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return False
    return any(
        isinstance(run, dict)
        and run.get("head_sha") == sha
        and run.get("conclusion") == "success"
        and run.get("event") == "push"
        and run.get("head_branch") == default_branch
        for run in payload["workflow_runs"]
    )


def fetch_ci_runs(*, repository: str, sha: str, token: str) -> dict[str, Any]:
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError("repository must use owner/name syntax")
    query = urllib.parse.urlencode({"head_sha": sha, "per_page": 100})
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/workflows/ci.yml/runs?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(MAX_API_RESPONSE_BYTES + 1)
    if len(raw) > MAX_API_RESPONSE_BYTES:
        raise ValueError("GitHub Actions API response exceeded the size limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub Actions API returned a non-object response")
    return payload


def validate_ci(project_dir: Path, *, repository: str, default_branch: str, token: str) -> None:
    sha = _git(project_dir, "rev-parse", "HEAD")
    payload = fetch_ci_runs(repository=repository, sha=sha, token=token)
    if not trusted_ci_passed(payload, sha=sha, default_branch=default_branch):
        raise ValueError(
            f"trusted default-branch CI has not passed for exact release commit {sha}"
        )


def _git(project_dir: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=project_dir,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("identity", "ancestry", "ci"))
    parser.add_argument("--project-dir", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = Path(args.project_dir).resolve()
    try:
        if args.gate == "identity":
            validate_release_identity(
                project_dir,
                os.environ["RELEASE_TAG"],
                expected_sha=os.environ["RELEASE_SHA"],
            )
        elif args.gate == "ancestry":
            validate_default_branch_ancestry(project_dir, os.environ["DEFAULT_BRANCH"])
        else:
            validate_ci(
                project_dir,
                repository=os.environ["REPOSITORY"],
                default_branch=os.environ["DEFAULT_BRANCH"],
                token=os.environ["GH_TOKEN"],
            )
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
