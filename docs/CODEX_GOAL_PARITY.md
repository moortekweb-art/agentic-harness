# Codex `/goal` Parity Contract

## Purpose

Agentic Harness cannot copy Codex's private runtime. It can implement the
observable engineering contract that matters to an operator: provide one full
objective, let the system plan and repair without routine prompting, and stop
only when completion is proven or a real blocker needs a person.

The versioned public contracts are:

- `agentic_harness.autonomy.v1` for durable execution state.
- `agentic_harness.completion_audit.v1` for the final acceptance decision.

Changing these persisted shapes requires an explicit compatibility or migration
decision. Provider prompts, model names, and local sidecars may evolve without
changing the contract.

## Operator Contract

The operator supplies one plain-English objective:

```bash
agentic-harness goal "implement the requested change and verify it end to end"
```

The runner then owns routine execution decisions. It must:

1. Preserve the complete original objective across continuation and recovery.
2. Derive and persist a plan, requirement list, current subgoal, and checkpoint.
3. Inspect current workspace state instead of trusting stale narrative state.
4. Treat worker errors, failed checks, and review findings as repair input.
5. Continue for as many cycles as useful progress requires.
6. Request a person only when the same blocker repeats without progress for the
   configured number of consecutive cycles.
7. Reject completion based only on effort, elapsed time, attempts, context, or
   token use.
8. Accept completion only after the structured audit and at least one
   independent deterministic review criterion both pass.

The default repeated-blocker threshold is three. A workspace change alters the
progress signature and resets the consecutive count. Checkpoint text remains
useful durable context, but changing it alone does not prove progress or reset a
repeated blocker.

## Durable State

Autonomy state lives in the active goal artifact below
`.agentic-harness/runs/<goal-id>/state.json`. It includes:

- SHA-256 identity of the immutable original objective.
- Cycle count and latest heartbeat.
- Current plan, requirements, subgoal, and checkpoint.
- Last structured worker outcome.
- Completion-audit evidence.
- Normalized blocker signature and consecutive count.
- Whether operator intervention is genuinely required.

An interrupted foreground `agentic-harness goal` process can be resumed by
running `agentic-harness goal` with no new objective. The original goal ID,
workspace baseline, checkpoint, and history are retained.

## Structured Worker Result

A strict autonomy worker ends its output with one line:

```text
HARNESS_RESULT_JSON={"status":"complete","summary":"verified","plan":[{"step":"verify","status":"completed"}],"current_subgoal":"final audit","checkpoint":"verified","requirements":[{"id":"requested-outcome","status":"satisfied","evidence":["tests passed"]}],"blockers":[]}
```

Valid statuses are `progress`, `blocked`, and `complete`.

- `progress` means useful work occurred and another cycle should start.
- `blocked` records the condition but does not immediately ask a person.
- `complete` is a claim to audit, not permission to mark the goal done.

Every completion requirement must be a structured item with a stable ID,
`status: satisfied`, and a non-empty evidence list. A malformed or incomplete
claim becomes repair feedback.

## Completion Gate

Strict completion requires all of the following:

- The worker claims `complete` in the structured result.
- Summary, current subgoal, and checkpoint fields are non-empty.
- The structured plan is non-empty and every plan item is completed.
- At least one derived requirement is present.
- Every requirement has an ID, is satisfied, and contains non-empty evidence.
- The result contains an explicit empty blockers list.
- The deterministic reviewer ran at least one criterion.
- Every deterministic criterion passed.
- At least one passing criterion is independent of the worker's own success
  claim.
- Acceptance metadata is persisted after the audit.

If one condition fails, the goal returns to repair. It becomes human-blocked
only when the resulting no-progress condition repeats at the configured
threshold.

## Concurrency And Recovery

The harness uses two project-local locks:

- `state.lock` protects short atomic state transitions and artifact writes.
- `autonomy.lock` leases planning and continuation decisions to one autonomous
  driver for the complete run or single resumed step.

Every mutating Supervisor entry point acquires the autonomy lease for its
operation or proves that it owns the existing long-running lease. This prevents
a GUI process, CLI process, and scheduler from running duplicate workers or
reviewing different snapshots of the same goal. Atomic writes and the
current-goal marker allow process restart without reconstructing state from
terminal output.

The original workspace snapshot is immutable across failed-attempt restarts so
the final report still shows all work performed for the goal.

## Foreground And Background Ownership

`agentic-harness goal` is an autonomous foreground driver. It requires no
routine decisions while running, and it is resumable after interruption.

Human Mode commands such as `agentic-harness do` use the local-goal bridge. They
queue only when `capabilities --json` proves an active background watcher. Once
queued, the watcher owns continuation, repair, dispatch, review, and acceptance,
so the terminal and browser may be closed. `check` reports status; `watch` and
`mode3a-monitor` are diagnostic controls, not required workflow steps.

## Turnstone Boundary

Turnstone is an optional machine-local sidecar, not a Python dependency and not
part of the public repository. Integration uses this narrow boundary:

```text
Agentic Harness GUI/CLI
        |
        | LocalGoalBridge command contract
        v
AGENTIC_HARNESS_LOCAL_GOAL
        |
        +-- stock local-goal implementation, or
        +-- Turnstone-compatible wrapper on this machine
```

The wrapper must preserve the local-goal commands used by the bridge and expose
watcher truth through `capabilities --json`. Turnstone-specific services,
configuration, state, and release history stay in their own repository. This
avoids GitHub conflicts while allowing private operational improvements.

## Parity And Known Limits

Implemented parity:

- One full objective instead of manually authored goal packets.
- Durable plan, requirement, subgoal, checkpoint, and heartbeat state.
- Progress-aware continuation with no total-attempt cutoff.
- Repair loops for worker and review failures.
- Repeated identical-blocker escalation.
- Strict evidence plus deterministic completion review.
- Process-resumable state and single-driver concurrency control.
- Verified background ownership for Human Mode and the GUI.

Deliberate limits:

- The generic `goal` command is foreground, not a system daemon.
- A model can propose inaccurate evidence; at least one independent
  deterministic project check remains required for strict completion.
- Destructive operations, secrets, provider dashboards, billing, and broad
  machine changes still require explicit policy and are not implied by autonomy.
- Multiple independent simultaneous goals in one project are not supported; one
  project has one active-goal pointer.

These limits are safety and architecture boundaries, not instructions for the
operator to babysit normal work.

## Upgrade Checklist

When changing the harness or a sidecar:

1. Preserve or migrate both versioned contract shapes.
2. Test more than five workspace-backed progress cycles without false loop
   failure.
3. Test three identical no-progress cycles and verify escalation occurs only on
   the third observation.
4. Test a process restart from a persisted checkpoint.
5. Test competing autonomous drivers and verify only one acquires the lease.
6. Test missing or malformed completion evidence and verify it cannot be
   accepted.
7. Test deterministic review failure followed by autonomous repair.
8. Test GUI task creation with the watcher active and inactive.
9. Run the complete test, lint, type, compile, and installed-package smoke suite.
10. Test that a strict goal cannot be downgraded by a compatibility resume path.
11. Test direct Supervisor mutation while another driver owns the autonomy
    lease.
