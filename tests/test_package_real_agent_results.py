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


def test_package_recomputes_aggregates_from_raw_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    rows = []
    for index in range(10):
        for arm in ("direct", "harness"):
            rows.append(
                {
                    "task_id": f"task-{index}", "arm": arm,
                    "accepted": True, "verifier_pass": True,
                    "false_accept": False, "attempts": 1,
                    "elapsed_seconds": 2.0, "unintended_paths": [],
                }
            )
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (source / "summary.json").write_text(
        json.dumps({"arms": {"direct": {"accepted": 0}, "harness": {"accepted": 0}}}),
        encoding="utf-8",
    )
    transcripts = source / "transcripts"
    transcripts.mkdir()
    for row in rows:
        (transcripts / f"{row['task_id']}-{row['arm']}.log").write_text(
            "tokens used\n1\n", encoding="utf-8"
        )

    summary = package(source, tmp_path / "published")

    assert summary["arms"]["direct"]["accepted"] == 10
    assert summary["arms"]["harness"]["verifier_passes"] == 10


def test_package_derives_false_accept_instead_of_trusting_raw_flag(tmp_path: Path) -> None:
    source, rows = _complete_source(tmp_path)
    rows[0]["verifier_pass"] = False
    rows[0]["false_accept"] = False
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    summary = package(source, tmp_path / "published")

    assert summary["arms"]["direct"]["false_accepts"] == 1


def test_package_rejects_unknown_arm(tmp_path: Path) -> None:
    source, rows = _complete_source(tmp_path)
    rows[0]["arm"] = "evil"
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="exactly ten direct and ten harness"):
        package(source, tmp_path / "published")


def test_package_rejects_imbalanced_arms(tmp_path: Path) -> None:
    source, rows = _complete_source(tmp_path)
    direct = next(row for row in rows if row["arm"] == "direct")
    direct["task_id"] = "extra-task"
    direct["arm"] = "harness"
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="exactly ten direct and ten harness"):
        package(source, tmp_path / "published")


def test_package_rejects_missing_raw_row(tmp_path: Path) -> None:
    source, rows = _complete_source(tmp_path)
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows[:-1]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="20 unique task-arm"):
        package(source, tmp_path / "published")


def test_package_accepts_missing_optional_token_footer(tmp_path: Path) -> None:
    source, rows = _complete_source(tmp_path)
    first = source / "transcripts" / f"{rows[0]['task_id']}-{rows[0]['arm']}.log"
    first.write_text("timed out without telemetry\n", encoding="utf-8")

    summary = package(source, tmp_path / "published")

    assert summary["token_metrics_available"] is True
    assert summary["token_metrics_complete"] is False
    assert summary["token_observations"] == 19
    assert "mean_tokens" not in summary["arms"][rows[0]["arm"]]


def test_package_rejects_missing_retry_transcript(tmp_path: Path) -> None:
    source, rows = _complete_source(tmp_path)
    harness = next(row for row in rows if row["arm"] == "harness")
    harness["attempts"] = 3
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    base = source / "transcripts" / f"{harness['task_id']}-harness.log"
    base.rename(base.with_name(f"{harness['task_id']}-harness.attempt-3.log"))

    with pytest.raises(ValueError, match="attempt transcripts do not match"):
        package(source, tmp_path / "published")


def _complete_source(tmp_path: Path) -> tuple[Path, list[dict[str, object]]]:
    source = tmp_path / "source"
    source.mkdir()
    rows: list[dict[str, object]] = []
    for index in range(10):
        for arm in ("direct", "harness"):
            rows.append(
                {
                    "task_id": f"task-{index}", "arm": arm,
                    "accepted": True, "verifier_pass": True,
                    "false_accept": False, "attempts": 1,
                    "elapsed_seconds": 2.0, "unintended_paths": [],
                }
            )
    (source / "raw.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (source / "summary.json").write_text(
        json.dumps({"arms": {"direct": {}, "harness": {}}}), encoding="utf-8"
    )
    transcripts = source / "transcripts"
    transcripts.mkdir()
    for row in rows:
        (transcripts / f"{row['task_id']}-{row['arm']}.log").write_text(
            "tokens used\n1\n", encoding="utf-8"
        )
    return source, rows


def test_redact_covers_common_private_paths() -> None:
    value = " /home/alice/project /Users/bob/work C:\\Users\\carol\\repo "
    redacted = redact(value)
    assert "alice" not in redacted
    assert "bob" not in redacted
    assert "carol" not in redacted
