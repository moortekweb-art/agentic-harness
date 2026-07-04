#!/usr/bin/env python3
"""Lightweight tests for mission mode in the local Node1 goal supervisor.

No tmux or vLLM started. Tests pure functions and state transitions.
"""

import json
import atexit
import contextlib
import io
import sys
import tempfile
from pathlib import Path

# Import the supervisor module by exec'ing it.
# The module name has a hyphen so we can't import it directly.
# We need to patch the globals dict (sup_ns) because the compiled functions
# use LOAD_GLOBAL to look up constants like MISSION_JSON.
SUPERVISOR_PATH = (
    Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts")
    / "local-node1-goal-supervisor.py"
)
MANAGER_PATH = Path("/mnt/raid0/documentation/scripts/local-node1-goal-manager.py")

sup_ns: dict = {"__file__": str(SUPERVISOR_PATH)}
exec(compile(SUPERVISOR_PATH.read_text(), str(SUPERVISOR_PATH), "exec"), sup_ns)
manager_ns: dict = {
    "__file__": str(MANAGER_PATH),
    "__name__": "local_goal_manager_test",
}
exec(compile(MANAGER_PATH.read_text(), str(MANAGER_PATH), "exec"), manager_ns)
LIVE_STATE_PATHS = [
    sup_ns["STATE_DIR"] / "local-node1-goal-mission.json",
    sup_ns["STATE_DIR"] / "local-node1-goal-queue.json",
]
LIVE_STATE_SNAPSHOT = {
    path: path.read_bytes() if path.exists() else None for path in LIVE_STATE_PATHS
}


def _restore_live_state() -> None:
    for path, original in LIVE_STATE_SNAPSHOT.items():
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)


def _assert_live_state_unchanged() -> None:
    for path, original in LIVE_STATE_SNAPSHOT.items():
        current = path.read_bytes() if path.exists() else None
        assert current == original, f"live state modified by tests: {path}"


atexit.register(_restore_live_state)

# ---------------------------------------------------------------------------
# Test helpers — patch sup_ns globals, not module attributes
# ---------------------------------------------------------------------------


def _temp_mission_path() -> Path:
    """Return a temp file path for mission JSON."""
    return Path(tempfile.mktemp(suffix="-mission.json"))


def _temp_queue_path() -> Path:
    """Return a temp file path for queue JSON."""
    return Path(tempfile.mktemp(suffix="-queue.json"))


def _patch_paths(mission_path: Path, queue_path: Path) -> None:
    sup_ns["MISSION_JSON"] = mission_path
    sup_ns["QUEUE_JSON"] = queue_path


def _restore_paths() -> None:
    sup_ns["MISSION_JSON"] = sup_ns["STATE_DIR"] / "local-node1-goal-mission.json"
    sup_ns["QUEUE_JSON"] = sup_ns["STATE_DIR"] / "local-node1-goal-queue.json"


def _cleanup(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def _get(name: str):
    """Get a function/constant from the exec'd namespace."""
    return sup_ns[name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_mission_has_required_fields() -> None:
    m = _get("empty_mission")()
    assert m["version"] == 1
    assert m["status"] == "idle"
    assert m["umbrella_objective"] == ""
    assert m["active_subgoal"] is None
    assert m["completed_subgoals"] == []
    assert m["failed_subgoals"] == []
    assert m["rejected_subgoals"] == []
    assert m["max_subgoals"] == 20
    assert m["generated_count"] == 0
    assert m["failure_streak"] == 0
    print("PASS test_empty_mission_has_required_fields")


def test_mission_create_writes_valid_state() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["umbrella_objective"] = "Test mission"
        mission["status"] = "active"
        mission["created_at"] = _get("now")()
        mission["done_criteria"] = ["Step 1", "Step 2"]
        _get("write_mission")(mission)

        # Verify atomic write (file exists, valid JSON)
        assert mp.exists()
        data = json.loads(mp.read_text())
        assert data["status"] == "active"
        assert data["umbrella_objective"] == "Test mission"
        assert data["done_criteria"] == ["Step 1", "Step 2"]
        print("PASS test_mission_create_writes_valid_state")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_mission_show_returns_valid_json() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        _get("write_mission")(_get("empty_mission")())
        mission = _get("load_mission")()
        assert isinstance(mission, dict)
        assert mission["version"] == 1
        print("PASS test_mission_show_returns_valid_json")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_mission_stop_changes_status() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        _get("write_mission")(mission)

        mission = _get("load_mission")()
        mission["status"] = "stopped"
        mission["next_action"] = "Mission stopped by operator."
        _get("write_mission")(mission)

        assert _get("load_mission")()["status"] == "stopped"
        print("PASS test_mission_stop_changes_status")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_creates_subgoal_when_active_free_empty() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Improve test fixture"
        mission["done_criteria"] = ["Step 1 done"]
        _get("write_mission")(mission)

        # Queue is empty
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is not None
        assert "title" in subgoal
        assert "goal" in subgoal
        assert "executor" in subgoal
        assert "subgoal_number" in subgoal
        assert subgoal["subgoal_number"] == 1
        print("PASS test_generator_creates_subgoal_when_active_free_empty")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_refuses_when_mission_not_active() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "idle"
        mission["umbrella_objective"] = "Should not generate"
        _get("write_mission")(mission)

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is None
        print("PASS test_generator_refuses_when_mission_not_active")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_refuses_when_max_subgoals_reached() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Should not generate"
        mission["done_criteria"] = ["Step 1", "Step 2"]
        mission["generated_count"] = 20
        mission["max_subgoals"] = 20
        _get("write_mission")(mission)

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is None
        print("PASS test_generator_refuses_when_max_subgoals_reached")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_refuses_when_failure_streak_too_high() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Should not generate"
        mission["done_criteria"] = ["Step 1"]
        mission["failure_streak"] = _get("MAX_FAILURE_STREAK")
        _get("write_mission")(mission)

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is None
        print("PASS test_generator_refuses_when_failure_streak_too_high")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_refuses_when_active_subgoal_exists() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Should not generate"
        mission["done_criteria"] = ["Step 1"]
        mission["active_subgoal"] = {"title": "Already waiting"}
        _get("write_mission")(mission)

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is None
        print("PASS test_generator_refuses_when_active_subgoal_exists")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_refuses_when_queue_has_work() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Should not generate"
        mission["done_criteria"] = ["Step 1"]
        _get("write_mission")(mission)

        # Queue has a running item
        _get("write_queue")(
            {
                "contract": "local_node1_goal_queue.v1",
                "items": [{"id": "test1", "status": "running"}],
            }
        )

        assert _get("queue_has_active_work")() is True
        print("PASS test_generator_refuses_when_queue_has_work")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_auto_enqueue_creates_one_queued_item() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test enqueue"
        mission["done_criteria"] = ["Step 1"]
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is not None

        result = _get("auto_enqueue_subgoal")(mission, subgoal)
        assert result is not None
        assert result["status"] == "queued"
        assert result.get("mission_id") == "auto"

        # Mission state updated
        m = _get("load_mission")()
        assert m["active_subgoal"] is not None
        assert m["generated_count"] == 1

        # Queue has exactly one item
        q = _get("load_queue")()
        assert len(q["items"]) == 1
        print("PASS test_auto_enqueue_creates_one_queued_item")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_accepted_subgoal_updates_completed_list() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test accept"
        mission["done_criteria"] = ["Step 1"]
        mission["active_subgoal"] = {
            "title": "[Mission subgoal 1] Step 1",
            "queue_item_id": "test-qid",
            "subgoal_number": 1,
        }
        _get("write_mission")(mission)

        _get("mission_on_accepted_subgoal")(mission, "test-qid")

        m = _get("load_mission")()
        assert len(m["completed_subgoals"]) == 1
        assert m["active_subgoal"] is None
        assert m["failure_streak"] == 0
        assert m["status"] == "complete"  # done criteria satisfied
        print("PASS test_accepted_subgoal_updates_completed_list")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_repeated_failures_set_blocked() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test block"
        mission["done_criteria"] = ["Step 1"]
        mission["failure_streak"] = _get("MAX_FAILURE_STREAK") - 1

        for i in range(_get("MAX_FAILURE_STREAK")):
            mission["active_subgoal"] = {
                "title": f"[Mission subgoal {i + 1}] Step 1",
                "queue_item_id": f"test-qid-{i}",
                "subgoal_number": i + 1,
            }
            _get("mission_on_failed_subgoal")(mission, f"Failure {i + 1}")
            m = _get("load_mission")()

        assert m["status"] == "blocked"
        assert m["failure_streak"] >= _get("MAX_FAILURE_STREAK")
        assert len(m["failed_subgoals"]) >= _get("MAX_FAILURE_STREAK")
        print("PASS test_repeated_failures_set_blocked")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_max_subgoals_sets_complete() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test max"
        mission["done_criteria"] = ["Step 1", "Step 2", "Step 3"]
        # 20 subgoals generated (the max), last one is active
        mission["generated_count"] = 20
        mission["max_subgoals"] = 20
        mission["active_subgoal"] = {
            "title": "[Mission subgoal 20] Step 1",
            "queue_item_id": "test-qid-20",
            "subgoal_number": 20,
        }
        _get("write_mission")(mission)

        _get("mission_on_accepted_subgoal")(mission, "test-qid-20")

        m = _get("load_mission")()
        # Max subgoals reached: generated_count(20) >= max_subgoals(20)
        assert m["status"] == "complete"
        print("PASS test_max_subgoals_sets_complete")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_mission_try_generate_and_enqueue_all_conditions() -> None:
    """Test the full guard chain: active, free, empty queue, no active subgoal."""
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        # Build a fake idle status
        idle_status = {
            "verdict": "idle",
            "tmux_running": False,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
        }

        # Active mission, empty queue
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test full chain"
        mission["done_criteria"] = ["Step 1"]
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        result = _get("mission_try_generate_and_enqueue")(mission, idle_status)
        assert result is not None
        assert result["status"] == "queued"

        m = _get("load_mission")()
        assert m["active_subgoal"] is not None
        assert m["generated_count"] == 1
        print("PASS test_mission_try_generate_and_enqueue_all_conditions")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_node1_is_free() -> None:
    idle_status = {"verdict": "idle", "tmux_running": False, "vllm": {"running": 0}}
    accepted_status = {
        "verdict": "working",
        "tmux_running": False,
        "accepted": True,
        "vllm": {"running": 0},
    }
    working_status = {
        "verdict": "working",
        "tmux_running": True,
        "vllm": {"running": 1},
    }

    assert _get("node1_is_free")(idle_status) is True
    assert _get("node1_is_free")(accepted_status) is True
    assert _get("node1_is_free")(working_status) is False
    print("PASS test_node1_is_free")


def test_queue_has_active_work() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        # Empty queue
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})
        assert _get("queue_has_active_work")() is False

        # Queue with running item
        _get("write_queue")(
            {
                "contract": "local_node1_goal_queue.v1",
                "items": [{"id": "test", "status": "running"}],
            }
        )
        assert _get("queue_has_active_work")() is True

        # Queue with only accepted items
        _get("write_queue")(
            {
                "contract": "local_node1_goal_queue.v1",
                "items": [{"id": "test", "status": "accepted"}],
            }
        )
        assert _get("queue_has_active_work")() is False
        print("PASS test_queue_has_active_work")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_reconcile_mission_with_queue_accepts_active_subgoal() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test reconcile"
        mission["done_criteria"] = ["Step 1"]
        mission["active_subgoal"] = {
            "title": "[Mission subgoal 1] Step 1",
            "queue_item_id": "test-qid",
            "subgoal_number": 1,
        }
        _get("write_mission")(mission)
        _get("write_queue")(
            {
                "contract": "local_node1_goal_queue.v1",
                "items": [{"id": "test-qid", "status": "accepted"}],
            }
        )

        result = _get("reconcile_mission_with_queue")()
        assert result is not None
        assert result["event"] == "accepted"
        m = _get("load_mission")()
        assert m["status"] == "complete"
        assert m["active_subgoal"] is None
        assert len(m["completed_subgoals"]) == 1
        print("PASS test_reconcile_mission_with_queue_accepts_active_subgoal")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_generator_skips_completed_done_criteria() -> None:
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Test criterion ordering"
        mission["done_criteria"] = ["First criterion", "Second criterion"]
        mission["generated_count"] = 1
        mission["completed_subgoals"] = [
            {
                "title": "[Mission subgoal 1] First criterion",
                "queue_item_id": "qid-1",
                "subgoal_number": 1,
                "criterion_index": 0,
                "criterion": "First criterion",
            }
        ]
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        subgoal = _get("generate_subgoal")(mission)
        assert subgoal is not None
        assert "Second criterion" in subgoal["title"]
        assert subgoal["criterion_index"] == 1
        assert subgoal["criterion"] == "Second criterion"
        print("PASS test_generator_skips_completed_done_criteria")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_live_state_not_modified_by_mission_tests() -> None:
    try:
        _assert_live_state_unchanged()
        print("PASS test_live_state_not_modified_by_mission_tests")
    finally:
        _restore_paths()
        _restore_live_state()


# ---------------------------------------------------------------------------
# Subgoal 6 — targeted tests proving the production-executor path
# ---------------------------------------------------------------------------


def test_classify_useful_execution_rejects_report_only() -> None:
    """Report-only completion markers are classified as NOT useful."""
    classify = _get("classify_useful_execution")

    # Dashboard / report-only markers
    for marker in [
        {"summary": "Created a new dashboard"},
        {"summary": "report-only artifact"},
        {"verification": ["alert system installed"]},
        {"summary": "policy note added"},
        {"summary": "artifact gallery updated"},
        {"summary": "sparkline chart added"},
    ]:
        result = classify(marker)
        assert result["useful"] is False, (
            f"Expected report-only to be rejected, got: {result} for {marker}"
        )
    print("PASS test_classify_useful_execution_rejects_report_only")


def test_classify_useful_execution_accepts_production_work() -> None:
    """Production work with evidence is classified as useful."""
    classify = _get("classify_useful_execution")

    for marker in [
        {"summary": "file changed: fixed broken link", "files_changed": ["index.html"]},
        {
            "summary": "before/after: repaired stale claim",
            "verification": ["pytest passed"],
        },
        {"verification": ["py_compile ok", "test pass"]},
        {"summary": "deployed maintenance fix"},
        {"summary": "installed capability: test passed"},
        {
            "summary": "installed capability: files changed and test passed",
            "verification": [
                "node --check assets/dashboard.js returned OK",
                "production_executor.py reported repair_queue_slop_filter OK",
            ],
        },
    ]:
        result = classify(marker)
        assert result["useful"] is True, (
            f"Expected production work to be accepted, got: {result} for {marker}"
        )
    print("PASS test_classify_useful_execution_accepts_production_work")


def test_classify_useful_execution_empty_marker() -> None:
    """Empty or missing markers return None (unknown)."""
    classify = _get("classify_useful_execution")
    result = classify({})
    assert result["useful"] is None
    result2 = classify({"status": "complete", "summary": "done"})
    assert result2["useful"] is None
    print("PASS test_classify_useful_execution_empty_marker")


def test_manager_completion_classifier_ignores_path_fragments() -> None:
    """Manager review should not reject useful work because filenames contain indicator words."""
    classify = manager_ns["classify_completion_marker"]
    marker = {
        "status": "complete",
        "summary": "installed capability: files changed and test passed",
        "verification": [
            "node --check assets/dashboard.js returned OK",
            "production_executor.py reported repair_queue_slop_filter OK",
        ],
    }
    result = classify(marker)
    assert result["report_only"] is False
    assert result["has_useful"] is True
    assert result["has_slop"] is False

    report_only = classify({"verification": ["alert system installed"]})
    assert report_only["report_only"] is True
    assert report_only["has_useful"] is False
    print("PASS test_manager_completion_classifier_ignores_path_fragments")


def test_manager_writes_ticket_and_evidence_bundle() -> None:
    """Ticket/evidence helpers create the expected per-run artifacts."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        session_log = run_dir / "session.log"
        old_session_log = manager_ns["SESSION_LOG"]
        old_git_status_lines = manager_ns["git_status_lines"]
        manager_ns["SESSION_LOG"] = session_log
        manager_ns["git_status_lines"] = lambda limit=500: [
            " M index.html",
            " M shared.py",
            "?? new-file.md",
        ][:limit]
        (run_dir / "prompt.md").write_text(
            "Fix index.html and run pytest", encoding="utf-8"
        )
        (run_dir / "start-git-status.txt").write_text(
            "# Start Git Status\n\n```text\n M shared.py\n M old-only.md\n```\n",
            encoding="utf-8",
        )
        session_log.write_text(
            "$ python3 -m pytest tests/test_example.py\n"
            "OPENAI_API_KEY=should-not-leak python3 script.py\n"
            "normal prose line\n",
            encoding="utf-8",
        )
        try:
            ticket = manager_ns["ensure_ticket"](
                run_dir,
                title="Ticket Smoke",
                goal_text="Fix index.html and verify with pytest",
                executor="opencode",
                planner="none",
            )
            assert ticket is not None and ticket.exists()
            payload = json.loads(ticket.read_text())
            assert payload["contract"] == "local_node1_goal_ticket.v1"
            assert payload["ticket_id"] == run_dir.name

            artifacts = manager_ns["write_evidence_bundle"](
                run_dir,
                status={
                    "changed_files": [" M index.html"],
                    "complete_marker_path": "/tmp/complete.json",
                    "complete_marker": {"classification": "installed capability"},
                    "accepted": False,
                },
                verification=["pytest passed"],
                checks=[{"name": "example", "ok": True, "detail": "passed"}],
                review={
                    "status": "accepted",
                    "ok": True,
                    "complete_marker_sha256": "abc",
                    "changed_file_count": 1,
                },
            )
            for key in (
                "changed_files",
                "commands_log",
                "context_map",
                "diff_summary",
                "end_git_status",
                "verification_results",
                "final_result",
                "owned_changes",
                "progress_ledger",
                "review_gaps",
                "suggested_verification",
                "start_git_status",
                "ticket",
            ):
                assert key in artifacts
                assert Path(artifacts[key]).exists()
            context_text = Path(artifacts["context_map"]).read_text()
            assert "## Candidate Context Files" in context_text
            assert "index.html" in context_text
            command_text = Path(artifacts["commands_log"]).read_text()
            assert "$ python3 -m pytest tests/test_example.py" in command_text
            assert "OPENAI_API_KEY=<redacted>" in command_text
            assert "should-not-leak" not in command_text
            ownership_text = Path(artifacts["owned_changes"]).read_text()
            assert "## created_by_run" in ownership_text
            assert "`index.html`" in ownership_text
            assert "`new-file.md`" in ownership_text
            assert "## pre_existing_dirty" in ownership_text
            assert "`shared.py`" in ownership_text
            assert "## unrelated_dirty" in ownership_text
            assert "`old-only.md`" in ownership_text
            suggested_text = Path(artifacts["suggested_verification"]).read_text()
            assert "# Suggested Verification" in suggested_text
            assert "`not seen` browser_or_site_smoke" in suggested_text
            gaps_text = Path(artifacts["review_gaps"]).read_text()
            assert "# Review Gaps" in gaps_text
            assert "Acceptability: `acceptable_with_gaps`" in gaps_text
            assert "## Suggested Checks Not Seen" in gaps_text
        finally:
            manager_ns["SESSION_LOG"] = old_session_log
            manager_ns["git_status_lines"] = old_git_status_lines
    print("PASS test_manager_writes_ticket_and_evidence_bundle")


def test_manager_ticket_validation_rejects_vague_or_unsafe_tickets() -> None:
    validate = manager_ns["validate_ticket"]
    good = {
        "contract": "local_node1_goal_ticket.v1",
        "title": "Repair site navigation",
        "source_goal": "Repair the site navigation links and verify with a browser smoke test.",
        "problem_statement": "Navigation links are inconsistent and need a bounded repair.",
        "allowed_paths": ["/mnt/raid0/documentation"],
        "concrete_path_hints": [
            "/mnt/raid0/documentation/reference/LOCAL_NODE1_GOAL_HARNESS_QUICKREF.md"
        ],
        "forbidden_paths": [".env", ".secrets", "credentials", "tokens"],
        "done_criteria": ["Changed files are reviewed", "Verification passes"],
        "requires_secret_access": False,
        "requires_restart": False,
    }
    assert validate(good)["ok"] is True

    vague = dict(good)
    vague["title"] = "misc"
    vague["source_goal"] = "fix stuff"
    vague["problem_statement"] = "do things"
    assert validate(vague)["ok"] is False

    unsafe = dict(good)
    unsafe["requires_secret_access"] = True
    assert validate(unsafe)["ok"] is False
    print("PASS test_manager_ticket_validation_rejects_vague_or_unsafe_tickets")


def test_auto_generate_continues_without_manual_feeding() -> None:
    """After one subgoal is accepted, the next is auto-generated without
    any manual enqueue — proving the mission does not rely on manual subgoal feeding.

    Simulates the real flow: queue item transitions to 'accepted', then
    reconcile_mission_with_queue finds it and calls mission_on_accepted_subgoal,
    then the monitor loop auto-generates the next subgoal.
    """
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        # Create a 3-criterion mission
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Fix three issues"
        mission["done_criteria"] = [
            "Fix issue A",
            "Fix issue B",
            "Fix issue C",
        ]
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        idle_status = {
            "verdict": "idle",
            "tmux_running": False,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
        }

        # Step 1: auto-generate and enqueue subgoal 1
        result1 = _get("mission_try_generate_and_enqueue")(mission, idle_status)
        assert result1 is not None
        assert "Fix issue A" in result1["title"]
        assert result1["status"] == "queued"
        assert result1.get("mission_id") == "auto"

        m = _get("load_mission")()
        assert m["generated_count"] == 1
        assert m["active_subgoal"] is not None

        # Step 2: simulate the queue item being accepted (real flow)
        q = _get("load_queue")()
        q["items"][0]["status"] = "accepted"
        _get("write_queue")(q)

        # Step 3: reconcile picks up the accepted item and updates mission
        reconcile_result = _get("reconcile_mission_with_queue")()
        assert reconcile_result is not None
        assert reconcile_result["event"] == "accepted"

        m = _get("load_mission")()
        assert len(m["completed_subgoals"]) == 1
        assert m["active_subgoal"] is None

        # Step 4: auto-generate subgoal 2 — no manual feeding
        result2 = _get("mission_try_generate_and_enqueue")(m, idle_status)
        assert result2 is not None
        assert result2["status"] == "queued"
        assert "Fix issue B" in result2.get("title", "")

        m = _get("load_mission")()
        assert m["generated_count"] == 2
        assert m["active_subgoal"] is not None

        # Step 5: accept subgoal 2, auto-generate subgoal 3
        q = _get("load_queue")()
        q["items"][1]["status"] = "accepted"
        _get("write_queue")(q)

        reconcile_result = _get("reconcile_mission_with_queue")()
        assert reconcile_result["event"] == "accepted"

        m = _get("load_mission")()
        result3 = _get("mission_try_generate_and_enqueue")(m, idle_status)
        assert result3 is not None
        assert "Fix issue C" in result3.get("title", "")

        print("PASS test_auto_generate_continues_without_manual_feeding")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_supervisor_state_includes_useful_execution_field() -> None:
    """write_supervisor_state includes useful_execution classification."""
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        # Mock status with no tmux
        status = {
            "verdict": "idle",
            "tmux_running": False,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "complete_marker_path": "",
        }
        payload = _get("write_supervisor_state")(status)
        assert "useful_execution" in payload
        assert "useful" in payload["useful_execution"]
        assert "reason" in payload["useful_execution"]
        print("PASS test_supervisor_state_includes_useful_execution_field")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_mission_monitor_auto_generates_when_queue_empty_and_node_free() -> None:
    """mission-monitor command generates and enqueues the next subgoal
    when the queue is empty and Node1 is free — proving automatic continuation."""
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        # Active mission, one criterion completed
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "Two-step mission"
        mission["done_criteria"] = ["Step 1", "Step 2"]
        mission["generated_count"] = 1
        mission["completed_subgoals"] = [
            {
                "title": "[Mission subgoal 1] Step 1",
                "queue_item_id": "qid-1",
                "subgoal_number": 1,
                "criterion_index": 0,
                "criterion": "Step 1",
            }
        ]
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        # Simulate the monitor flow: reconcile then generate
        reconcile_result = _get("reconcile_mission_with_queue")()
        assert reconcile_result is None  # nothing to reconcile

        m = _get("load_mission")()
        idle_status = {
            "verdict": "idle",
            "tmux_running": False,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
        }
        result = _get("mission_try_generate_and_enqueue")(m, idle_status)
        assert result is not None
        assert "Step 2" in result.get("title", "")
        assert result["status"] == "queued"

        m = _get("load_mission")()
        assert m["generated_count"] == 2
        assert m["active_subgoal"] is not None
        print("PASS test_mission_monitor_auto_generates_when_queue_empty_and_node_free")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_mission_monitor_json_is_machine_readable_when_inactive() -> None:
    """mission-monitor --json must not print human text before JSON."""
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "complete"
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        class Args:
            json = True
            dry_run = True

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = _get("cmd_mission_monitor")(Args())

        assert rc == 0
        payload = json.loads(out.getvalue())
        assert payload == {"status": "skipped", "reason": "mission not active"}
        print("PASS test_mission_monitor_json_is_machine_readable_when_inactive")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_empty_done_criteria_uses_implicit_parts_once() -> None:
    """No-criteria missions should not repeat the first umbrella phrase forever."""
    mp = _temp_mission_path()
    qp = _temp_queue_path()
    _patch_paths(mp, qp)
    try:
        mission = _get("empty_mission")()
        mission["status"] = "active"
        mission["umbrella_objective"] = "fix docs and harden cleanup"
        mission["done_criteria"] = []
        _get("write_mission")(mission)
        _get("write_queue")({"contract": "local_node1_goal_queue.v1", "items": []})

        idle_status = {
            "verdict": "idle",
            "tmux_running": False,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
        }

        first = _get("mission_try_generate_and_enqueue")(mission, idle_status)
        assert first is not None
        assert "fix docs" in first["title"]

        q = _get("load_queue")()
        q["items"][0]["status"] = "accepted"
        _get("write_queue")(q)
        assert _get("reconcile_mission_with_queue")()["event"] == "accepted"

        second = _get("mission_try_generate_and_enqueue")(
            _get("load_mission")(), idle_status
        )
        assert second is not None
        assert "harden cleanup" in second["title"]

        q = _get("load_queue")()
        q["items"][1]["status"] = "accepted"
        _get("write_queue")(q)
        result = _get("reconcile_mission_with_queue")()
        assert result["event"] == "accepted"
        assert result["mission_status"] == "complete"

        complete = _get("load_mission")()
        assert complete["status"] == "complete"
        assert "implicit mission slices" in complete["next_action"]
        assert (
            _get("mission_try_generate_and_enqueue")(complete, idle_status) is None
        )
        print("PASS test_empty_done_criteria_uses_implicit_parts_once")
    finally:
        _restore_paths()
        _cleanup([mp, qp])


def test_write_progress_ledger_creates_artifact() -> None:
    """write_progress_ledger generates a valid progress-ledger.md artifact."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        session_log = run_dir / "session.log"
        old_session_log = manager_ns["SESSION_LOG"]
        manager_ns["SESSION_LOG"] = session_log
        session_log.write_text(
            "$ python3 -m pytest tests/test_example.py\n"
            "$ python3 -m py_compile script.py\n",
            encoding="utf-8",
        )
        # Create owned-changes.md with some content
        (run_dir / "owned-changes.md").write_text(
            "# Owned Changes\n\n"
            "## created_by_run\n"
            "- `new-file.md`\n\n"
            "## modified_by_run\n"
            "- `index.html`\n\n",
            encoding="utf-8",
        )
        # Create suggested-verification.md with a gap
        (run_dir / "suggested-verification.md").write_text(
            "# Suggested Verification\n\n"
            "- `seen` py_compile\n"
            "- `not seen` browser_or_site_smoke\n",
            encoding="utf-8",
        )
        # Create ticket.json
        ticket_path = run_dir / "ticket.json"
        ticket_path.write_text(
            json.dumps(
                {
                    "contract": "local_node1_goal_ticket.v1",
                    "title": "Progress Ledger Test",
                    "problem_statement": "Test that progress ledger is generated correctly.",
                }
            ),
            encoding="utf-8",
        )
        try:
            ledger_path = manager_ns["write_progress_ledger"](
                run_dir,
                current_objective="Test progress ledger generation",
                ticket={
                    "title": "Progress Ledger Test",
                    "problem_statement": "Test generation",
                },
                checks=[
                    {"name": "check_a", "ok": True, "detail": "passed"},
                    {"name": "check_b", "ok": False, "detail": "failed"},
                ],
                command_count=2,
            )
            assert ledger_path.exists()
            text = ledger_path.read_text()
            # Core sections present
            assert "# Progress Ledger" in text
            assert "## Current Objective" in text
            assert "Test progress ledger generation" in text
            assert "## Ticket" in text
            assert "Progress Ledger Test" in text
            assert "## Completion Summary" in text
            assert "1 of 2 review checks passed" in text
            assert "## Review Check Statuses" in text
            assert "`PASS` check_a" in text
            assert "`FAIL` check_b" in text
            assert "## Suggested Verification" in text
            assert "gaps_present" in text
            assert "## Owned-Change Confidence" in text
            assert "## Command Count" in text
            assert "Commands executed: `2`" in text
            assert "## Next Action" in text
            # Next action should reference failed checks
            assert "check_b" in text
        finally:
            manager_ns["SESSION_LOG"] = old_session_log
    print("PASS test_write_progress_ledger_creates_artifact")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_empty_mission_has_required_fields,
        test_mission_create_writes_valid_state,
        test_mission_show_returns_valid_json,
        test_mission_stop_changes_status,
        test_generator_creates_subgoal_when_active_free_empty,
        test_generator_refuses_when_mission_not_active,
        test_generator_refuses_when_max_subgoals_reached,
        test_generator_refuses_when_failure_streak_too_high,
        test_generator_refuses_when_active_subgoal_exists,
        test_generator_refuses_when_queue_has_work,
        test_auto_enqueue_creates_one_queued_item,
        test_accepted_subgoal_updates_completed_list,
        test_repeated_failures_set_blocked,
        test_max_subgoals_sets_complete,
        test_mission_try_generate_and_enqueue_all_conditions,
        test_node1_is_free,
        test_queue_has_active_work,
        test_reconcile_mission_with_queue_accepts_active_subgoal,
        test_generator_skips_completed_done_criteria,
        test_live_state_not_modified_by_mission_tests,
        # Subgoal 6 — production-executor path verification
        test_classify_useful_execution_rejects_report_only,
        test_classify_useful_execution_accepts_production_work,
        test_classify_useful_execution_empty_marker,
        test_manager_completion_classifier_ignores_path_fragments,
        test_manager_writes_ticket_and_evidence_bundle,
        test_manager_ticket_validation_rejects_vague_or_unsafe_tickets,
        test_auto_generate_continues_without_manual_feeding,
        test_supervisor_state_includes_useful_execution_field,
        test_mission_monitor_auto_generates_when_queue_empty_and_node_free,
        test_mission_monitor_json_is_machine_readable_when_inactive,
        test_empty_done_criteria_uses_implicit_parts_once,
        test_write_progress_ledger_creates_artifact,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed else 0)
