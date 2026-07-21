# Agentic Harness v0.13.2

Version 0.13.2 is a security and crash-consistency update for Verified Best-of-N.

## Security and correctness fixes

- Freezes verifier-sensitive paths that were absent before candidate execution,
  preventing candidate-added `pytest.py`, `pytest.ini`, `conftest.py`, runner
  definitions, and equivalent ecosystem files from weakening acceptance.
- Freezes protected directory membership so new test definitions, build files,
  or symlinks cannot enter the verifier boundary unnoticed.
- Starts Python `-m pytest` and `-m unittest` checks in safe-path mode to prevent
  repository-root module shadowing at interpreter startup.
- Requires repository-local custom verifiers to declare `review_assets`; declared
  directories freeze both their contents and membership.
- Reconciles a durably `verified` tournament after GUI restart only when the
  GoalSpec, final review, commit, and applied workspace fingerprint all match.
  Divergent state remains blocked.

## Managed GUI recovery

- Preserves the managed runtime's `needs_attention` state instead of flattening
  it into a generic blocker.
- Shows clear Continue, Stop safely, and Open current task decisions when an
  interrupted task prevents new work from starting.
- Keeps recovery scoped to the task. The browser does not receive authority to
  start or enable host background services.
- Retains the supervised OpenCode conversation and independent verification
  boundary while the user guides or resumes work. Conversation state follows a
  managed continuation only when its bounded, no-follow ticket explicitly
  names the prior sibling run; unrelated runs fail closed without inheriting
  task guidance.
- Binds a successful local GUI start to the exact harness ticket and requested
  completion criterion. If another client wins the local-lane race, the GUI
  rejects the start instead of attaching the pending objective or guidance to
  that other task.
- Forwards every Mode 1 completion check to `quick-start` as a preregistered
  verification command, so the independent reviewer receives the same frozen
  checks the user configured in the GUI. The requested objective is also the
  managed ticket title and done criterion, keeping evidence mapping bound to
  the user's task instead of a generic placeholder.
- Stores the managed GUI conversation and ownership ledger under the operator's
  state directory (`XDG_STATE_HOME`, or `~/.local/state`) instead of inside the
  worker-controlled project. Existing in-project GUI session metadata is not
  imported automatically; set `AGENTIC_HARNESS_GUI_SESSION_PATH` explicitly
  only when a legacy state file is trusted and migration is intentional.

## Compatibility

Verified Best-of-N configurations that use a repository-local custom verifier
must declare the complete repository-controlled dependency boundary with
`review.assets` or top-level `review_assets`. Built-in supported ecosystem
commands continue to infer their standard verifier boundary automatically.
