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
PATH_PATTERN = re.compile(r"/(?:tmp|mnt/raid0)/[^\s'\"]+")
SECRET_PATTERN = re.compile(r"(?i)(?:ghp_[A-Za-z0-9]+|sk-[A-Za-z0-9]+|authorization:\s*bearer)")


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
    expected_files = {
        source / "transcripts" / f"{task_id}-{arm}.log" for task_id, arm in keys
    }
    if transcript_files != expected_files:
        raise ValueError("transcript set does not match raw task-arm records")
    token_by_arm: dict[str, list[int]] = {"direct": [], "harness": []}
    manifest: list[dict[str, Any]] = []
    for transcript in sorted(transcript_files):
        text = transcript.read_text(encoding="utf-8")
        if SECRET_PATTERN.search(text):
            raise ValueError(f"possible secret in {transcript.name}")
        match = TOKEN_PATTERN.search(text)
        if match is None:
            raise ValueError(f"missing token count in {transcript.name}")
        arm = "harness" if transcript.stem.endswith("-harness") else "direct"
        tokens = int(match.group(1).replace(",", ""))
        token_by_arm[arm].append(tokens)
        target = redacted_dir / transcript.name
        target.write_text(redact(text), encoding="utf-8")
        manifest.append(
            {
                "file": f"transcripts/{transcript.name}",
                "arm": arm,
                "tokens": tokens,
                "sha256": sha256(target),
            }
        )
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    summary["token_metrics_available"] = True
    summary["data_quality"] = {
        "records": len(rows), "unique_task_arm_pairs": len(keys),
        "transcripts": len(manifest), "secret_scan_passed": True,
        "full_prompt_identity": False,
        "comparison_scope": "end_to_end_systems",
    }
    for arm, values in token_by_arm.items():
        summary["arms"][arm]["total_tokens"] = sum(values)
        summary["arms"][arm]["mean_tokens"] = round(sum(values) / len(values), 1)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.destination), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
