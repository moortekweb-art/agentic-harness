# Persistent Orchestrator Guide

This guide is for operators who run a coding agent through a persistent
terminal multiplexer or an external orchestrator, and then bring the result
back to Agentic Harness for acceptance. A terminal session can keep a process
alive across reconnects and panes; it cannot make work complete. Agentic
Harness keeps that distinction strict.

## The split between layers

Session persistence, panes, windows, and reconnection belong to the terminal
or orchestrator layer. They keep a process running and are a convenience, not
an acceptance mechanism.

Agentic Harness evidence plus independent review remains the acceptance
authority. A surviving pane, an agent still running, or text that looks done in
the terminal is never, by itself, a completion result.

## Keep the workspace fixed

Use one fixed repository or worktree and one goal ID for the whole task. Point
both the terminal session and Agentic Harness at the same working tree so the
review sees the exact files the worker changed. Do not let the worker switch
roots mid-task; a goal and its evidence must stay bound to one workspace.

## Trust durable artifacts, not pane text

Trust durable artifacts rather than scraping pane text. The terminal scrollback
can be truncated, wrapped, edited, or replayed, and the same pane may contain
output from unrelated commands. Acceptance is based on the harness goal state,
recorded events, the worker report, and the independent verification result
that the harness persisted for that goal ID. Read those through the harness; do
not parse them out of the terminal.

## Keep builder and reviewer roles distinct

Keep builder and reviewer roles distinct. The process that implements the work
is the builder. The harness review and the deterministic check it runs are the
reviewer. The builder must not grade its own work, and a reviewer step must not
be replaced by the builder asserting that it already passed. If the same person
or tool operates both, keep the two acts separate and let the harness record
each one.

## Require verification before a completion claim

Require verification before a completion claim. A completion claim is accepted
only after the configured independent check passes and the harness records the
evidence for that goal ID. Reaching the end of a script, a confident summary, a
clean pane, or a worker exit code is not verification.

## Completion labels

Use only these public completion labels, exactly as written:

- **Verified done** — the structured completion claim and the configured
  independent review both passed for the current goal ID.
- **Blocked with reason** — useful progress stopped because an operator
  decision, authority, credential, or resource is required before work can
  continue. The reason is recorded with the goal.
- **Failed with evidence** — execution or independent review failed, and the
  durable evidence for that goal ID is preserved.

Worker-authored prose cannot select any of these labels. A terminal that
prints one of them is still only text until the harness accepts the result for
the goal ID.

## What the terminal or orchestrator may and may not do

The terminal or orchestrator layer may keep a long session alive, reconnect
after a dropped connection, show several panes at once, and run the same
project command repeatedly. It is a transport for a human or worker process.

It may not define what finished means, mint completion evidence, replace the
harness review, or turn a late or partial worker result into an accepted
result. Those belong to Agentic Harness for the goal ID.

## Related guides

- [How Completion Works](https://github.com/moortekweb-art/agentic-harness/blob/main/README.md#how-completion-works)
  in the README for the objective to verified-result flow.
- [Evidence contract](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/EVIDENCE_CONTRACT.md)
  for why worker prose alone is not evidence.
- [Autonomous goal contract](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/CODEX_GOAL_PARITY.md#completion-gate)
  for the structured completion gate.
- [Optional orchestration ownership](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/TURNSTONE_INTEGRATION.md#security-and-ownership)
  for the external-orchestration ownership model.
