# Autonomy Audit - 2026-07-10

## Scope

This review compared Agentic Harness behavior with the observable operator
contract of Codex CLI `/goal`: one complete objective, autonomous planning and
repair, durable recovery, evidence-based completion, and no routine human
continuation.

The implementation was rebased from the local `v0.6.25` checkout onto the
published `v0.6.27` baseline before live installation. This retained the newer
GUI token, loopback, browser-open, dynamic-port, and portable document-root
hardening.

## Findings Resolved

### High - Total attempts were treated as failure

The old `run-until-done` path stopped after a fixed number of worker attempts,
even when every cycle changed the workspace. `AutonomousRunner` now uses a
consecutive identical no-progress blocker threshold. Meaningful progress may
use more than five cycles without tripping the guard.

### High - Unevidenced progress could run forever

A worker could repeatedly return `status: progress` without changing anything
and reset the old guard. Progress now requires a workspace change. Checkpoint
text remains durable context but cannot reset the guard by itself. Repeated
unchanged progress claims count toward operator escalation.

### High - Completion was not a strict claim-and-audit protocol

The coding-agent adapter now parses a final `HARNESS_RESULT_JSON` record. Strict
completion requires a summary, current subgoal, checkpoint, completed plan,
identified requirements with non-empty evidence, an explicit empty blockers
list, and at least one independent deterministic review criterion. Worker
process success alone is not independent proof. Acceptance is persisted only
after that audit, and strictness cannot be downgraded when another command
resumes the goal.

### High - Concurrent drivers could interleave goal decisions

Short state writes were locked, but separate GUI, CLI, or scheduler processes
could still make overlapping continuation decisions. A project-local
`autonomy.lock` now leases the full run or resumed step to one driver, and every
mutating Supervisor entry point acquires or proves ownership of that lease.
`state.lock` continues to protect atomic transitions.

### High - Human Mode could queue work without a verified owner

The GUI health badge previously claimed no-babysitting behavior unconditionally,
and the task API did not enforce it. Human Mode and GUI task creation now fail
closed unless `capabilities --json` proves an active background watcher.

### Medium - A global loop guard leaked across independent goals

Starting a new goal now resets prior loop-guard history. Progress cycles also
reset the legacy continuation guard so a later independent goal does not inherit
another goal's safety events.

### Medium - Restart erased the original workspace baseline

Failed-attempt restart now retains the initial workspace snapshot. Final reports
therefore include changes made during every repair attempt, not only the last
one.

### Medium - Review and acceptance evidence could be lost

Failed deterministic reviews are archived before restart. Review can run without
prematurely transitioning to done, and acceptance metadata is persisted even
when an older path already placed a reviewed goal in `done`.

### Medium - Durable state did not fully drive the next worker

Continuation instructions now include the original objective, current subgoal,
checkpoint, persisted plan, persisted requirements, and prior feedback. A
process restart resumes the same goal ID and rejects state whose objective no
longer matches the original SHA-256 identity.

### Medium - GUI encouraged babysitting

The visible Move Forward control and Ctrl-M shortcut were removed. Continue and
Accept are hidden unless an exceptional review or blocker state requires them.
The Start control remains disabled until both task readiness and watcher
ownership are verified.

### Medium - Backend command errors were over-escalated

Local-goal calls now have a timeout and normalize timeout/start failures into
structured results. Timeout and generic transient failures remain recoverable;
invalid invocations and missing executables become human-visible blockers. A
stopped-incomplete run remains under background recovery unless the
repeated-blocker contract explicitly requests intervention.

### High - Proxied GUI writes lacked browser-origin enforcement

The GUI now rejects cross-origin state-changing requests and WebSocket upgrades,
requires JSON for API writes, and caps request bodies at 1 MiB. This preserves a
usable loopback server behind a private Tailscale-style reverse proxy while
preventing unrelated browser origins from triggering task actions.

## Public And Private Boundary

The public package does not import or depend on Turnstone. `LocalGoalBridge`
uses the portable local-goal command contract and the optional
`AGENTIC_HARNESS_LOCAL_GOAL` executable override. This machine points that
override at a Turnstone-compatible wrapper. Turnstone source, services, runtime
state, and releases stay in their separate repository.

The Turnstone repository remained clean during this work. Only the pipx-installed
Agentic Harness `0.6.27` wheel and `agentic-harness-gui.service` were updated.

## Verification Evidence

- Full source suite: `731 passed`.
- Ruff: all checks passed.
- Mypy strict package check: no issues in 27 source files.
- Python compileall: passed.
- JavaScript syntax and concurrent token-prompt regression: passed.
- Wheel and sdist release smoke: passed, including Twine metadata checks,
  isolated installs, strict autonomous goals with independent review, recipes,
  demos, reports, and final demo tests.
- Real unattended goal pilot: two cycles, durable plan handoff, independent
  review passed, completion audit passed, and acceptance persisted.
- Live service: active on `127.0.0.1:8769` with the Turnstone watcher reported
  active through `capabilities --json`.
- Playwright: desktop 1440x1000 and mobile 390x844 passed with four modes, no
  horizontal overflow, no overlapping/off-screen controls, and no page errors.

## Residual Limits

- Generic `agentic-harness goal` is an autonomous foreground process. It is
  resumable after interruption but is not itself a daemon.
- Structured evidence is model-proposed. Deterministic project checks remain
  the independent trust boundary and should be meaningful for the objective.
- One project has one active-goal pointer; simultaneous independent goals need
  separate project roots.
- Checkpoint text is narrative state and does not count as progress. Workspace
  evidence prevents label churn, but deterministic checks must still establish
  semantic correctness.
- Destructive machine operations, secrets, billing, routing, and provider
  dashboards remain outside implied autonomous authority.

These are explicit architecture and safety limits. They do not require routine
operator monitoring of normal work.
