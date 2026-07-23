# Human-Gated Linear Coding Factory

`agentic-factory` is an optional operator integration. It uses Linear for the
requested scope and approval state, Herdr plus Agentic Harness for isolated
implementation and independent review, and GitHub for the draft PR and exact
reviewed commit.

It does not merge, enable auto-merge, publish, release, or deploy.

## Required contract

Each approved issue must contain:

- `## Requested outcome`
- `## Acceptance criteria` with unique `AC-N` identifiers
- `## Non-goals` with unique `NG-N` identifiers
- `## Verification requirements`
- `## Approval boundary`
- a `Target repository: https://github.com/OWNER/REPO` line

The intake command creates `spec-drafted` issues. It never applies
`agent-ready`; that label is the human approval boundary.

Verification requirements are reviewer-facing prose. Issue content cannot
authorize shell commands; `Command:` lines and fenced shell blocks fail closed.
Herdr uses repository-owned verification discovery.

## Commands

Read-only preflight:

```bash
agentic-factory doctor
```

Draft a specification. Missing required decisions are asked interactively:

```bash
agentic-factory draft \
  --idea "rough idea" \
  --title "bounded change" \
  --repo /path/to/repository
```

List the next eligible issue without claiming it:

```bash
agentic-factory import-once
```

Claim and execute at most one issue:

```bash
agentic-factory import-once --act
```

The production scheduler should run the one-pass command rather than keeping an
interactive agent process alive. Templates are in `ops/systemd/`.

## Production environment

- `LINEAR_FACTORY_API_KEY`: Linear API key for a dedicated execution identity.
  The execution identity must be different from the configured human approver.
  `LINEAR_API_KEY` remains a compatibility fallback for interactive use, but
  the production doctor rejects it when it resolves to the approver.
- `LINEAR_FACTORY_HUMAN_APPROVER_ID`: immutable Linear user ID authorized to
  approve exact specifications. The factory reads the complete paginated
  approval history and fails closed if it cannot prove this identity approved.
- `LINEAR_FACTORY_TEAM`: one team key, such as `AI`.
- `LINEAR_FACTORY_REPO_MAP`: JSON map from allowed `owner/repo` to the local
  source checkout used to create clean worktrees.
- `LINEAR_FACTORY_GITHUB_OWNER`: required GitHub owner allowlist.
- `LINEAR_FACTORY_WORKER`: registered Herdr implementation worker allowed for
  the scheduler's run context. The production timer must use a cron-eligible
  non-premium worker resolved from Controller policy; `codex` remains a manual
  lane and is rejected by the unattended paid-provider guard.
- `LINEAR_FACTORY_HERDR_ADAPTER`: installed Controller Herdr adapter.
- `LINEAR_FACTORY_STATE_ROOT`: durable receipt and lock directory.
- `LINEAR_FACTORY_BROWSER_VERIFY_CMD`: optional repository-owned browser check.
- `LINEAR_FACTORY_BROWSER_ENV_ALLOWLIST`: comma-separated environment names
  exposed to the browser verifier. It defaults to `PATH,LANG,LC_ALL`;
  factory credentials are not inherited.
- `LINEAR_FACTORY_PIPELINE_ENV_ALLOWLIST`: non-secret environment names exposed
  to the Herdr adapter. Credential-shaped names are rejected even if listed.

Only one import pass may hold the team lock. Assigned, blocked,
dependency-blocked, completed, canceled, malformed, or already-receipted issues
remain outside the queue. UI changes without a configured passing browser check
receive `needs-human-review`, never `loop-approved`.

`agentic-factory doctor` fails unless the execution token resolves to a Linear
viewer different from `LINEAR_FACTORY_HUMAN_APPROVER_ID`. Keep the timer
disabled until that check reports both `human_approver_configured: true` and
`approver_is_independent: true`.

## Verdicts

`loop-approved` is recorded as a commit-scoped `factory/review` GitHub status
only when every exact required ruleset context, conflict check, authorization
refresh, and applicable exact-head browser gate passes. It is evidence for the
human merge decision, not permission to merge. Unscoped PR verdict labels are
removed.

`needs-human-review` means the automated evidence was insufficient. A blocked
Linear issue is not retried until the human resolves it and removes `blocked`.
