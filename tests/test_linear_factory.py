from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from agentic_harness.integrations import linear_factory as factory


def issue(
    *,
    identifier: str = "AI-10",
    labels: tuple[str, ...] = ("agent-ready",),
    assignee: dict[str, str] | None = None,
    state_type: str = "unstarted",
    description: str | None = None,
) -> dict[str, Any]:
    body = description or """## Requested outcome

Ship the exact useful behavior.
Target repository: https://github.com/moortekweb-art/agentic-harness

## Acceptance criteria

- AC-1: The behavior works.
- AC-2: Direct evidence proves it.

## Non-goals

- NG-1: Do not merge.

## Verification requirements

1. Run the focused tests.

## Approval boundary

Michael applies agent-ready. Humans merge.
"""
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": "Factory test",
        "description": body,
        "url": f"https://linear.example/{identifier}",
        "priority": 2,
        "state": {"id": "todo", "name": "Todo", "type": state_type},
        "assignee": assignee,
        "labels": {
            "nodes": [
                {"id": f"label-{name}", "name": name}
                for name in labels
            ]
        },
        "history": {
            "nodes": [
                {
                    "actorId": "viewer",
                    "addedLabelIds": ["ready"],
                    "updatedDescription": False,
                    "createdAt": "2026-07-23T12:00:00Z",
                }
            ]
        },
        "comments": {"nodes": []},
        "relations": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }


def workspace() -> dict[str, Any]:
    return {
        "viewer": {"id": "viewer", "name": "Michael"},
        "team": {
            "id": "team",
            "key": "AI",
            "states": {
                "nodes": [
                    {"id": "todo", "name": "Todo", "type": "unstarted"},
                    {"id": "progress", "name": "In Progress", "type": "started"},
                    {"id": "review", "name": "In Review", "type": "started"},
                    {"id": "done", "name": "Done", "type": "completed"},
                ]
            },
            "labels": {
                "nodes": [
                    {"id": "draft", "name": "spec-drafted"},
                    {"id": "ready", "name": "agent-ready"},
                    {"id": "blocked", "name": "blocked"},
                ]
            },
        },
    }


class FakeLinear:
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues

    def workspace(self, _team: str) -> dict[str, Any]:
        return workspace()

    def eligible_issues(self, _team: str, _label: str) -> list[dict[str, Any]]:
        return self.issues

    def active_builder_issues(self, _team: str, _label: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.issues
            if row.get("assignee")
            and row["state"]["type"] == "started"
            and "blocked"
            not in {item["name"] for item in row["labels"]["nodes"]}
        ]


def config(tmp_path: Path) -> factory.FactoryConfig:
    adapter = tmp_path / "herdr.py"
    adapter.write_text("# adapter\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    return factory.FactoryConfig(
        team="AI",
        state_root=tmp_path / "state",
        repo_map={"moortekweb-art/agentic-harness": repo},
        worker="codex",
        herdr_adapter=adapter,
        github_owner="moortekweb-art",
    )


def test_contract_is_stable_and_extracts_binding_ids() -> None:
    parsed = factory.validate_contract(issue())
    assert parsed["name_with_owner"] == "moortekweb-art/agentic-harness"
    assert parsed["acceptance_ids"] == ["AC-1", "AC-2"]
    assert parsed["non_goal_ids"] == ["NG-1"]
    assert parsed["spec_sha256"] == factory.spec_sha256(parsed["description"])


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("## Acceptance criteria\n\nNo stable identifiers.", "unique AC-N"),
        ("## Non-goals\n\nNo stable identifiers.", "unique NG-N"),
        ("Target repository: local-only", "GitHub repository"),
    ],
)
def test_contract_fails_closed(replacement: str, message: str) -> None:
    body = issue()["description"]
    if replacement.startswith("Target"):
        body = body.replace(
            "Target repository: https://github.com/moortekweb-art/agentic-harness",
            replacement,
        )
    elif replacement.startswith("## Acceptance"):
        body = body.replace(
            "## Acceptance criteria\n\n- AC-1: The behavior works.\n"
            "- AC-2: Direct evidence proves it.",
            replacement,
        )
    else:
        body = body.replace("## Non-goals\n\n- NG-1: Do not merge.", replacement)
    with pytest.raises(factory.FactoryError, match=message):
        factory.validate_contract(issue(description=body))


def test_dependency_blocked_until_upstream_done() -> None:
    row = issue()
    row["inverseRelations"]["nodes"] = [
        {
            "type": "blocks",
            "issue": {"identifier": "AI-9", "state": {"type": "started"}},
        }
    ]
    assert factory.is_dependency_blocked(row) is True
    row["inverseRelations"]["nodes"][0]["issue"]["state"]["type"] = "completed"
    assert factory.is_dependency_blocked(row) is False


def test_human_approval_must_be_owner_applied_and_after_last_spec_change() -> None:
    row = issue()
    assert factory.human_approval_verified(
        row, ready_label_id="ready", viewer_id="viewer"
    ) == (True, "human_approval_verified")
    row["history"]["nodes"][0]["actorId"] = "automation"
    assert factory.human_approval_verified(
        row, ready_label_id="ready", viewer_id="viewer"
    )[1] == "agent_ready_not_applied_by_human_owner"
    row["history"]["nodes"][0]["actorId"] = "viewer"
    row["history"]["nodes"].append(
        {
            "actorId": "viewer",
            "addedLabelIds": [],
            "updatedDescription": True,
            "createdAt": "2026-07-23T12:01:00Z",
        }
    )
    assert factory.human_approval_verified(
        row, ready_label_id="ready", viewer_id="viewer"
    )[1] == "spec_changed_after_approval"


def test_hash_bound_owner_comment_is_auditable_approval_fallback() -> None:
    row = issue()
    row["history"]["nodes"] = []
    row["comments"]["nodes"] = [
        {
            "user": {"id": "viewer", "name": "Michael"},
            "body": f"Factory approval: {factory.spec_sha256(row['description'])}",
            "createdAt": "2026-07-23T12:00:00Z",
        }
    ]
    assert factory.human_approval_verified(
        row, ready_label_id="ready", viewer_id="viewer"
    ) == (True, "human_spec_hash_approval_verified")
    row["description"] += "\nChanged after approval.\n"
    assert factory.human_approval_verified(
        row, ready_label_id="ready", viewer_id="viewer"
    )[1] == "agent_ready_not_applied_by_human_owner"


@pytest.mark.parametrize(
    ("labels", "assignee", "state_type", "expected"),
    [
        ((), None, "unstarted", "missing_agent_ready"),
        (("agent-ready", "blocked"), None, "unstarted", "blocked_label"),
        (("agent-ready",), {"id": "worker"}, "started", "already_assigned"),
        (("agent-ready",), None, "completed", "terminal_state"),
        (("agent-ready",), None, "unstarted", "eligible"),
    ],
)
def test_eligibility_gates(
    tmp_path: Path,
    labels: tuple[str, ...],
    assignee: dict[str, str] | None,
    state_type: str,
    expected: str,
) -> None:
    assert factory._eligible(  # noqa: SLF001 - focused contract test
        issue(labels=labels, assignee=assignee, state_type=state_type),
        config(tmp_path),
    )[1] == expected


def test_dry_run_selects_without_claiming(tmp_path: Path) -> None:
    payload = factory.import_once(FakeLinear([issue()]), config(tmp_path), act=False)  # type: ignore[arg-type]
    assert payload["ok"] is True
    assert payload["action"] == "dry_run"
    assert payload["selected"] == "AI-10"
    assert not (tmp_path / "state" / "receipts").exists()


def test_active_builder_is_a_single_team_lock(tmp_path: Path) -> None:
    active = issue(assignee={"id": "viewer"}, state_type="started")
    payload = factory.import_once(FakeLinear([active]), config(tmp_path), act=True)  # type: ignore[arg-type]
    assert payload["action"] == "active_builder"
    assert payload["issues"] == ["AI-10"]


def test_blocked_assigned_issue_does_not_hold_builder_lock(tmp_path: Path) -> None:
    blocked = issue(
        labels=("agent-ready", "blocked"),
        assignee={"id": "viewer"},
        state_type="started",
    )
    ready = issue(identifier="AI-11")
    payload = factory.import_once(
        FakeLinear([blocked, ready]), config(tmp_path), act=False  # type: ignore[arg-type]
    )
    assert payload["action"] == "dry_run"
    assert payload["selected"] == "AI-11"


def test_successful_handoff_consumes_only_queue_approval_label() -> None:
    assert factory.consumed_label_ids(
        {
            "spec-drafted": "draft",
            "agent-ready": "ready",
            "customer-visible": "customer",
        },
        "agent-ready",
    ) == ["draft", "customer"]


def test_terminal_receipt_prevents_duplicate_import(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    row = issue()
    parsed = factory.validate_contract(row)
    factory.atomic_write_json(
        factory._receipt_path(cfg, "AI-10"),  # noqa: SLF001
        {
            "contract": factory.CONTRACT,
            "identifier": "AI-10",
            "spec_sha256": parsed["spec_sha256"],
            "status": "merge_ready",
        },
    )
    payload = factory.import_once(FakeLinear([row]), cfg, act=False)  # type: ignore[arg-type]
    assert payload["selected"] is None
    assert payload["candidates"][0]["reason"] == "terminal_receipt_exists"


def test_intake_draft_never_adds_agent_ready(tmp_path: Path) -> None:
    args = argparse.Namespace(
        idea="small factory",
        outcome="A draft issue is created.",
        context="Keep humans in control.",
        acceptance=["The issue has AC identifiers."],
        non_goal=["Do not merge."],
        verification=["Inspect the created issue."],
        human_step=["Apply agent-ready after review."],
    )
    rendered = factory.render_spec(
        args, "https://github.com/moortekweb-art/agentic-harness"
    )
    assert "spec-drafted" not in rendered
    assert "Build must not start until Michael applies `agent-ready`" in rendered
    assert "Agents never merge or enable auto-merge" in rendered


def test_browser_gate_fails_closed_for_ui_without_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINEAR_FACTORY_BROWSER_VERIFY_CMD", raising=False)
    payload = factory._browser_gate(  # noqa: SLF001
        tmp_path, {"number": 7, "headRefOid": "a" * 40}, ["agentic_harness/gui/static/app.js"]
    )
    assert payload == {
        "required": True,
        "passed": False,
        "reason": "browser_verifier_not_configured",
    }


def test_non_ui_change_does_not_invent_browser_evidence(tmp_path: Path) -> None:
    payload = factory._browser_gate(  # noqa: SLF001
        tmp_path, {"number": 7, "headRefOid": "a" * 40}, ["docs/README.md"]
    )
    assert payload == {
        "required": False,
        "passed": True,
        "reason": "no_ui_files_changed",
    }
