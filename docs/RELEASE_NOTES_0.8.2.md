# Agentic Harness v0.8.2

This maintenance release fixes the public GUI handoff seen when a user starts a
new goal while the previous task is still visible in the live status stream.

## Trustworthy goal starts

- A submitted goal remains visible while its start request is being accepted;
  late polling or WebSocket updates from the previous goal can no longer replace
  the optimistic new-goal state.
- The start response now includes the submitted objective and an explicit
  acceptance result, allowing the browser to distinguish accepted work from a
  readiness or review blocker.
- Rejected starts display the real blocker and preserve the user's draft for a
  retry instead of clearing the form or remaining stuck on a false starting
  state.
- If a mobile connection changes after submission, recovery adopts only the
  active task that matches the submitted objective.

## Complete requirements in live status

- When a local worker reports only a shortened title, the GUI session preserves
  the full submitted objective in the task and Requirements panel.
- Regression coverage exercises delayed starts, stale live updates, rejected
  starts, lost-response recovery, and long-objective reconciliation.
