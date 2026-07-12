#!/usr/bin/env python3
"""Validate and redact a completed real-agent comparison for publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

TOKEN_PATTERN = re.compile(r"tokens used\s*\n([0-9,]+)\s*$")
SESSION_PATTERN = re.compile(r"(?m)^session id: .+$")
PATH_PATTERN = re.compile(
    r"(?:/(?:tmp|mnt/raid0|home|Users)/[^\s'\"]+|[A-Za-z]:\\Users\\[^\s'\"]+)"
)
SECRET_PATTERN = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9]+|"
    r"authorization:\s*bearer|(?:api[_-]?key|password|secret)\s*[:=]\s*\S+)"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def redact(text: str) -> str:
    text = SESSION_PATTERN.sub("session id: [redacted]", text)
    text = PATH_PATTERN.sub("[local-path]", text)
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def package(source: Path, destination: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in (source / "raw.jsonl").read_text().splitlines()]
    keys = {(row["task_id"], row["arm"]) for row in rows}
    if len(rows) != 20 or len(keys) != 20:
        raise ValueError("expected 20 unique task-arm records")
    destination.mkdir(parents=True, exist_ok=False)
    redacted_dir = destination / "transcripts"
    redacted_dir.mkdir()
    transcript_files = set((source / "transcripts").glob("*.log"))
    identities = [_transcript_identity(path, keys) for path in transcript_files]
    transcript_keys = {identity[0] for identity in identities if identity is not None}
    if any(identity is None for identity in identities) or transcript_keys != keys:
        raise ValueError("transcript set does not match raw task-arm records")
    actual_attempts: dict[tuple[str, str], set[int]] = {key: set() for key in keys}
    for identity in identities:
        assert identity is not None
        key, attempt = identity
        if attempt in actual_attempts[key]:
            raise ValueError("duplicate attempt transcript")
        actual_attempts[key].add(attempt)
    expected_attempts = {
        (row["task_id"], row["arm"]): set(range(1, int(row["attempts"]) + 1))
        for row in rows
    }
    if actual_attempts != expected_attempts:
        raise ValueError("attempt transcripts do not match raw attempt counts")
    tokens_by_run: dict[tuple[str, str], int] = {key: 0 for key in keys}
    token_observed_runs: set[tuple[str, str]] = set()
    manifest: list[dict[str, Any]] = []
    for transcript in sorted(transcript_files):
        text = transcript.read_text(encoding="utf-8")
        if SECRET_PATTERN.search(text):
            raise ValueError(f"possible secret in {transcript.name}")
        identity = _transcript_identity(transcript, keys)
        if identity is None:
            raise ValueError(f"unmatched transcript: {transcript.name}")
        key, attempt = identity
        arm = key[1]
        match = TOKEN_PATTERN.search(text)
        tokens = int(match.group(1).replace(",", "")) if match is not None else None
        if tokens is not None:
            tokens_by_run[key] += tokens
            token_observed_runs.add(key)
        target = redacted_dir / transcript.name
        target.write_text(redact(text), encoding="utf-8")
        manifest.append(
            {
                "file": f"transcripts/{transcript.name}",
                "arm": arm,
                "attempt": attempt,
                "tokens": tokens,
                "sha256": sha256(target),
            }
        )
    source_summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    summary = {key: value for key, value in source_summary.items() if key != "arms"}
    summary["arms"] = {arm: _summarize_rows(rows, arm) for arm in ("direct", "harness")}
    token_observations = sum(row["tokens"] is not None for row in manifest)
    summary["token_metrics_available"] = token_observations > 0
    summary["token_metrics_complete"] = token_observations == len(manifest)
    summary["token_observations"] = token_observations
    summary["data_quality"] = {
        "records": len(rows), "unique_task_arm_pairs": len(keys),
        "transcripts": len(manifest), "recognized_pattern_scan_passed": True,
        "full_prompt_identity": False,
        "comparison_scope": "end_to_end_systems",
    }
    for arm in ("direct", "harness"):
        values = [value for (task_id, key_arm), value in tokens_by_run.items() if key_arm == arm]
        arm_keys = {key for key in keys if key[1] == arm}
        observed = arm_keys & token_observed_runs
        summary["arms"][arm]["token_observations"] = len(observed)
        if observed == arm_keys:
            summary["arms"][arm]["total_tokens"] = sum(values)
            summary["arms"][arm]["mean_tokens"] = round(sum(values) / len(values), 1)
        elif observed:
            summary["arms"][arm]["observed_total_tokens"] = sum(
                tokens_by_run[key] for key in observed
            )
    (destination / "raw.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (destination / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (destination / "transcript_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _transcript_identity(
    path: Path, keys: set[tuple[str, str]]
) -> tuple[tuple[str, str], int] | None:
    match = re.search(r"\.attempt-([1-9][0-9]*)$", path.stem)
    attempt = int(match.group(1)) if match else 1
    stem = re.sub(r"\.attempt-[1-9][0-9]*$", "", path.stem)
    matches = [key for key in keys if stem == f"{key[0]}-{key[1]}"]
    return (matches[0], attempt) if len(matches) == 1 else None


def _summarize_rows(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    selected = [row for row in rows if row["arm"] == arm]
    if not selected:
        raise ValueError(f"missing raw rows for arm: {arm}")
    return {
        "runs": len(selected),
        "accepted": sum(bool(row["accepted"]) for row in selected),
        "verifier_passes": sum(bool(row["verifier_pass"]) for row in selected),
        "false_accepts": sum(
            bool(row["accepted"]) and not bool(row["verifier_pass"])
            for row in selected
        ),
        "mean_attempts": round(
            sum(int(row["attempts"]) for row in selected) / len(selected), 3
        ),
        "mean_elapsed_seconds": round(
            sum(float(row["elapsed_seconds"]) for row in selected) / len(selected), 3
        ),
        "runs_with_unintended_paths": sum(
            bool(row["unintended_paths"]) for row in selected
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.destination), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
