# Agentic Harness v0.8.1

This maintenance release fixes the first real public v0.8.0 coding-agent run:
the agent repaired the workspace and the independent check passed, but the GUI
retried the successful work and eventually displayed a false blocked result.

## Reliable completion parsing

- Coding-agent results may use either `HARNESS_RESULT_JSON=<object>` or a JSON
  wrapper whose `HARNESS_RESULT_JSON` property contains the result.
- Pretty-printed multiline results are accepted, and a later malformed marker
  no longer erases an earlier valid completion result.
- The common completion statuses `complete`, `completed`, and `done` are
  normalized without weakening the independent verification gate.
- The worker instruction now gives an exact requirement/evidence schema and
  tells agents not to submit their private tool-call IDs as harness evidence.

The original calculator repair was replayed with the real Codex CLI. It reached
Verified done in one agent pass with zero retries, one independently passing
unittest command, and only `calculator.py` changed.

## Honest setup and bounded retry behavior

- Installed coding agents are now labeled as installed but untested until a
  live connection/model probe succeeds in the current GUI session.
- Setup includes an explicit **Test coding agent** action. Codex is probed with
  read-only sandboxing, so configuration, authentication, CLI compatibility,
  and configured-model errors are shown before normal work begins.
- Other installed agents receive a non-mutating executable check and retain an
  honest notice that live model compatibility will be checked on their first
  run.
- Permanent launch and configuration failures stop after the first attempt
  instead of spending the normal three-attempt no-progress budget.

Independent checks remain mandatory. A coding agent's prose or structured
claim still cannot turn failed or missing verification into success.
