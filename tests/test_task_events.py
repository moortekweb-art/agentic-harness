from __future__ import annotations

import json
from pathlib import Path

from agentic_harness.core.events import TaskEventStore


def test_task_events_are_ordered_private_and_sanitized(tmp_path: Path) -> None:
    store = TaskEventStore(tmp_path, "goal-123")

    first = store.append(
        stage="act",
        kind="tool_finished",
        summary="Updated src/app.py with api_key=super-secret-value",
        tool_name="replace_text",
        tool_status="passed",
        cycle=2,
        checkpoint="source_updated",
    )
    second = store.append(
        stage="check",
        kind="check_finished",
        summary="Focused test passed",
        tool_name="run_check",
        tool_status="passed",
        cycle=2,
        checkpoint="verified",
    )

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert first["evidence_id"] == "event:1"
    assert "super-secret-value" not in json.dumps(first)
    assert [event["summary"] for event in store.read()] == [
        "Updated src/app.py with api_key=<redacted>",
        "Focused test passed",
    ]
    paths = sorted(store.events_dir.glob("*.json"))
    assert [path.name for path in paths] == ["000001.json", "000002.json"]
    assert all(path.stat().st_mode & 0o077 == 0 for path in paths)


def test_task_event_store_rejects_path_traversal_goal_id(tmp_path: Path) -> None:
    try:
        TaskEventStore(tmp_path, "../../escape")
    except ValueError as exc:
        assert "goal id" in str(exc)
    else:
        raise AssertionError("unsafe goal id should be rejected")
