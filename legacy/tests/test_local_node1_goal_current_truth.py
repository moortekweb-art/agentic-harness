#!/usr/bin/env python3
"""Tests for the local Node1 goal current-truth adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPTS_DIR = (
    "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts"
)
_CURRENT_TRUTH_PATH = Path(_SCRIPTS_DIR) / "local-node1-goal-current-truth.py"

_spec = importlib.util.spec_from_file_location(
    "local_node1_goal_current_truth", str(_CURRENT_TRUTH_PATH)
)
current_truth = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(current_truth)


def test_build_status_promotes_commands_lanes_and_latest_audit(tmp_path, monkeypatch):
    audit_path = tmp_path / "integration-audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-06-27T10:00:00Z",
                "ok": True,
                "status": "integrated",
                "missing": [],
                "artifact_paths": {"markdown": "/tmp/audit.md"},
                "checks": [
                    {
                        "name": "dry_run_route_checks",
                        "routes": [
                            {
                                "expected_intent": "external-review",
                                "command": "local-goal external-review --reviewer glm-5.2",
                            },
                            {
                                "expected_intent": "mission-create",
                                "command": "local-goal mission-create --goal example",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(current_truth, "INTEGRATION_AUDIT_STATE", audit_path)

    supervisor_status = {
        "_returncode": 0,
        "classification": "working",
        "node1_is_idle": False,
        "node1_is_idle_scope": "legacy: local-goal lane availability, not raw vLLM/GPU idleness",
        "local_goal_lane_free": False,
        "node1_vllm_idle": False,
        "node1_vllm_has_other_activity": True,
        "runtime": {"vllm": {"running": 1.0, "waiting": 0.0}},
        "recommended_action": "Node1 vLLM is actively working.",
        "active_goal": {
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "objective": "test goal",
            "run_dir": "/tmp/run",
            "planner": "none",
            "executor": "opencode",
            "current_subgoal": {"subgoal_number": 2},
        },
        "commands": {
            "external_review": "local-goal external-review --reviewer glm-5.2",
            "integration_audit": "local-goal integration-audit",
            "mission_show": "local-goal mission-show",
        },
        "capabilities": {
            "lanes": {
                "local": {
                    "classification": "installed_capability",
                    "installed": True,
                    "available_now": False,
                    "availability_reason": "node1_not_free",
                    "executor": "opencode",
                },
                "cloud_executor": {
                    "classification": "installed_capability",
                    "installed": True,
                    "available_now": True,
                    "availability_reason": "ready",
                    "builder": "Hermes worker_dispatch via terminal-worker-runner",
                    "default_executor_worker": "opencode-kimi-build",
                    "executor_workers": ["opencode-glm-build", "opencode-kimi-build"],
                    "runner_path": "/tmp/terminal-worker-runner.py",
                    "runner_present": True,
                    "notes": ["review/acceptance still gates completion"],
                },
            }
        },
        "queue": {"running": 1, "queued": 0},
    }
    mission = {
        "status": "active",
        "objective": "test goal",
        "completed_count": 1,
        "failed_count": 0,
        "rejected_count": 0,
        "max_subgoals": 8,
    }

    def fake_supervisor(*args: str):
        if args == ("status", "--json"):
            return supervisor_status
        if args == ("capabilities", "--json"):
            return {"lanes": {}}
        if args == ("mission-show", "--json"):
            return mission
        raise AssertionError(args)

    monkeypatch.setattr(current_truth, "run_supervisor", fake_supervisor)
    monkeypatch.setattr(
        current_truth,
        "run_local_goal",
        lambda *args: {
            "_returncode": 0,
            "contract": "local_node1_goal_model_promotion_decision.v1",
            "status": "ready-for-operator-decision",
            "mutates_live_service": False,
            "promotion_allowed": False,
            "decision_required": True,
            "operator_can_choose_promotion": True,
            "compare_verdict": "comparison_evidence_complete_needs_promotion_decision",
            "next_action": "Review the complete A/B evidence.",
            "promotion_allowed_meaning": "false means the harness will not promote automatically; it does not mean Ornith failed the A/B gate.",
            "approval_preview_command": "scripts/local-goal model-promotion-apply",
            "terminal_approval_command": "scripts/local-goal model-promotion-apply --execute --confirm PROMOTE_ORNITH_PERMANENT",
        },
    )
    monkeypatch.setattr(
        current_truth,
        "run_manager",
        lambda *args: {
            "_returncode": 0,
            "acceptance": {
                "status": "accepted",
                "active_run_dir": "/tmp/previous-run",
                "dirty_completion_ok": True,
                "disposition_summary": {
                    "dirty_completion_ok": True,
                    "blocking_count": 0,
                    "action_required_count": 12,
                    "human_required_count": 1,
                    "approval_required_count": 0,
                },
            },
        },
    )

    status = current_truth.build_status()

    assert status["ok"] is True
    assert status["local_goal_lane_free"] is False
    assert status["node1_vllm_idle"] is False
    assert status["node1_vllm_has_other_activity"] is True
    assert status["node1_vllm_running"] == 1.0
    assert status["node1_vllm_waiting"] == 0.0
    assert status["start_may_wait"] is False
    assert "local-goal lane is not free" in status["start_guidance"].lower()
    assert status["node1_capacity"]["node1_is_idle_scope"].startswith("legacy:")
    assert status["model_promotion_decision"]["promotion_allowed_meaning"] == (
        "false means the harness will not promote automatically; it does not mean Ornith failed the A/B gate."
    )
    assert status["acceptance"]["applies_to_active_run"] is False
    assert status["acceptance"]["context_note"] == "acceptance_belongs_to_previous_run"
    assert status["commands"]["external_review"].endswith("--reviewer glm-5.2")
    assert status["commands"]["current_truth"].endswith("current-truth")
    assert status["commands"]["shortcuts"].endswith("shortcuts")
    assert status["commands"]["last_run"].endswith("last-run")
    assert status["commands"]["queue_summary"].endswith("queue-summary")
    assert status["commands"]["doctor"].endswith("doctor")
    assert status["commands"]["doctor_json"].endswith("doctor --json")
    assert status["commands"]["completion_summary"].endswith("completion-summary")
    assert status["commands"]["soak_plan"].endswith("soak-plan")
    assert status["commands"]["quick_start_bounded"].endswith(
        'quick-start --goal "Describe one bounded task with expected change and verification"'
    )
    assert status["commands"]["hermes_start_bounded"] == (
        "/local-goal start local goal: Describe one bounded task with expected change and verification"
    )
    assert status["commands"]["model_status"].endswith("model-status")
    assert status["commands"]["model_eval_next"].endswith("model-eval-next")
    assert status["commands"]["model_promotion_decision"].endswith(
        "model-promotion-decision"
    )
    assert status["commands"]["model_promotion_plan"].endswith("model-promotion-plan")
    assert status["commands"]["model_promotion_apply_preview"].endswith(
        "model-promotion-apply"
    )
    assert status["commands"]["terminal_only_model_promotion_apply_execute"].endswith(
        "model-promotion-apply --execute --confirm PROMOTE_ORNITH_PERMANENT"
    )
    assert status["commands"]["model_promotion_waiver"].endswith(
        "model-promotion-waiver"
    )
    assert status["commands"]["model_decision_packet"].endswith("qwopus-packet")
    assert status["commands"]["qwopus_completion_risk"].endswith(
        "qwopus-completion-risk"
    )
    assert status["commands"]["qwopus_safe_harness"].endswith(
        'ask "is Qwopus safe to use for the harness?"'
    )
    assert status["commands"]["qwopus_192k_seq4"].endswith(
        'ask "can Qwopus handle 192k seq4?"'
    )
    assert status["commands"]["qwopus_window_check"].endswith("qwopus-window-check")
    assert status["commands"]["qwopus_window_next"].endswith("qwopus-window-next")
    assert status["commands"]["qwopus_window_open_preview"].endswith(
        "qwopus-window-open"
    )
    assert status["commands"]["qwopus_window_restore_preview"].endswith(
        "qwopus-window-restore"
    )
    assert status["commands"]["telegram_alias_progress"] == "/local_goal progress"
    assert status["commands"]["audit_health"].endswith(
        "scripts/local-goal audit-health"
    )
    assert status["commands"]["telegram_alias_can_accept"] == (
        "/node1-goal can I accept the local goal?"
    )
    assert status["commands"]["telegram_alias_model_promotion_apply_preview"] == (
        "/node1_goal model-promotion-apply"
    )
    assert status["dirty_operator_summary"]["dirty_completion_ok"] is True
    assert status["dirty_operator_summary"]["blocks_acceptance"] is False
    assert status["dirty_operator_summary"]["action_required_count"] == 12
    assert status["dirty_operator_summary"]["human_required_count"] == 1
    assert "non-blocking" in status["dirty_operator_summary"]["note"]
    assert status["lane_capabilities"]["cloud_executor"]["default_executor_worker"] == (
        "opencode-kimi-build"
    )
    assert status["lane_capabilities"]["cloud_executor"]["builder"] == (
        "Hermes worker_dispatch via terminal-worker-runner"
    )
    assert (
        status["lane_capabilities"]["cloud_executor"]["runner_path"]
        == "/tmp/terminal-worker-runner.py"
    )
    assert status["lane_capabilities"]["cloud_executor"]["runner_present"] is True
    assert status["lane_capabilities"]["cloud_executor"]["notes"] == [
        "review/acceptance still gates completion"
    ]
    assert status["integration_audit_latest"]["ok"] is True
    assert status["integration_audit_latest"]["dry_run_intents"] == [
        "external-review",
        "mission-create",
    ]
    assert status["model_promotion_decision"] == {
        "available": True,
        "status": "ready-for-operator-decision",
        "mutates_live_service": False,
        "promotion_allowed": False,
        "decision_required": True,
        "operator_can_choose_promotion": True,
        "compare_verdict": "comparison_evidence_complete_needs_promotion_decision",
        "next_action": "Review the complete A/B evidence.",
        "promotion_allowed_meaning": "false means the harness will not promote automatically; it does not mean Ornith failed the A/B gate.",
        "approval_preview_command": "scripts/local-goal model-promotion-apply",
        "terminal_approval_command": "scripts/local-goal model-promotion-apply --execute --confirm PROMOTE_ORNITH_PERMANENT",
    }


def test_compact_integration_audit_reports_missing_file(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(current_truth, "INTEGRATION_AUDIT_STATE", missing)

    audit = current_truth.compact_integration_audit()

    assert audit == {"available": False, "path": str(missing)}
