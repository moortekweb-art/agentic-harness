from __future__ import annotations

import argparse
import subprocess
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
                {
                    "id": {
                        "agent-ready": "ready",
                        "blocked": "blocked",
                        "spec-drafted": "draft",
                    }.get(name, f"label-{name}"),
                    "name": name,
                }
                for name in labels
            ]
        },
        "history": {
            "nodes": [
                {
                    "actorId": "human",
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

    def approval_evidence(self, issue_id: str) -> dict[str, Any]:
        row = next(item for item in self.issues if item["id"] == issue_id)
        return {"history": row["history"], "comments": row["comments"]}


class ActingFakeLinear(FakeLinear):
    def __init__(self, issues: list[dict[str, Any]], *, revoke_on_claim: bool = False) -> None:
        super().__init__(issues)
        self.revoke_on_claim = revoke_on_claim
        self.label_arguments: list[list[str] | None] = []
        self.comments: list[str] = []

    def issue_snapshot(self, issue_id: str) -> dict[str, Any]:
        return next(item for item in self.issues if item["id"] == issue_id)

    def update_issue(
        self,
        issue_id: str,
        *,
        assignee_id: str | None,
        state_id: str,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        row = self.issue_snapshot(issue_id)
        self.label_arguments.append(label_ids)
        row["assignee"] = (
            {"id": assignee_id, "name": "Factory"} if assignee_id else None
        )
        row["state"] = {
            "id": state_id,
            "name": "In Review" if state_id == "review" else "In Progress",
            "type": "started",
        }
        if self.revoke_on_claim and len(self.label_arguments) == 1:
            row["labels"]["nodes"] = [
                label
                for label in row["labels"]["nodes"]
                if label["name"] != "agent-ready"
            ]
        return row

    def add_label(self, issue_id: str, label_id: str) -> None:
        row = self.issue_snapshot(issue_id)
        if label_id not in {label["id"] for label in row["labels"]["nodes"]}:
            row["labels"]["nodes"].append({"id": label_id, "name": "blocked"})

    def remove_label(self, issue_id: str, label_id: str) -> None:
        row = self.issue_snapshot(issue_id)
        row["labels"]["nodes"] = [
            label for label in row["labels"]["nodes"] if label["id"] != label_id
        ]

    def comment(self, _issue_id: str, body: str) -> None:
        self.comments.append(body)


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
        human_approver_id="human",
    )


def test_contract_is_stable_and_extracts_binding_ids() -> None:
    parsed = factory.validate_contract(issue())
    assert parsed["name_with_owner"] == "moortekweb-art/agentic-harness"
    assert parsed["acceptance_ids"] == ["AC-1", "AC-2"]
    assert parsed["non_goal_ids"] == ["NG-1"]
    assert parsed["spec_sha256"] == factory.spec_sha256(parsed["description"])
    assert parsed["verification_command"] == ""


def test_contract_rejects_explicit_verification_command() -> None:
    body = issue()["description"].replace(
        "1. Run the focused tests.",
        "Run the focused tests.\n\nCommand: git diff --check",
    )
    with pytest.raises(factory.FactoryError, match="cannot contain executable"):
        factory.validate_contract(issue(description=body))


def test_contract_rejects_explicit_shell_verification_block() -> None:
    body = issue()["description"].replace(
        "1. Run the focused tests.",
        "Run the focused tests.\n\n```sh\ngit diff --check\npytest -q\n```",
    )
    with pytest.raises(factory.FactoryError, match="cannot contain executable"):
        factory.validate_contract(issue(description=body))


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
        row, ready_label_id="ready", approver_id="human"
    ) == (True, "human_approval_verified")
    row["history"]["nodes"][0]["actorId"] = "automation"
    assert factory.human_approval_verified(
        row, ready_label_id="ready", approver_id="human"
    )[1] == "agent_ready_not_applied_by_human_owner"
    row["history"]["nodes"][0]["actorId"] = "human"
    row["history"]["nodes"].append(
        {
            "actorId": "human",
            "addedLabelIds": [],
            "updatedDescription": True,
            "createdAt": "2026-07-23T12:01:00Z",
        }
    )
    assert factory.human_approval_verified(
        row, ready_label_id="ready", approver_id="human"
    )[1] == "spec_changed_after_approval"


def test_hash_bound_owner_comment_is_auditable_approval_fallback() -> None:
    row = issue()
    row["history"]["nodes"] = []
    row["comments"]["nodes"] = [
        {
            "user": {"id": "human", "name": "Michael"},
            "body": f"Factory approval: {factory.spec_sha256(row['description'])}",
            "createdAt": "2026-07-23T12:00:00Z",
        }
    ]
    assert factory.human_approval_verified(
        row, ready_label_id="ready", approver_id="human"
    ) == (True, "human_spec_hash_approval_verified")
    row["description"] += "\nChanged after approval.\n"
    assert factory.human_approval_verified(
        row, ready_label_id="ready", approver_id="human"
    )[1] == "agent_ready_not_applied_by_human_owner"


def test_approval_evidence_paginates_history_and_comments_to_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = factory.LinearClient("test-key")
    calls: list[tuple[str, str | None]] = []

    def fake_query(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        assert variables is not None
        connection = "history" if "history(first:" in query else "comments"
        cursor = variables.get("after")
        calls.append((connection, cursor))
        if cursor is None:
            return {
                "issue": {
                    connection: {
                        "nodes": [{"page": 1}],
                        "pageInfo": {"hasNextPage": True, "endCursor": f"{connection}-2"},
                    }
                }
            }
        return {
            "issue": {
                connection: {
                    "nodes": [{"page": 2}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }

    monkeypatch.setattr(client, "query", fake_query)
    evidence = client.approval_evidence("issue-id")
    assert evidence["history"]["nodes"] == [{"page": 1}, {"page": 2}]
    assert evidence["comments"]["nodes"] == [{"page": 1}, {"page": 2}]
    assert calls == [
        ("history", None),
        ("history", "history-2"),
        ("comments", None),
        ("comments", "comments-2"),
    ]


def test_approval_evidence_fails_closed_on_invalid_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = factory.LinearClient("test-key")
    monkeypatch.setattr(
        client,
        "query",
        lambda *_args, **_kwargs: {
            "issue": {
                "history": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": True, "endCursor": None},
                }
            }
        },
    )
    with pytest.raises(factory.FactoryError, match="cursor was invalid"):
        client.approval_evidence("issue-id")


def test_issue_queue_paginates_to_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = factory.LinearClient("test-key")
    cursors: list[str | None] = []

    def fake_query(_query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        assert variables is not None
        cursor = variables.get("after")
        cursors.append(cursor)
        if cursor is None:
            return {
                "issues": {
                    "nodes": [{"id": "first"}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "page-2"},
                }
            }
        return {
            "issues": {
                "nodes": [{"id": "second"}],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }

    monkeypatch.setattr(client, "query", fake_query)
    assert client.eligible_issues("AI", "agent-ready") == [
        {"id": "first"},
        {"id": "second"},
    ]
    assert cursors == [None, "page-2"]


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


def test_pipeline_uses_supported_scheduled_run_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []
    captured_env: dict[str, str] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, '{"success": true}', "")

    monkeypatch.setenv("LINEAR_API_KEY", "must-not-reach-pipeline")
    monkeypatch.setattr(factory, "run", fake_run)
    row = issue()
    payload = factory.run_pipeline(
        config=config(tmp_path),
        issue=row,
        contract=factory.validate_contract(row),
        repo_path=tmp_path / "repo",
        base_ref="origin/trunk",
    )
    assert payload["success"] is True
    assert captured[captured.index("--run-context") + 1] == "cron"
    assert captured[captured.index("--base") + 1] == "origin/trunk"
    assert captured[captured.index("--task-id") + 1].endswith("-a1")
    assert captured[captured.index("--verification") + 1] == (
        "auto-detect repository verification"
    )
    task = captured[captured.index("--task") + 1]
    assert "The outer Controller owns commit, push, draft PR creation" in task
    assert "do not block only because those outer steps are pending" in task
    assert "LINEAR_API_KEY" not in captured_env


def test_pipeline_env_rejects_credential_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_FACTORY_PIPELINE_ENV_ALLOWLIST", "PATH,GH_TOKEN")
    with pytest.raises(factory.FactoryError, match="denied credential names"):
        factory.run_pipeline(
            config=config(tmp_path),
            issue=issue(),
            contract=factory.validate_contract(issue()),
            repo_path=tmp_path / "repo",
            base_ref="origin/main",
        )


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


def test_invalid_receipt_fails_closed(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    path = factory._receipt_path(cfg, "AI-10")  # noqa: SLF001
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    payload = factory.import_once(FakeLinear([issue()]), cfg, act=False)  # type: ignore[arg-type]
    assert payload["selected"] is None
    assert "receipt is unreadable or invalid" in payload["candidates"][0]["reason"]


def test_execution_identity_cannot_be_human_approver(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    cfg = factory.FactoryConfig(
        team=cfg.team,
        state_root=cfg.state_root,
        repo_map=cfg.repo_map,
        worker=cfg.worker,
        herdr_adapter=cfg.herdr_adapter,
        github_owner=cfg.github_owner,
        human_approver_id="viewer",
    )
    with pytest.raises(factory.FactoryError, match="must be independent"):
        factory.import_once(FakeLinear([issue()]), cfg, act=False)  # type: ignore[arg-type]


def _patch_successful_external_gates(
    monkeypatch: pytest.MonkeyPatch, cfg: factory.FactoryConfig
) -> None:
    monkeypatch.setattr(
        factory,
        "inspect_repository",
        lambda _path: {
            "name_with_owner": "moortekweb-art/agentic-harness",
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        factory, "_required_status_contexts", lambda *_args: ["required"]
    )
    monkeypatch.setattr(factory, "_open_prs_for_issue", lambda *_args: [])
    monkeypatch.setattr(
        factory,
        "run_pipeline",
        lambda **_kwargs: {"success": True, "branch": "nm/ai-10"},
    )
    pr = {
        "number": 10,
        "url": "https://github.example/pr/10",
        "headRefOid": "a" * 40,
        "isDraft": True,
        "mergeStateStatus": "CLEAN",
    }
    monkeypatch.setattr(factory, "_pr_for_branch", lambda *_args: dict(pr))
    monkeypatch.setattr(factory, "_changed_files", lambda *_args: ["docs/change.md"])
    monkeypatch.setattr(
        factory,
        "_browser_gate",
        lambda *_args: {
            "required": False,
            "passed": True,
            "reason": "no_ui_files_changed",
        },
    )
    monkeypatch.setattr(factory, "_set_pr_verdict", lambda *_args: "loop-approved")
    assert cfg.repo_map["moortekweb-art/agentic-harness"].is_dir()


def test_act_path_rechecks_authorization_and_never_overwrites_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    client = ActingFakeLinear([issue()])
    _patch_successful_external_gates(monkeypatch, cfg)
    payload = factory.import_once(client, cfg, act=True)  # type: ignore[arg-type]
    assert payload["action"] == "completed"
    assert payload["receipt"]["status"] == "merge_ready"
    assert client.label_arguments == [None, None]
    assert "agent-ready" not in {
        label["name"] for label in client.issues[0]["labels"]["nodes"]
    }


def test_revocation_during_claim_stops_before_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    client = ActingFakeLinear([issue()], revoke_on_claim=True)
    _patch_successful_external_gates(monkeypatch, cfg)
    pipeline_called = False

    def unexpected_pipeline(**_kwargs: Any) -> dict[str, Any]:
        nonlocal pipeline_called
        pipeline_called = True
        return {}

    monkeypatch.setattr(factory, "run_pipeline", unexpected_pipeline)
    payload = factory.import_once(client, cfg, act=True)  # type: ignore[arg-type]
    assert payload["action"] == "blocked"
    assert pipeline_called is False
    assert "human approval was revoked" in payload["receipt"]["error"]
    assert "blocked" in {
        label["name"] for label in client.issues[0]["labels"]["nodes"]
    }


def test_resolved_blocked_receipt_can_be_retried(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    row = issue()
    parsed = factory.validate_contract(row)
    factory.atomic_write_json(
        factory._receipt_path(cfg, "AI-10"),  # noqa: SLF001
        {
            "contract": factory.CONTRACT,
            "identifier": "AI-10",
            "spec_sha256": parsed["spec_sha256"],
            "status": "blocked",
        },
    )
    payload = factory.import_once(FakeLinear([row]), cfg, act=False)  # type: ignore[arg-type]
    assert payload["selected"] == "AI-10"
    assert payload["candidates"][0]["reason"] == "eligible"


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


def test_browser_gate_uses_allowlisted_env_and_redacts_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_env: dict[str, str] = {}
    verifier_command: list[str] = []

    def fake_run(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["git", "worktree", "add"]:
            Path(command[-2]).mkdir()
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
        if command[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        verifier_command.extend(command)
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(
            command,
            0,
            "token=sk-secretvalue123",
            "Bearer secretvalue123",
        )

    monkeypatch.setenv("LINEAR_FACTORY_BROWSER_VERIFY_CMD", "verify-ui")
    monkeypatch.setenv("LINEAR_FACTORY_BROWSER_ENV_ALLOWLIST", "PATH,SAFE_VALUE")
    monkeypatch.setenv("SAFE_VALUE", "allowed")
    monkeypatch.setenv("LINEAR_API_KEY", "must-not-leak")
    monkeypatch.setattr(factory, "run", fake_run)
    payload = factory._browser_gate(  # noqa: SLF001
        tmp_path,
        {"number": 7, "headRefOid": "a" * 40},
        ["agentic_harness/gui/static/app.js"],
    )
    assert captured_env["SAFE_VALUE"] == "allowed"
    assert "LINEAR_API_KEY" not in captured_env
    assert "HOME" not in captured_env
    assert verifier_command == ["/bin/sh", "-c", "verify-ui"]
    assert payload["stdout_tail"] == "token=<redacted>"
    assert payload["stderr_tail"] == "Bearer <redacted>"
    assert payload["head_sha"] == "a" * 40


def test_pr_gate_requires_each_exact_context_and_clean_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        payload = {
            "number": 7,
            "url": "https://github.example/pr/7",
            "isDraft": True,
            "headRefOid": "a" * 40,
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [
                {"name": "required-a", "conclusion": "SUCCESS"},
                {"name": "required-b", "conclusion": "SKIPPED"},
                {"name": "unrelated", "conclusion": "SUCCESS"},
            ],
        }
        return subprocess.CompletedProcess(command, 0, factory.json.dumps(payload), "")

    monkeypatch.setattr(factory, "run", fake_run)
    with pytest.raises(factory.FactoryError, match="required-b"):
        factory._pr_for_branch(  # noqa: SLF001
            tmp_path, "branch", ["required-a", "required-b"]
        )


def test_required_contexts_apply_to_actual_default_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[-1].endswith("/rulesets"):
            payload: Any = [
                {
                    "id": 1,
                    "target": "branch",
                    "enforcement": "active",
                },
                {
                    "id": 2,
                    "target": "branch",
                    "enforcement": "active",
                },
            ]
        elif command[-1].endswith("/1"):
            payload = {
                "conditions": {"ref_name": {"include": ["release"], "exclude": []}},
                "rules": [
                    {"type": "pull_request"},
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "required_status_checks": [{"context": "wrong-branch"}]
                        },
                    },
                ],
            }
        else:
            payload = {
                "conditions": {
                    "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
                },
                "rules": [
                    {"type": "pull_request"},
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "required_status_checks": [{"context": "required-main"}]
                        },
                    },
                ],
            }
        return subprocess.CompletedProcess(command, 0, factory.json.dumps(payload), "")

    monkeypatch.setattr(factory, "run", fake_run)
    assert factory._required_status_contexts(  # noqa: SLF001
        tmp_path, "owner/repo", "main"
    ) == ["required-main"]
