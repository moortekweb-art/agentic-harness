# Agentic Harness v0.13.0

> **Security notice:** Do not use v0.13.0 for unattended Verified Best-of-N
> acceptance. Its frozen verifier boundary can omit repository-local command
> executables and several supported ecosystem definitions. Upgrade to v0.13.1.

Version 0.13.0 adds fail-closed, verified multi-approach execution and an
official Grok Build worker profile without weakening the v0.12 assurance
contracts.

## Verified Best-of-N

- Adds `agentic-harness best-of-n` and the equivalent
  `verified-best-of-n` alias for two to ten concurrent candidates.
- Runs every candidate from the same commit, immutable GoalSpec, and frozen
  verification command set in an isolated Git worktree.
- Hashes pre-existing verifier inputs and disqualifies candidates that alter
  them, even when the altered check exits successfully.
- Selects only from independently verified non-empty patches, applies the
  smallest verified patch deterministically, and runs the same checks again in
  the original workspace.
- Rolls back the applied patch and reports blocked if final verification fails,
  errors, or cannot be preserved durably.
- Stores private, checksummed candidate patches and a versioned tournament
  receipt under `.agentic-harness/tournaments/`.

## GUI

- Adds a plain-language **Implementation approaches** choice to the embedded
  GUI: one approach for speed or three verified approaches for stronger search.
- Runs tournaments in the existing background task lifecycle with cooperative
  stop behavior, durable progress, recovery that fails closed after an
  interrupted process, and a final result backed by the tournament verifier.
- Shows the number of approaches and winning candidate in public task evidence;
  managed external routes remain single-approach unless their own contract
  explicitly supports tournaments.

## Grok Build

- Adds `agentic-harness init-agent grok` and a **Grok Build** Settings option.
- Uses Grok Build's documented headless `grok -p` interface with plain output,
  bounded turns, automatic updates disabled, and its OS-enforced `workspace`
  sandbox.
- Denies `git push` and `sudo` shell commands in the starter profile. Grok
  still owns its credentials and provider traffic, and headless edits require
  bypass permissions inside the bounded workspace; this is not a local-only
  execution path.

## Compatibility and assurance

- No GoalSpec, evidence-v2, assurance-mode, or amendment schema changes.
- Existing single-worker CLI and GUI paths retain their prior behavior.
- High-assurance tournament specification approval remains explicitly blocked
  rather than silently downgraded.
- Git worktrees isolate candidate changes but are not a general security
  boundary for arbitrary external agents. Use each agent's supported OS sandbox
  and review its tool policy.
