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

Verification requirements are reviewer-facing prose by default. To authorize
one bounded executable check, include exactly one `Command: ...` line or one
fenced `sh`, `bash`, or `shell` block in that section. Unmarked prose is never
passed to a shell; without an explicit command, Herdr uses repository-owned
verification discovery.

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

- `LINEAR_API_KEY`: Linear personal API key.
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

Only one import pass may hold the team lock. Assigned, blocked,
dependency-blocked, completed, canceled, malformed, or already-receipted issues
remain outside the queue. UI changes without a configured passing browser check
receive `needs-human-review`, never `loop-approved`.

## Verdicts

`loop-approved` means required CI and conflict checks were green at the exact
reviewed head SHA and every applicable structured verification gate passed. It
is evidence for the human merge decision, not permission to merge.

`needs-human-review` means the automated evidence was insufficient. A blocked
Linear issue is not retried until the human resolves it and removes `blocked`.
