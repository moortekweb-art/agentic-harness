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
  boundary while the user guides or resumes work.

## Compatibility

Verified Best-of-N configurations that use a repository-local custom verifier
must declare the complete repository-controlled dependency boundary with
`review.assets` or top-level `review_assets`. Built-in supported ecosystem
commands continue to infer their standard verifier boundary automatically.
