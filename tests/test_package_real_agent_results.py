import json
from pathlib import Path

import pytest

from evaluation.package_real_agent_results import package, redact


def test_redact_removes_session_ids_and_local_paths() -> None:
    value = "session id: abc-123\nworkdir: /tmp/private/work\nsource /mnt/raid0/home/file\n"
    redacted = redact(value)
    assert "abc-123" not in redacted
    assert "/tmp/private" not in redacted
    assert "/mnt/raid0" not in redacted


def test_package_rejects_missing_transcript(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    rows = [
        {"task_id": f"task-{index}", "arm": arm}
        for index in range(10)
        for arm in ("direct", "harness")
    ]
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (source / "summary.json").write_text(
        json.dumps({"arms": {"direct": {}, "harness": {}}}), encoding="utf-8"
    )
    transcripts = source / "transcripts"
    transcripts.mkdir()
    for row in rows[:-1]:
        (transcripts / f"{row['task_id']}-{row['arm']}.log").write_text(
            "tokens used\n1\n", encoding="utf-8"
        )

    with pytest.raises(ValueError, match="transcript set does not match"):
        package(source, tmp_path / "published")
