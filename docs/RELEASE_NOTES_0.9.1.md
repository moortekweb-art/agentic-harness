# Agentic Harness v0.9.1

This corrective release makes the v0.9.0 first-success demo available when a
self-hosted GUI keeps its existing managed `local-goal` execution backend.

## Managed-backend first success

- The credential-free practice run now uses an isolated embedded overlay while
  the real managed workspace and its current task remain untouched.
- Demo status, history, events, evidence previews, and WebSocket updates are
  routed through the overlay only while the practice task is visible.
- A terminal demo can be dismissed with **Return to real workspace**, restoring
  the unchanged managed task instead of forcing a backend switch or setup
  change.
- Starting, continuing, accepting, stopping, or dismissing a practice task
  never issues a `local-goal` command.

## Safety boundary

- The scripted practice worker still uses no AI model, provider credential, or
  selected-workspace access.
- Temporary demo paths remain private and are not returned in task payloads.
- Existing managed execution, provider routing, workspace state, and background
  supervision remain authoritative after the overlay is dismissed.

## Verification

- Managed and embedded demo API journeys cover false completion, repair,
  independent verification, terminal evidence, and return to the real task.
- The complete Python, frontend, lint, typecheck, compile, package, and release
  smoke gates are required before tagging this release.
