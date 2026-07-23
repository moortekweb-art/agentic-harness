"""Human-gated Linear intake and Controller execution bridge.

Linear owns the requested scope and approval state.  This module performs one
bounded pass at a time; systemd or another scheduler owns persistence.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agentic_harness.core.redaction import redact_secrets


LINEAR_API_URL = "https://api.linear.app/graphql"
CONTRACT = "agentic_harness_linear_factory.v1"
REQUIRED_SECTIONS = (
    "Requested outcome",
    "Acceptance criteria",
    "Non-goals",
    "Verification requirements",
    "Approval boundary",
)
TERMINAL_RECEIPT_STATES = frozenset({"merge_ready", "blocked", "failed"})
UI_SUFFIXES = frozenset(
    {".css", ".html", ".htm", ".js", ".jsx", ".mjs", ".svelte", ".tsx", ".vue"}
)


class FactoryError(RuntimeError):
    """Expected, user-facing factory failure."""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_sha256(description: str) -> str:
    normalized = description.replace("\r\n", "\n").strip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def slug(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return (cleaned or "task")[:limit]


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


class LinearClient:
    def __init__(self, api_key: str, *, api_url: str = LINEAR_API_URL) -> None:
        if not api_key.strip():
            raise FactoryError("LINEAR_API_KEY is not set")
        self.api_key = api_key.strip()
        self.api_url = api_url

    def query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps({"query": query, "variables": variables or {}}).encode("utf-8"),
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "agentic-harness-linear-factory/1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1000:]
            raise FactoryError(f"Linear HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FactoryError(f"Linear request failed: {type(exc).__name__}") from exc
        errors = payload.get("errors") or []
        if errors:
            safe = "; ".join(str(item.get("message") or "GraphQL error") for item in errors)
            raise FactoryError(f"Linear GraphQL error: {safe[:1000]}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FactoryError("Linear response did not contain an object")
        return data

    def workspace(self, team_key: str) -> dict[str, Any]:
        query = """query($team: String!) {
          viewer { id name }
          teams(filter: { key: { eq: $team } }, first: 1) {
            nodes {
              id key name
              states(first: 100) { nodes { id name type } }
              labels(first: 100) { nodes { id name } }
            }
          }
        }"""
        data = self.query(query, {"team": team_key})
        teams = data.get("teams", {}).get("nodes", [])
        if len(teams) != 1:
            raise FactoryError(f"Linear team {team_key!r} was not found uniquely")
        return {"viewer": data["viewer"], "team": teams[0]}

    def eligible_issues(self, team_key: str, ready_label: str) -> list[dict[str, Any]]:
        query = """query($team: String!, $label: String!) {
          issues(
            filter: {
              team: { key: { eq: $team } }
              labels: { name: { eq: $label } }
            }
            first: 50
            orderBy: createdAt
          ) {
            nodes {
              id identifier title description url priority
              state { id name type }
              assignee { id name }
              labels { nodes { id name } }
              history(first: 100) {
                nodes { actorId addedLabelIds updatedDescription createdAt }
              }
              relations { nodes { type relatedIssue { identifier state { type } } } }
              inverseRelations { nodes { type issue { identifier state { type } } } }
            }
          }
        }"""
        return list(
            self.query(query, {"team": team_key, "label": ready_label})
            .get("issues", {})
            .get("nodes", [])
        )

    def active_builder_issues(self, team_key: str, ready_label: str) -> list[dict[str, Any]]:
        return [
            issue
            for issue in self.eligible_issues(team_key, ready_label)
            if issue.get("assignee")
            and (issue.get("state") or {}).get("type") == "started"
            and "blocked"
            not in {
                str(label.get("name") or "")
                for label in issue.get("labels", {}).get("nodes", [])
            }
        ]

    def update_issue(
        self,
        issue_id: str,
        *,
        assignee_id: str | None,
        state_id: str,
        label_ids: list[str],
    ) -> dict[str, Any]:
        query = """mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue { id identifier state { name type } assignee { id name } labels { nodes { id name } } }
          }
        }"""
        payload = self.query(
            query,
            {
                "id": issue_id,
                "input": {
                    "assigneeId": assignee_id,
                    "stateId": state_id,
                    "labelIds": label_ids,
                },
            },
        ).get("issueUpdate", {})
        if payload.get("success") is not True:
            raise FactoryError(f"Linear did not update issue {issue_id}")
        return dict(payload.get("issue") or {})

    def comment(self, issue_id: str, body: str) -> None:
        query = """mutation($input: CommentCreateInput!) {
          commentCreate(input: $input) { success comment { id } }
        }"""
        payload = self.query(
            query, {"input": {"issueId": issue_id, "body": body}}
        ).get("commentCreate", {})
        if payload.get("success") is not True:
            raise FactoryError(f"Linear did not comment on issue {issue_id}")

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        priority: int,
        label_ids: list[str],
    ) -> dict[str, Any]:
        query = """mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success issue { id identifier title url labels { nodes { name } } }
          }
        }"""
        payload = self.query(
            query,
            {
                "input": {
                    "teamId": team_id,
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "labelIds": label_ids,
                }
            },
        ).get("issueCreate", {})
        if payload.get("success") is not True:
            raise FactoryError("Linear did not create the draft specification")
        return dict(payload.get("issue") or {})


def _section(description: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(description)
    return match.group(1).strip() if match else ""


def validate_contract(issue: dict[str, Any]) -> dict[str, Any]:
    description = str(issue.get("description") or "")
    missing = [heading for heading in REQUIRED_SECTIONS if not _section(description, heading)]
    acceptance = re.findall(r"\bAC-(\d+)\s*:", _section(description, "Acceptance criteria"))
    non_goals = re.findall(r"\bNG-(\d+)\s*:", _section(description, "Non-goals"))
    if missing:
        raise FactoryError(f"{issue.get('identifier')}: missing sections: {', '.join(missing)}")
    if not acceptance or len(set(acceptance)) != len(acceptance):
        raise FactoryError(f"{issue.get('identifier')}: acceptance criteria require unique AC-N IDs")
    if not non_goals or len(set(non_goals)) != len(non_goals):
        raise FactoryError(f"{issue.get('identifier')}: non-goals require unique NG-N IDs")
    repository_match = re.search(
        r"(?:Target repository|Repository):\s*(?:\[)?"
        r"(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        description,
        re.IGNORECASE,
    )
    if not repository_match:
        raise FactoryError(f"{issue.get('identifier')}: missing GitHub repository URL")
    repository_url = repository_match.group(1).removesuffix(".git")
    name_with_owner = repository_url.removeprefix("https://github.com/")
    return {
        "description": description,
        "spec_sha256": spec_sha256(description),
        "repository_url": repository_url,
        "name_with_owner": name_with_owner,
        "acceptance_ids": [f"AC-{value}" for value in acceptance],
        "non_goal_ids": [f"NG-{value}" for value in non_goals],
        "verification": _section(description, "Verification requirements"),
    }


def is_dependency_blocked(issue: dict[str, Any]) -> bool:
    for relation in issue.get("inverseRelations", {}).get("nodes", []):
        dependency = relation.get("issue") or {}
        if relation.get("type") == "blocks" and (dependency.get("state") or {}).get(
            "type"
        ) != "completed":
            return True
    return False


def label_map(workspace: dict[str, Any]) -> dict[str, str]:
    return {
        str(label["name"]): str(label["id"])
        for label in workspace["team"].get("labels", {}).get("nodes", [])
    }


def state_by_type(workspace: dict[str, Any], state_type: str, preferred: str) -> str:
    states = list(workspace["team"].get("states", {}).get("nodes", []))
    exact = next(
        (
            item
            for item in states
            if item.get("type") == state_type
            and str(item.get("name") or "").lower() == preferred.lower()
        ),
        None,
    )
    fallback = next((item for item in states if item.get("type") == state_type), None)
    selected = exact or fallback
    if not selected:
        raise FactoryError(f"Linear team has no {state_type!r} workflow state")
    return str(selected["id"])


def _prompt(value: str, question: str) -> str:
    if value.strip():
        return value.strip()
    if not sys.stdin.isatty():
        raise FactoryError(f"missing required intake decision: {question}")
    return input(f"{question}: ").strip()


def render_spec(args: argparse.Namespace, repository_url: str) -> str:
    outcome = _prompt(args.outcome, "What useful behavior should work")
    criteria = list(args.acceptance)
    non_goals = list(args.non_goal)
    verification = list(args.verification)
    human_steps = list(args.human_step)
    if not criteria:
        criteria.append(_prompt("", "What observable result proves success"))
    if not non_goals:
        non_goals.append(_prompt("", "What tempting adjacent work is outside scope"))
    if not verification:
        verification.append(_prompt("", "What independent check must pass"))
    if not human_steps:
        human_steps.append(_prompt("", "What should the human verify before merge"))
    lines = [
        "## Requested outcome",
        "",
        outcome,
        "",
        f"Source idea: {args.idea.strip()}",
        f"Target repository: {repository_url}",
        "",
        "## Context",
        "",
        (args.context.strip() or "Inspect the repository and preserve its current governance."),
        "",
        "## Acceptance criteria",
        "",
    ]
    lines.extend(f"- AC-{index}: {value.strip()}" for index, value in enumerate(criteria, 1))
    lines.extend(["", "## Non-goals", ""])
    lines.extend(f"- NG-{index}: {value.strip()}" for index, value in enumerate(non_goals, 1))
    lines.extend(["", "## Verification requirements", ""])
    lines.extend(f"{index}. {value.strip()}" for index, value in enumerate(verification, 1))
    lines.extend(["", "## Human acceptance steps", ""])
    lines.extend(f"{index}. {value.strip()}" for index, value in enumerate(human_steps, 1))
    lines.extend(
        [
            "",
            "## Approval boundary",
            "",
            "This issue is a draft specification. Build must not start until Michael applies "
            "`agent-ready`. Agents never merge or enable auto-merge.",
            "",
        ]
    )
    return "\n".join(lines)


def inspect_repository(raw: str) -> dict[str, str]:
    path = Path(raw).expanduser().resolve()
    root = run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], timeout=10)
    if root.returncode != 0:
        raise FactoryError(f"not a Git repository: {path}")
    repo = Path(root.stdout.strip())
    remote = run(["git", "-C", str(repo), "remote", "get-url", "origin"], timeout=10)
    if remote.returncode != 0:
        raise FactoryError("repository has no origin remote")
    view = run(
        ["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef,url"],
        cwd=repo,
        timeout=30,
    )
    if view.returncode != 0:
        raise FactoryError(f"GitHub repository inspection failed: {view.stderr[-500:]}")
    metadata = json.loads(view.stdout)
    return {
        "path": str(repo),
        "url": str(metadata["url"]),
        "name_with_owner": str(metadata["nameWithOwner"]),
        "default_branch": str(metadata["defaultBranchRef"]["name"]),
        "origin": remote.stdout.strip(),
    }


@dataclass(frozen=True)
class FactoryConfig:
    team: str
    state_root: Path
    repo_map: dict[str, Path]
    worker: str
    herdr_adapter: Path
    github_owner: str
    ready_label: str = "agent-ready"
    blocked_label: str = "blocked"
    draft_label: str = "spec-drafted"

    @classmethod
    def from_env(cls, args: argparse.Namespace) -> "FactoryConfig":
        raw_map = os.environ.get("LINEAR_FACTORY_REPO_MAP", "{}")
        try:
            decoded = json.loads(raw_map)
        except json.JSONDecodeError as exc:
            raise FactoryError("LINEAR_FACTORY_REPO_MAP is invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise FactoryError("LINEAR_FACTORY_REPO_MAP must be a JSON object")
        return cls(
            team=str(args.team),
            state_root=Path(args.state_root).expanduser().resolve(),
            repo_map={str(key): Path(str(value)).expanduser().resolve() for key, value in decoded.items()},
            worker=os.environ.get("LINEAR_FACTORY_WORKER", "codex").strip() or "codex",
            herdr_adapter=Path(
                os.environ.get(
                    "LINEAR_FACTORY_HERDR_ADAPTER",
                    "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/"
                    "scripts/herdr-controller-execution.py",
                )
            ),
            github_owner=os.environ.get("LINEAR_FACTORY_GITHUB_OWNER", "moortekweb-art"),
        )


def _issue_label_ids(issue: dict[str, Any]) -> dict[str, str]:
    return {
        str(label["name"]): str(label["id"])
        for label in issue.get("labels", {}).get("nodes", [])
    }


def _eligible(issue: dict[str, Any], config: FactoryConfig) -> tuple[bool, str]:
    labels = set(_issue_label_ids(issue))
    if config.ready_label not in labels:
        return False, "missing_agent_ready"
    if config.blocked_label in labels:
        return False, "blocked_label"
    if issue.get("assignee"):
        return False, "already_assigned"
    if (issue.get("state") or {}).get("type") in {"completed", "canceled"}:
        return False, "terminal_state"
    if is_dependency_blocked(issue):
        return False, "dependency_blocked"
    return True, "eligible"


def human_approval_verified(
    issue: dict[str, Any], *, ready_label_id: str, viewer_id: str
) -> tuple[bool, str]:
    history = list(issue.get("history", {}).get("nodes", []))
    approvals = [
        row
        for row in history
        if ready_label_id in list(row.get("addedLabelIds") or [])
        and str(row.get("actorId") or "") == viewer_id
    ]
    if not approvals:
        return False, "agent_ready_not_applied_by_human_owner"
    approved_at = max(str(row.get("createdAt") or "") for row in approvals)
    changed_after = any(
        row.get("updatedDescription") is True
        and str(row.get("createdAt") or "") > approved_at
        for row in history
    )
    if changed_after:
        return False, "spec_changed_after_approval"
    return True, "human_approval_verified"


def _receipt_path(config: FactoryConfig, identifier: str) -> Path:
    return config.state_root / "receipts" / f"{slug(identifier, 100)}.json"


def _load_receipt(config: FactoryConfig, identifier: str) -> dict[str, Any]:
    path = _receipt_path(config, identifier)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pipeline_task(issue: dict[str, Any], contract: dict[str, Any]) -> str:
    return (
        f"Implement Linear issue {issue['identifier']} exactly as written.\n"
        f"Linear URL: {issue['url']}\n"
        f"Immutable specification SHA-256: {contract['spec_sha256']}\n\n"
        f"{contract['description']}\n\n"
        "Factory rules:\n"
        "- One issue maps to this one branch and one draft PR.\n"
        "- Non-goals are binding; do not broaden scope.\n"
        "- Run focused and repository-owned verification.\n"
        "- Never merge, enable auto-merge, publish, release, or deploy.\n"
    )


def run_pipeline(
    *,
    config: FactoryConfig,
    issue: dict[str, Any],
    contract: dict[str, Any],
    repo_path: Path,
) -> dict[str, Any]:
    task_id = f"linear-{slug(issue['identifier'], 30)}-{contract['spec_sha256'][:12]}"
    command = [
        sys.executable,
        str(config.herdr_adapter),
        "pipeline",
        "--worker",
        config.worker,
        "--task",
        _pipeline_task(issue, contract),
        "--title",
        f"{issue['identifier']} {issue['title']}",
        "--mode",
        "implementation",
        "--task-type",
        "code_work",
        "--risk",
        "medium",
        "--workdir",
        str(repo_path),
        "--base",
        "origin/main",
        "--run-context",
        "linear-factory",
        "--timeout",
        "1800",
        "--task-id",
        task_id,
        "--verification",
        str(contract["verification"]),
    ]
    completed = run(command, cwd=repo_path, timeout=7200, env=os.environ.copy())
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FactoryError(
            f"Herdr pipeline returned invalid JSON (rc={completed.returncode}): "
            f"{redact_secrets(completed.stderr[-1000:])}"
        ) from exc
    if not isinstance(payload, dict):
        raise FactoryError("Herdr pipeline returned a non-object JSON value")
    payload["returncode"] = completed.returncode
    if completed.returncode != 0 or payload.get("success") is not True:
        raise FactoryError(
            "Herdr pipeline blocked: "
            f"{redact_secrets(str(payload.get('reason') or completed.stderr[-500:]))}"
        )
    return payload


def _pr_for_branch(repo_path: Path, branch: str) -> dict[str, Any]:
    completed = run(
        [
            "gh",
            "pr",
            "view",
            branch,
            "--json",
            "number,url,isDraft,headRefOid,mergeStateStatus,statusCheckRollup",
        ],
        cwd=repo_path,
        timeout=60,
    )
    if completed.returncode != 0:
        raise FactoryError(f"draft PR lookup failed: {completed.stderr[-800:]}")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise FactoryError("draft PR lookup returned a non-object JSON value")
    checks = list(payload.get("statusCheckRollup") or [])
    failed = [
        item
        for item in checks
        if str(item.get("conclusion") or item.get("state") or "").upper()
        not in {"SUCCESS", "NEUTRAL", "SKIPPED"}
    ]
    if not checks or failed:
        raise FactoryError("required GitHub checks are absent or not green")
    if payload.get("mergeStateStatus") not in {"CLEAN", "UNSTABLE"}:
        raise FactoryError(f"PR is not conflict-free: {payload.get('mergeStateStatus')}")
    if payload.get("isDraft") is not True:
        raise FactoryError("factory PR must remain a draft")
    return payload


def _changed_files(repo_path: Path, branch: str) -> list[str]:
    completed = run(["gh", "pr", "diff", branch, "--name-only"], cwd=repo_path, timeout=60)
    if completed.returncode != 0:
        raise FactoryError(f"PR changed-file lookup failed: {completed.stderr[-800:]}")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _browser_gate(repo_path: Path, pr: dict[str, Any], changed: list[str]) -> dict[str, Any]:
    ui_changed = any(Path(path).suffix.lower() in UI_SUFFIXES for path in changed)
    if not ui_changed:
        return {"required": False, "passed": True, "reason": "no_ui_files_changed"}
    command = os.environ.get("LINEAR_FACTORY_BROWSER_VERIFY_CMD", "").strip()
    if not command:
        return {"required": True, "passed": False, "reason": "browser_verifier_not_configured"}
    env = os.environ.copy()
    env.update(
        {
            "LINEAR_FACTORY_PR_NUMBER": str(pr["number"]),
            "LINEAR_FACTORY_PR_HEAD_SHA": str(pr["headRefOid"]),
        }
    )
    completed = run(
        ["/bin/sh", "-lc", command], cwd=repo_path, timeout=900, env=env
    )
    return {
        "required": True,
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def _set_pr_verdict(
    repo_path: Path, pr: dict[str, Any], browser: dict[str, Any], issue: dict[str, Any]
) -> str:
    approved = browser.get("passed") is True
    verdict = "loop-approved" if approved else "needs-human-review"
    competing = (
        ["loop-changes-requested", "needs-human-review"]
        if approved
        else ["loop-approved", "loop-changes-requested"]
    )
    for label in competing:
        run(
            ["gh", "pr", "edit", str(pr["number"]), "--remove-label", label],
            cwd=repo_path,
            timeout=30,
        )
    edited = run(
        ["gh", "pr", "edit", str(pr["number"]), "--add-label", verdict],
        cwd=repo_path,
        timeout=30,
    )
    if edited.returncode != 0:
        raise FactoryError(f"could not apply PR verdict: {edited.stderr[-800:]}")
    body = (
        f"Factory review for [{issue['identifier']}]({issue['url']}) at "
        f"`{pr['headRefOid']}`: **{verdict}**.\n\n"
        f"- Required CI: PASS\n"
        f"- Conflict check: PASS\n"
        f"- Browser gate: {'PASS' if browser.get('passed') else 'NEEDS HUMAN REVIEW'}\n"
        f"- Merge authority: Michael only; this verdict is evidence, not permission."
    )
    commented = run(
        ["gh", "pr", "comment", str(pr["number"]), "--body", body],
        cwd=repo_path,
        timeout=30,
    )
    if commented.returncode != 0:
        raise FactoryError(f"could not post PR review evidence: {commented.stderr[-800:]}")
    return verdict


def doctor(client: LinearClient, config: FactoryConfig) -> dict[str, Any]:
    workspace = client.workspace(config.team)
    labels = label_map(workspace)
    missing_labels = [
        name
        for name in (config.draft_label, config.ready_label, config.blocked_label)
        if name not in labels
    ]
    repo_results: dict[str, Any] = {}
    for name_with_owner, path in config.repo_map.items():
        try:
            info = inspect_repository(str(path))
            if info["name_with_owner"] != name_with_owner:
                raise FactoryError(
                    f"repo map expected {name_with_owner}, got {info['name_with_owner']}"
                )
            rules = run(
                ["gh", "api", f"repos/{name_with_owner}/rulesets"],
                cwd=path,
                timeout=30,
            )
            if rules.returncode != 0:
                raise FactoryError("GitHub ruleset lookup failed")
            rulesets = json.loads(rules.stdout)
            if not isinstance(rulesets, list):
                raise FactoryError("GitHub ruleset lookup returned invalid JSON")
            protected = False
            for ruleset in rulesets:
                if (
                    isinstance(ruleset, dict)
                    and ruleset.get("target") == "branch"
                    and ruleset.get("enforcement") == "active"
                    and ruleset.get("id")
                ):
                    detail = run(
                        [
                            "gh",
                            "api",
                            f"repos/{name_with_owner}/rulesets/{ruleset['id']}",
                        ],
                        cwd=path,
                        timeout=30,
                    )
                    if detail.returncode != 0:
                        continue
                    detail_payload = json.loads(detail.stdout)
                    rule_types = {
                        str(item.get("type"))
                        for item in detail_payload.get("rules", [])
                        if isinstance(item, dict)
                    }
                    if "pull_request" in rule_types and "required_status_checks" in rule_types:
                        protected = True
                        break
            if not protected:
                raise FactoryError("default branch lacks active PR and required-check rules")
            repo_results[name_with_owner] = {
                "ok": True,
                "path": str(path),
                "default_branch": info["default_branch"],
            }
        except FactoryError as exc:
            repo_results[name_with_owner] = {"ok": False, "error": str(exc)}
    return {
        "contract": CONTRACT,
        "ok": not missing_labels
        and bool(config.repo_map)
        and all(item.get("ok") for item in repo_results.values())
        and config.herdr_adapter.is_file(),
        "team": config.team,
        "viewer": workspace["viewer"].get("name"),
        "missing_labels": missing_labels,
        "herdr_adapter": str(config.herdr_adapter),
        "herdr_adapter_present": config.herdr_adapter.is_file(),
        "repositories": repo_results,
        "human_merge_only": True,
    }


def import_once(
    client: LinearClient, config: FactoryConfig, *, act: bool
) -> dict[str, Any]:
    config.state_root.mkdir(parents=True, exist_ok=True)
    lock_path = config.state_root / f"{slug(config.team, 40)}.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"contract": CONTRACT, "ok": True, "action": "busy", "team": config.team}
        workspace = client.workspace(config.team)
        workspace_labels = label_map(workspace)
        if config.ready_label not in workspace_labels:
            raise FactoryError(f"Linear label {config.ready_label!r} is missing")
        active = client.active_builder_issues(config.team, config.ready_label)
        if active:
            return {
                "contract": CONTRACT,
                "ok": True,
                "action": "active_builder",
                "team": config.team,
                "issues": [item["identifier"] for item in active],
            }
        candidates: list[dict[str, Any]] = []
        for issue in client.eligible_issues(config.team, config.ready_label):
            eligible, reason = _eligible(issue, config)
            row: dict[str, Any] = {
                "identifier": issue["identifier"],
                "title": issue["title"],
                "eligible": eligible,
                "reason": reason,
            }
            if eligible:
                approved, approval_reason = human_approval_verified(
                    issue,
                    ready_label_id=workspace_labels[config.ready_label],
                    viewer_id=str(workspace["viewer"]["id"]),
                )
                if not approved:
                    row.update(eligible=False, reason=approval_reason)
                    candidates.append(row)
                    continue
                try:
                    contract = validate_contract(issue)
                    receipt = _load_receipt(config, issue["identifier"])
                    if (
                        receipt.get("spec_sha256") == contract["spec_sha256"]
                        and receipt.get("status") in TERMINAL_RECEIPT_STATES
                    ):
                        row.update(eligible=False, reason="terminal_receipt_exists")
                    else:
                        row["contract"] = contract
                except FactoryError as exc:
                    row.update(eligible=False, reason=str(exc))
            candidates.append(row)
        selected = next((row for row in candidates if row.get("eligible")), None)
        result: dict[str, Any] = {
            "contract": CONTRACT,
            "ok": True,
            "team": config.team,
            "act": act,
            "candidates": [
                {key: value for key, value in row.items() if key != "contract"}
                for row in candidates
            ],
            "selected": selected["identifier"] if selected else None,
            "action": "dry_run" if not act else ("none" if not selected else "claim"),
        }
        if not act or not selected:
            return result
        issue = next(
            item
            for item in client.eligible_issues(config.team, config.ready_label)
            if item["identifier"] == selected["identifier"]
        )
        still_eligible, current_reason = _eligible(issue, config)
        if not still_eligible:
            raise FactoryError(
                f"{issue['identifier']} changed before claim: {current_reason}"
            )
        approved, approval_reason = human_approval_verified(
            issue,
            ready_label_id=workspace_labels[config.ready_label],
            viewer_id=str(workspace["viewer"]["id"]),
        )
        if not approved:
            raise FactoryError(
                f"{issue['identifier']} changed before claim: {approval_reason}"
            )
        contract = validate_contract(issue)
        if contract["spec_sha256"] != selected["contract"]["spec_sha256"]:
            raise FactoryError(f"{issue['identifier']} specification changed before claim")
        if not contract["name_with_owner"].startswith(f"{config.github_owner}/"):
            raise FactoryError("issue repository owner is outside the production allowlist")
        repo_path = config.repo_map.get(contract["name_with_owner"])
        if repo_path is None or not repo_path.is_dir():
            raise FactoryError(
                f"no production repository mapping for {contract['name_with_owner']}"
            )
        labels = label_map(workspace)
        issue_labels = _issue_label_ids(issue)
        started_state = state_by_type(workspace, "started", "In Progress")
        review_state = state_by_type(workspace, "started", "In Review")
        claimed = client.update_issue(
            issue["id"],
            assignee_id=str(workspace["viewer"]["id"]),
            state_id=started_state,
            label_ids=list(issue_labels.values()),
        )
        receipt = {
            "contract": CONTRACT,
            "issue_id": issue["id"],
            "identifier": issue["identifier"],
            "issue_url": issue["url"],
            "spec_sha256": contract["spec_sha256"],
            "repository": contract["name_with_owner"],
            "claimed_at": now_iso(),
            "claimed_by": claimed.get("assignee", {}).get("name"),
            "status": "running",
            "human_merge_only": True,
        }
        atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
        client.comment(
            issue["id"],
            f"Factory claimed this exact specification (`{contract['spec_sha256']}`). "
            "Build is running in an isolated Herdr worktree. Human merge remains mandatory.",
        )
        try:
            pipeline = run_pipeline(
                config=config, issue=issue, contract=contract, repo_path=repo_path
            )
            branch = str(pipeline.get("branch") or "")
            if not branch:
                raise FactoryError("Herdr result did not identify its isolated branch")
            pr = _pr_for_branch(repo_path, branch)
            changed = _changed_files(repo_path, branch)
            browser = _browser_gate(repo_path, pr, changed)
            verdict = _set_pr_verdict(repo_path, pr, browser, issue)
            receipt.update(
                {
                    "status": "merge_ready" if verdict == "loop-approved" else "blocked",
                    "finished_at": now_iso(),
                    "branch": branch,
                    "pr": pr,
                    "changed_files": changed,
                    "browser": browser,
                    "verdict": verdict,
                    "herdr_task_id": pipeline.get("task_id"),
                    "herdr_state_path": pipeline.get("state_path"),
                }
            )
            target_labels = list(issue_labels.values())
            if verdict != "loop-approved" and config.blocked_label in labels:
                target_labels.append(labels[config.blocked_label])
            client.update_issue(
                issue["id"],
                assignee_id=str(workspace["viewer"]["id"]),
                state_id=review_state,
                label_ids=sorted(set(target_labels)),
            )
            client.comment(
                issue["id"],
                f"Draft PR {pr['url']} reviewed at `{pr['headRefOid']}`: **{verdict}**. "
                "Michael must make the merge decision.",
            )
            atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
            result.update(action="completed", receipt=receipt)
            return result
        except Exception as exc:
            safe_error = redact_secrets(f"{type(exc).__name__}: {exc}")[:2000]
            receipt.update(
                {
                    "status": "blocked",
                    "finished_at": now_iso(),
                    "error": safe_error,
                }
            )
            atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
            target_labels = list(issue_labels.values())
            if config.blocked_label in labels:
                target_labels.append(labels[config.blocked_label])
            client.update_issue(
                issue["id"],
                assignee_id=str(workspace["viewer"]["id"]),
                state_id=started_state,
                label_ids=sorted(set(target_labels)),
            )
            client.comment(
                issue["id"],
                f"Factory stopped safely: `{receipt['error']}`. The issue is blocked; "
                "no merge was attempted.",
            )
            result.update(ok=False, action="blocked", receipt=receipt)
            return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-factory")
    parser.add_argument("--team", default=os.environ.get("LINEAR_FACTORY_TEAM", "AI"))
    parser.add_argument(
        "--state-root",
        default=os.environ.get(
            "LINEAR_FACTORY_STATE_ROOT",
            "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/"
            "state/linear-factory",
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Read-only Linear, GitHub, repository, and Herdr preflight")
    draft = sub.add_parser("draft", help="Interview for missing decisions and file a spec draft")
    draft.add_argument("--idea", required=True)
    draft.add_argument("--title", required=True)
    draft.add_argument("--repo", required=True)
    draft.add_argument("--outcome", default="")
    draft.add_argument("--context", default="")
    draft.add_argument("--acceptance", action="append", default=[])
    draft.add_argument("--non-goal", action="append", default=[])
    draft.add_argument("--verification", action="append", default=[])
    draft.add_argument("--human-step", action="append", default=[])
    draft.add_argument("--priority", type=int, choices=range(0, 5), default=3)
    draft.add_argument("--dry-run", action="store_true")
    importer = sub.add_parser("import-once", help="Claim and execute at most one approved issue")
    importer.add_argument("--act", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = FactoryConfig.from_env(args)
        client = LinearClient(os.environ.get("LINEAR_API_KEY", ""))
        if args.command == "doctor":
            payload = doctor(client, config)
        elif args.command == "draft":
            workspace = client.workspace(config.team)
            labels = label_map(workspace)
            if config.draft_label not in labels:
                raise FactoryError(f"Linear label {config.draft_label!r} is missing")
            repository = inspect_repository(args.repo)
            description = render_spec(args, repository["url"])
            if args.dry_run:
                payload = {
                    "contract": CONTRACT,
                    "ok": True,
                    "dry_run": True,
                    "title": args.title,
                    "description": description,
                    "label": config.draft_label,
                }
            else:
                issue = client.create_issue(
                    team_id=str(workspace["team"]["id"]),
                    title=args.title,
                    description=description,
                    priority=args.priority,
                    label_ids=[labels[config.draft_label]],
                )
                payload = {
                    "contract": CONTRACT,
                    "ok": True,
                    "dry_run": False,
                    "issue": issue,
                    "human_action": f"Review {issue['identifier']} and apply agent-ready",
                }
        else:
            payload = import_once(client, config, act=bool(args.act))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") is True else 1
    except FactoryError as exc:
        print(
            json.dumps(
                {"contract": CONTRACT, "ok": False, "error": str(exc)}, sort_keys=True
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
