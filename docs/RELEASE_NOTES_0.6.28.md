# Agentic Harness v0.6.28

This release hardens autonomous completion, single-driver ownership, and the
browser control boundary before the durable-goal work leaves draft review.

## Changes

- Strict completion is monotonic across process and command changes. A strict
  goal cannot be resumed through a compatibility path that weakens its audit.
- Every Supervisor mutation participates in the autonomy lease, preventing a
  second CLI, GUI, or scheduler path from advancing an owned goal.
- Strict acceptance requires at least one independent deterministic review
  criterion; worker process success is recorded but is not independent proof.
- Checkpoint text remains durable narrative state but no longer proves progress
  or changes repeated-blocker identity. Workspace evidence drives continuation.
- GUI writes require same-origin browser requests and JSON bodies of at most
  1 MiB. Cross-origin WebSocket upgrades are rejected as well.
- Permanent backend invocation failures block for human action, while timeout
  and other retryable failures remain under background recovery.
- Installed wheel and sdist smoke tests now execute the strict `goal` path with
  structured completion evidence and an independent review command.

## Verification

- Full Python tests, Ruff, strict mypy, compileall, and JavaScript checks pass.
- Wheel and sdist build, Twine metadata, isolated installation, strict-goal
  execution, recipes, demos, and report generation pass through release smoke.
- Security regression tests cover cross-origin POST and WebSocket rejection,
  JSON-only writes, body limits, and permanent error classification.
