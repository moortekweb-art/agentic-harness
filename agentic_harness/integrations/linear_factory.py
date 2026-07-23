"""Human-gated Linear intake and Controller execution bridge.

Linear owns the requested scope and approval state.  This module performs one
bounded pass at a time; systemd or another scheduler owns persistence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
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
from typing import Any, Sequence, TextIO

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
TERMINAL_RECEIPT_STATES = frozenset({"merge_ready", "failed"})
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


def try_acquire_team_lock(handle: TextIO) -> bool:
    """Acquire a non-blocking process lock on Unix or Windows."""
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    fcntl = importlib.import_module("fcntl")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


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
            raise FactoryError("Linear API key is not set")
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
        issues: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query = """query($team: String!, $label: String!, $after: String) {
              issues(
                filter: {
                  team: { key: { eq: $team } }
                  labels: { name: { eq: $label } }
                }
                first: 100
                after: $after
                orderBy: createdAt
              ) {
                nodes {
                  id identifier title description url priority
                  state { id name type }
                  assignee { id name }
                  labels { nodes { id name } }
                  relations { nodes { type relatedIssue { identifier state { type } } } }
                  inverseRelations { nodes { type issue { identifier state { type } } } }
                }
                pageInfo { hasNextPage endCursor }
              }
            }"""
            payload = self.query(
                query,
                {"team": team_key, "label": ready_label, "after": cursor},
            ).get("issues", {})
            nodes = payload.get("nodes")
            page_info = payload.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(page_info, dict):
                raise FactoryError("Linear issue queue pagination was incomplete")
            issues.extend(dict(row) for row in nodes if isinstance(row, dict))
            if page_info.get("hasNextPage") is not True:
                return issues
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise FactoryError("Linear issue queue pagination cursor was invalid")
            cursor = next_cursor

    def issue_snapshot(self, issue_id: str) -> dict[str, Any]:
        query = """query($id: String!) {
          issue(id: $id) {
            id identifier title description url priority
            state { id name type }
            assignee { id name }
            labels { nodes { id name } }
            relations { nodes { type relatedIssue { identifier state { type } } } }
            inverseRelations { nodes { type issue { identifier state { type } } } }
          }
        }"""
        issue = self.query(query, {"id": issue_id}).get("issue")
        if not isinstance(issue, dict):
            raise FactoryError(f"Linear issue {issue_id} was not returned")
        return dict(issue)

    def active_builder_issues(self, team_key: str, ready_label: str) -> list[dict[str, Any]]:
        del ready_label  # active claims must be found even if approval is revoked
        issues: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query = """query($team: String!, $after: String) {
              issues(
                filter: {
                  team: { key: { eq: $team } }
                  state: { type: { eq: "started" } }
                }
                first: 100
                after: $after
              ) {
                nodes {
                  id identifier
                  state { id name type }
                  assignee { id name }
                  labels { nodes { id name } }
                }
                pageInfo { hasNextPage endCursor }
              }
            }"""
            payload = self.query(
                query, {"team": team_key, "after": cursor}
            ).get("issues", {})
            nodes = payload.get("nodes")
            page_info = payload.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(page_info, dict):
                raise FactoryError("Linear active-builder pagination was incomplete")
            issues.extend(dict(row) for row in nodes if isinstance(row, dict))
            if page_info.get("hasNextPage") is not True:
                break
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise FactoryError("Linear active-builder pagination cursor was invalid")
            cursor = next_cursor
        return [
            issue
            for issue in issues
            if issue.get("assignee")
            and (issue.get("state") or {}).get("type") == "started"
            and str((issue.get("state") or {}).get("name") or "").lower()
            != "in review"
            and "blocked"
            not in {
                str(label.get("name") or "")
                for label in issue.get("labels", {}).get("nodes", [])
            }
        ]

    def approval_evidence(self, issue_id: str) -> dict[str, Any]:
        """Read complete approval history and comments; partial evidence is unsafe."""

        def collect(connection: str, fields: str) -> list[dict[str, Any]]:
            nodes: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                query = f"""query($id: String!, $after: String) {{
                  issue(id: $id) {{
                    {connection}(first: 100, after: $after) {{
                      nodes {{ {fields} }}
                      pageInfo {{ hasNextPage endCursor }}
                    }}
                  }}
                }}"""
                payload = (
                    self.query(query, {"id": issue_id, "after": cursor})
                    .get("issue", {})
                    .get(connection, {})
                )
                page_nodes = payload.get("nodes")
                page_info = payload.get("pageInfo")
                if not isinstance(page_nodes, list) or not isinstance(page_info, dict):
                    raise FactoryError(f"Linear {connection} pagination was incomplete")
                nodes.extend(dict(row) for row in page_nodes if isinstance(row, dict))
                if page_info.get("hasNextPage") is not True:
                    return nodes
                next_cursor = page_info.get("endCursor")
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                    raise FactoryError(f"Linear {connection} pagination cursor was invalid")
                cursor = next_cursor

        return {
            "history": {
                "nodes": collect(
                    "history",
                    "actorId addedLabelIds removedLabelIds updatedDescription createdAt",
                )
            },
            "comments": {
                "nodes": collect(
                    "comments",
                    "user { id name } body createdAt",
                )
            },
        }

    def update_issue(
        self,
        issue_id: str,
        *,
        assignee_id: str | None,
        state_id: str,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        query = """mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue { id identifier state { name type } assignee { id name } labels { nodes { id name } } }
          }
        }"""
        issue_input: dict[str, Any] = {
            "assigneeId": assignee_id,
            "stateId": state_id,
        }
        if label_ids is not None:
            issue_input["labelIds"] = label_ids
        payload = self.query(
            query,
            {"id": issue_id, "input": issue_input},
        ).get("issueUpdate", {})
        if payload.get("success") is not True:
            raise FactoryError(f"Linear did not update issue {issue_id}")
        return dict(payload.get("issue") or {})

    def add_label(self, issue_id: str, label_id: str) -> None:
        query = """mutation($id: String!, $label: String!) {
          issueAddLabel(id: $id, labelId: $label) { success }
        }"""
        payload = self.query(query, {"id": issue_id, "label": label_id}).get(
            "issueAddLabel", {}
        )
        if payload.get("success") is not True:
            raise FactoryError(f"Linear did not add label to issue {issue_id}")

    def remove_label(self, issue_id: str, label_id: str) -> None:
        query = """mutation($id: String!, $label: String!) {
          issueRemoveLabel(id: $id, labelId: $label) { success }
        }"""
        payload = self.query(query, {"id": issue_id, "label": label_id}).get(
            "issueRemoveLabel", {}
        )
        if payload.get("success") is not True:
            raise FactoryError(f"Linear did not remove label from issue {issue_id}")

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


def _verification_command(verification: str) -> str:
    """Reject issue-controlled shell expressions; repository discovery owns checks."""
    command_lines = re.findall(
        r"^\s*(?:[-*]\s*)?Command:\s*(\S.*?)\s*$",
        verification,
        re.MULTILINE | re.IGNORECASE,
    )
    shell_blocks = re.findall(
        r"```(?:bash|sh|shell)\s*\n(.*?)^```",
        verification,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    candidates = [
        value.strip() for value in [*command_lines, *shell_blocks] if value.strip()
    ]
    if candidates:
        raise FactoryError(
            "verification requirements cannot contain executable `Command:` "
            "lines or shell blocks; use repository-owned verification"
        )
    return ""


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
    verification = _section(description, "Verification requirements")
    return {
        "description": description,
        "spec_sha256": spec_sha256(description),
        "repository_url": repository_url,
        "name_with_owner": name_with_owner,
        "acceptance_ids": [f"AC-{value}" for value in acceptance],
        "non_goal_ids": [f"NG-{value}" for value in non_goals],
        "verification": verification,
        "verification_command": _verification_command(verification),
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
    human_approver_id: str
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
            human_approver_id=os.environ.get(
                "LINEAR_FACTORY_HUMAN_APPROVER_ID", ""
            ).strip(),
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
    issue: dict[str, Any], *, ready_label_id: str, approver_id: str
) -> tuple[bool, str]:
    if not approver_id:
        return False, "human_approver_not_configured"
    history = list(issue.get("history", {}).get("nodes", []))
    approvals = [
        row
        for row in history
        if ready_label_id in list(row.get("addedLabelIds") or [])
        and str(row.get("actorId") or "") == approver_id
    ]
    if not approvals:
        approval_marker = f"Factory approval: {spec_sha256(str(issue.get('description') or ''))}"
        comment_approval = any(
            str((row.get("user") or {}).get("id") or "") == approver_id
            and approval_marker in str(row.get("body") or "")
            for row in issue.get("comments", {}).get("nodes", [])
        )
        if comment_approval:
            return True, "human_spec_hash_approval_verified"
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
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryError(f"receipt is unreadable or invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise FactoryError(f"receipt is not a JSON object: {path}")
    return payload


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
        "- The outer Controller owns commit, push, draft PR creation, CI watching, "
        "and Linear transitions.\n"
        "- Complete the builder task when the requested file changes and local "
        "checks pass; do not block only because those outer steps are pending.\n"
        "- Never merge, enable auto-merge, publish, release, or deploy.\n"
    )


def _minimal_env(allowlist_name: str, defaults: str) -> dict[str, str]:
    denied_fragments = (
        "API_KEY",
        "PASSWORD",
        "SECRET",
        "TOKEN",
        "LINEAR_",
        "GH_",
        "GITHUB_",
    )
    allowed = {
        name.strip()
        for name in os.environ.get(allowlist_name, defaults).split(",")
        if name.strip()
    }
    unsafe = sorted(
        name
        for name in allowed
        if any(fragment in name.upper() for fragment in denied_fragments)
    )
    if unsafe:
        raise FactoryError(
            f"{allowlist_name} contains denied credential names: {', '.join(unsafe)}"
        )
    return {name: value for name, value in os.environ.items() if name in allowed}


def run_pipeline(
    *,
    config: FactoryConfig,
    issue: dict[str, Any],
    contract: dict[str, Any],
    repo_path: Path,
    base_ref: str,
    attempt: int = 1,
) -> dict[str, Any]:
    task_id = (
        f"linear-{slug(issue['identifier'], 30)}-"
        f"{contract['spec_sha256'][:12]}-a{attempt}"
    )
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
        base_ref,
        "--run-context",
        "cron",
        "--timeout",
        "1800",
        "--task-id",
        task_id,
        "--verification",
        str(contract["verification_command"] or "auto-detect repository verification"),
    ]
    env = _minimal_env(
        "LINEAR_FACTORY_PIPELINE_ENV_ALLOWLIST",
        "PATH,HOME,HERMES_HOME,LANG,LC_ALL,XDG_RUNTIME_DIR",
    )
    completed = run(command, cwd=repo_path, timeout=7200, env=env)
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


def _required_status_contexts(
    repo_path: Path, name_with_owner: str, default_branch: str
) -> list[str]:
    listed = run(
        ["gh", "api", f"repos/{name_with_owner}/rulesets"],
        cwd=repo_path,
        timeout=30,
    )
    if listed.returncode != 0:
        raise FactoryError("GitHub ruleset lookup failed")
    try:
        rulesets = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise FactoryError("GitHub ruleset lookup returned invalid JSON") from exc
    if not isinstance(rulesets, list):
        raise FactoryError("GitHub ruleset lookup returned a non-list")

    def ref_matches(values: list[Any]) -> bool:
        accepted = {"~ALL", "~DEFAULT_BRANCH", default_branch, f"refs/heads/{default_branch}"}
        return any(str(value) in accepted for value in values)

    required: set[str] = set()
    pull_request_required = False
    for summary in rulesets:
        if (
            not isinstance(summary, dict)
            or summary.get("target") != "branch"
            or summary.get("enforcement") != "active"
            or not summary.get("id")
        ):
            continue
        detail = run(
            ["gh", "api", f"repos/{name_with_owner}/rulesets/{summary['id']}"],
            cwd=repo_path,
            timeout=30,
        )
        if detail.returncode != 0:
            raise FactoryError(f"GitHub ruleset {summary['id']} lookup failed")
        try:
            payload = json.loads(detail.stdout)
        except json.JSONDecodeError as exc:
            raise FactoryError("GitHub ruleset detail returned invalid JSON") from exc
        ref_name = ((payload.get("conditions") or {}).get("ref_name") or {})
        includes = list(ref_name.get("include") or [])
        excludes = list(ref_name.get("exclude") or [])
        if not ref_matches(includes) or ref_matches(excludes):
            continue
        for rule in payload.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            if rule.get("type") == "pull_request":
                pull_request_required = True
            if rule.get("type") == "required_status_checks":
                parameters = rule.get("parameters") or {}
                for item in parameters.get("required_status_checks") or []:
                    if isinstance(item, dict) and str(item.get("context") or "").strip():
                        required.add(str(item["context"]).strip())
    if not pull_request_required or not required:
        raise FactoryError(
            "default branch lacks applicable pull-request and required-check rules"
        )
    return sorted(required)


def _pr_for_branch(
    repo_path: Path, branch: str, required_contexts: list[str]
) -> dict[str, Any]:
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
    checks = {
        str(item.get("name") or item.get("context") or ""): str(
            item.get("conclusion") or item.get("state") or ""
        ).upper()
        for item in payload.get("statusCheckRollup") or []
        if isinstance(item, dict)
    }
    missing = [name for name in required_contexts if checks.get(name) != "SUCCESS"]
    if missing:
        raise FactoryError(
            f"required GitHub checks are absent or not successful: {', '.join(missing)}"
        )
    if payload.get("mergeStateStatus") != "CLEAN":
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
    env = _minimal_env(
        "LINEAR_FACTORY_BROWSER_ENV_ALLOWLIST",
        "PATH,LANG,LC_ALL",
    )
    env.update(
        {
            "LINEAR_FACTORY_PR_NUMBER": str(pr["number"]),
            "LINEAR_FACTORY_PR_HEAD_SHA": str(pr["headRefOid"]),
        }
    )
    worktree = Path(tempfile.mkdtemp(prefix="linear-factory-browser-"))
    worktree.rmdir()
    added = run(
        ["git", "worktree", "add", "--detach", str(worktree), str(pr["headRefOid"])],
        cwd=repo_path,
        timeout=60,
    )
    if added.returncode != 0:
        raise FactoryError(f"exact-head browser worktree failed: {added.stderr[-800:]}")
    try:
        head = run(["git", "rev-parse", "HEAD"], cwd=worktree, timeout=10)
        if head.returncode != 0 or head.stdout.strip() != str(pr["headRefOid"]):
            raise FactoryError("browser worktree did not match the reviewed PR head")
        completed = run(
            ["/bin/sh", "-c", command], cwd=worktree, timeout=900, env=env
        )
        return {
            "required": True,
            "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "head_sha": head.stdout.strip(),
            "stdout_tail": redact_secrets(completed.stdout[-2000:]),
            "stderr_tail": redact_secrets(completed.stderr[-2000:]),
        }
    finally:
        run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo_path,
            timeout=60,
        )


def _set_pr_verdict(
    repo_path: Path,
    name_with_owner: str,
    pr: dict[str, Any],
    browser: dict[str, Any],
    issue: dict[str, Any],
) -> str:
    approved = browser.get("passed") is True
    verdict = "loop-approved" if approved else "needs-human-review"
    for label in ("loop-approved", "loop-changes-requested", "needs-human-review"):
        run(
            ["gh", "pr", "edit", str(pr["number"]), "--remove-label", label],
            cwd=repo_path,
            timeout=30,
        )
    status = run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{name_with_owner}/statuses/{pr['headRefOid']}",
            "-f",
            f"state={'success' if approved else 'failure'}",
            "-f",
            "context=factory/review",
            "-f",
            f"description={verdict}; human merge required",
            "-f",
            f"target_url={pr['url']}",
        ],
        cwd=repo_path,
        timeout=30,
    )
    if status.returncode != 0:
        raise FactoryError(f"could not apply commit-scoped verdict: {status.stderr[-800:]}")
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


def _clear_pr_verdict(
    repo_path: Path, name_with_owner: str, pr: dict[str, Any], reason: str
) -> None:
    for label in ("loop-approved", "loop-changes-requested", "needs-human-review"):
        run(
            ["gh", "pr", "edit", str(pr["number"]), "--remove-label", label],
            cwd=repo_path,
            timeout=30,
        )
    run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{name_with_owner}/statuses/{pr['headRefOid']}",
            "-f",
            "state=failure",
            "-f",
            "context=factory/review",
            "-f",
            f"description={reason[:120]}",
            "-f",
            f"target_url={pr['url']}",
        ],
        cwd=repo_path,
        timeout=30,
    )


def _open_prs_for_issue(repo_path: Path, issue: dict[str, Any]) -> list[dict[str, Any]]:
    completed = run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--search",
            f'"{issue["url"]}" in:body',
            "--json",
            "number,url,isDraft,headRefName,headRefOid,body",
        ],
        cwd=repo_path,
        timeout=60,
    )
    if completed.returncode != 0:
        raise FactoryError(f"issue-bound PR lookup failed: {completed.stderr[-800:]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FactoryError("issue-bound PR lookup returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise FactoryError("issue-bound PR lookup returned a non-list")
    return [
        dict(item)
        for item in payload
        if isinstance(item, dict) and str(issue["url"]) in str(item.get("body") or "")
    ]


def _refresh_authorization(
    client: LinearClient,
    config: FactoryConfig,
    issue_id: str,
    contract: dict[str, Any],
    ready_label_id: str,
) -> dict[str, Any]:
    issue = client.issue_snapshot(issue_id)
    issue.update(client.approval_evidence(issue_id))
    names = {
        str(label.get("name") or "")
        for label in issue.get("labels", {}).get("nodes", [])
        if isinstance(label, dict)
    }
    if config.ready_label not in names:
        raise FactoryError("human approval was revoked")
    if config.blocked_label in names:
        raise FactoryError("issue was blocked during factory execution")
    if (issue.get("state") or {}).get("type") in {"completed", "canceled"}:
        raise FactoryError("issue entered a terminal state during factory execution")
    if is_dependency_blocked(issue):
        raise FactoryError("issue dependency became blocked during factory execution")
    refreshed = validate_contract(issue)
    if refreshed["spec_sha256"] != contract["spec_sha256"]:
        raise FactoryError("issue specification changed during factory execution")
    approved, reason = human_approval_verified(
        issue,
        ready_label_id=ready_label_id,
        approver_id=config.human_approver_id,
    )
    if not approved:
        raise FactoryError(f"human approval became invalid: {reason}")
    return issue


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
            required_contexts = _required_status_contexts(
                path, name_with_owner, info["default_branch"]
            )
            repo_results[name_with_owner] = {
                "ok": True,
                "path": str(path),
                "default_branch": info["default_branch"],
                "required_status_contexts": required_contexts,
            }
        except FactoryError as exc:
            repo_results[name_with_owner] = {"ok": False, "error": str(exc)}
    return {
        "contract": CONTRACT,
        "ok": not missing_labels
        and bool(config.human_approver_id)
        and str(workspace["viewer"].get("id") or "") != config.human_approver_id
        and bool(config.repo_map)
        and all(item.get("ok") for item in repo_results.values())
        and config.herdr_adapter.is_file(),
        "team": config.team,
        "viewer": workspace["viewer"].get("name"),
        "human_approver_configured": bool(config.human_approver_id),
        "approver_is_independent": (
            str(workspace["viewer"].get("id") or "") != config.human_approver_id
        ),
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
        if not try_acquire_team_lock(lock):
            return {"contract": CONTRACT, "ok": True, "action": "busy", "team": config.team}
        workspace = client.workspace(config.team)
        viewer_id = str(workspace["viewer"].get("id") or "")
        if not config.human_approver_id:
            raise FactoryError("LINEAR_FACTORY_HUMAN_APPROVER_ID is not configured")
        if viewer_id == config.human_approver_id:
            raise FactoryError(
                "Linear execution identity must be independent from the human approver"
            )
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
        for raw_issue in client.eligible_issues(config.team, config.ready_label):
            issue = dict(raw_issue)
            eligible, reason = _eligible(issue, config)
            row: dict[str, Any] = {
                "identifier": issue["identifier"],
                "title": issue["title"],
                "eligible": eligible,
                "reason": reason,
            }
            if eligible:
                issue.update(client.approval_evidence(str(issue["id"])))
                approved, approval_reason = human_approval_verified(
                    issue,
                    ready_label_id=workspace_labels[config.ready_label],
                    approver_id=config.human_approver_id,
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
        issue = dict(
            next(
                item
                for item in client.eligible_issues(config.team, config.ready_label)
                if item["identifier"] == selected["identifier"]
            )
        )
        issue.update(client.approval_evidence(str(issue["id"])))
        still_eligible, current_reason = _eligible(issue, config)
        if not still_eligible:
            raise FactoryError(
                f"{issue['identifier']} changed before claim: {current_reason}"
            )
        approved, approval_reason = human_approval_verified(
            issue,
            ready_label_id=workspace_labels[config.ready_label],
            approver_id=config.human_approver_id,
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
        repository = inspect_repository(str(repo_path))
        if repository["name_with_owner"] != contract["name_with_owner"]:
            raise FactoryError("repository mapping changed after preflight")
        base_ref = f"origin/{repository['default_branch']}"
        required_contexts = _required_status_contexts(
            repo_path,
            contract["name_with_owner"],
            repository["default_branch"],
        )
        existing_prs = _open_prs_for_issue(repo_path, issue)
        if existing_prs:
            raise FactoryError(
                "an issue-bound open PR already exists; operator resume is required"
            )
        labels = label_map(workspace)
        previous_receipt = _load_receipt(config, issue["identifier"])
        previous_attempt = previous_receipt.get("attempt")
        attempt = previous_attempt + 1 if isinstance(previous_attempt, int) else 1
        started_state = state_by_type(workspace, "started", "In Progress")
        review_state = state_by_type(workspace, "started", "In Review")
        receipt = {
            "contract": CONTRACT,
            "issue_id": issue["id"],
            "identifier": issue["identifier"],
            "issue_url": issue["url"],
            "spec_sha256": contract["spec_sha256"],
            "repository": contract["name_with_owner"],
            "claimed_at": now_iso(),
            "claimed_by": None,
            "attempt": attempt,
            "status": "claiming",
            "human_merge_only": True,
            "base_ref": base_ref,
        }
        atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
        try:
            claimed = client.update_issue(
                issue["id"],
                assignee_id=str(workspace["viewer"]["id"]),
                state_id=started_state,
            )
            issue = _refresh_authorization(
                client,
                config,
                str(issue["id"]),
                contract,
                workspace_labels[config.ready_label],
            )
            receipt.update(
                {
                    "status": "running",
                    "claimed_by": claimed.get("assignee", {}).get("name"),
                }
            )
            atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
            client.comment(
                issue["id"],
                f"Factory claimed this exact specification (`{contract['spec_sha256']}`). "
                "Build is running in an isolated Herdr worktree. Human merge remains mandatory.",
            )
            pipeline = run_pipeline(
                config=config,
                issue=issue,
                contract=contract,
                repo_path=repo_path,
                base_ref=base_ref,
                attempt=attempt,
            )
            branch = str(pipeline.get("branch") or "")
            if not branch:
                raise FactoryError("Herdr result did not identify its isolated branch")
            pr = _pr_for_branch(repo_path, branch, required_contexts)
            receipt.update({"branch": branch, "pr": pr})
            atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
            changed = _changed_files(repo_path, branch)
            browser = _browser_gate(repo_path, pr, changed)
            rechecked_pr = _pr_for_branch(repo_path, branch, required_contexts)
            if rechecked_pr["headRefOid"] != pr["headRefOid"]:
                raise FactoryError("PR head changed during factory review")
            issue = _refresh_authorization(
                client,
                config,
                str(issue["id"]),
                contract,
                workspace_labels[config.ready_label],
            )
            verdict = _set_pr_verdict(
                repo_path,
                contract["name_with_owner"],
                rechecked_pr,
                browser,
                issue,
            )
            issue = _refresh_authorization(
                client,
                config,
                str(issue["id"]),
                contract,
                workspace_labels[config.ready_label],
            )
            receipt.update(
                {
                    "status": "merge_ready" if verdict == "loop-approved" else "blocked",
                    "finished_at": now_iso(),
                    "pr": rechecked_pr,
                    "changed_files": changed,
                    "browser": browser,
                    "verdict": verdict,
                    "herdr_task_id": pipeline.get("task_id"),
                    "herdr_state_path": pipeline.get("state_path"),
                }
            )
            client.update_issue(
                issue["id"],
                assignee_id=str(workspace["viewer"]["id"]),
                state_id=review_state,
            )
            client.remove_label(
                str(issue["id"]), workspace_labels[config.ready_label]
            )
            client.comment(
                issue["id"],
                f"Draft PR {rechecked_pr['url']} reviewed at "
                f"`{rechecked_pr['headRefOid']}`: **{verdict}**. "
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
            cleanup_errors: list[str] = []
            if isinstance(receipt.get("pr"), dict):
                try:
                    _clear_pr_verdict(
                        repo_path,
                        contract["name_with_owner"],
                        receipt["pr"],
                        "factory execution blocked",
                    )
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        redact_secrets(
                            f"GitHub cleanup: {type(cleanup_exc).__name__}: {cleanup_exc}"
                        )[:500]
                    )
            try:
                client.update_issue(
                    issue["id"],
                    assignee_id=None,
                    state_id=started_state,
                )
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    redact_secrets(
                        f"issue cleanup: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )[:500]
                )
            if config.blocked_label in labels:
                try:
                    client.add_label(str(issue["id"]), labels[config.blocked_label])
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        redact_secrets(
                            f"blocked label cleanup: {type(cleanup_exc).__name__}: "
                            f"{cleanup_exc}"
                        )[:500]
                    )
            try:
                client.comment(
                    issue["id"],
                    f"Factory stopped safely: `{receipt['error']}`. The issue is blocked; "
                    "no merge was attempted.",
                )
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    redact_secrets(
                        f"comment cleanup: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )[:500]
                )
            if cleanup_errors:
                receipt["cleanup_errors"] = cleanup_errors
                atomic_write_json(_receipt_path(config, issue["identifier"]), receipt)
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
        client = LinearClient(
            os.environ.get("LINEAR_FACTORY_API_KEY")
            or os.environ.get("LINEAR_API_KEY", "")
        )
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
