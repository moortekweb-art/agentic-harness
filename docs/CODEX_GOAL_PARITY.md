# Codex `/goal` Parity Contract

## Scope

Agentic Harness does not copy a private agent runtime or expose hidden model
reasoning. It implements the observable operator contract that makes a long
goal useful: one complete objective, durable planning, bounded autonomous
repair, visible progress, preserved evidence, and acceptance only after an
independent check.

The public persisted contracts are:

- `agentic_harness.autonomy.v1` for execution state.
- `agentic_harness.completion_audit.v1` for acceptance evidence.
- `agentic_harness.task_event.v1` for ordered progress evidence.
- `agentic_harness.gui_task.v2` for the human-facing task view.

Changing these shapes requires an explicit compatibility or migration decision.
Endpoint, model ID, and worker implementation may change without changing the
goal contract.

## Operator contract

The operator supplies one plain-English objective through either interface:

```bash
agentic-harness do "fix the failing tests" --check "python -m pytest tests/ -q"
agentic-harness gui
```

The runner then owns routine execution decisions. It must:

1. Preserve the immutable original objective across continuation and recovery.
2. Persist a plan, requirements, current subgoal, and checkpoint.
3. Inspect current workspace state rather than trust stale narrative state.
4. Treat worker errors, failed checks, and review findings as repair input.
5. Record actual tool activity as ordered, sanitized events.
6. Continue while useful progress and configured resource budgets remain.
7. Escalate only after a repeated no-progress blocker, a hard safety boundary,
   cancellation, missing authority, or a depleted budget.
8. Accept completion only after a structured audit and at least one independent
   deterministic criterion pass.

Elapsed time, token use, attempt count, worker exit code, or confident prose can
never establish completion by themselves.

The interfaces present the same trusted result categories:

- `Verified done` means the structured worker claim and configured independent
  completion gate passed. In the v1 contract, requirements are worker-derived;
  this category is check-gated acceptance, not independent proof that every
  objective clause was captured.
- `Blocked with reason` names the operator decision, authority, credential, or
  resource required before useful progress can continue.
- `Failed with evidence` preserves the failed execution or verification result
  and its durable evidence.

Worker-authored prose cannot select any of these categories.

## Perceive, plan, act, evaluate, iterate

The built-in loop applies the useful parts of the supplied agent-loop analysis:

- **Perceive** through bounded workspace reads, search, Git state, and prior
  durable events.
- **Plan** through a persisted checklist and explicit requirements.
- **Act** through a narrow tool registry or a separately installed coding agent.
- **Evaluate** through configured checks and the independent reviewer.
- **Iterate** from the checkpoint until acceptance or an honest terminal state.

The GUI renders those observable stages. It does not display or persist private
chain-of-thought, raw prompts, or model-provider payloads.

## Durable state and events

The active goal lives below `.agentic-harness/runs/<goal-id>/state.json` and
includes:

- SHA-256 identity of the original objective;
- cycle, heartbeat, plan, requirements, subgoal, and checkpoint;
- last structured worker outcome and completion audit;
- blocker signature and consecutive count;
- budget limits and measured usage;
- cancellation and operator-intervention state; and
- acceptance metadata.

Each activity event is a separate atomic file below
`.agentic-harness/runs/<goal-id>/events/`. Separate event files let the GUI
observe activity while a worker cycle is running without contending for the
goal-state lock.

An interrupted CLI run or restarted GUI service retains the goal ID, objective,
workspace baseline, checkpoint, history, and evidence. Environment-referenced
credentials resolve again at use time. Memory-only credentials must be
re-entered and are never reconstructed from disk.

## Worker result

Installed coding agents end a strict cycle with a machine-readable
`HARNESS_RESULT_JSON` record. The embedded model worker uses its bounded
`report_outcome` action. Both normalize to the same fields:

- `status`: `progress`, `blocked`, or `complete`;
- non-empty plain summary, current subgoal, and checkpoint;
- plan items with explicit status;
- stable requirement IDs with status and evidence;
- blocker list; and
- measured usage and check evidence where available.

`progress` means another cycle is useful. `blocked` records the condition and
its repeated signature. `complete` is a claim to audit, never permission to set
`Verified done` directly. Malformed or incomplete output becomes repair
feedback.

## Completion gate

Strict acceptance requires all of the following:

- a structured `complete` claim;
- non-empty summary, current subgoal, and checkpoint;
- a non-empty plan with every item completed;
- at least one harness-frozen requirement;
- every harness-frozen requirement satisfied with non-empty recognized
  current-run evidence;
- an explicit empty blocker list;
- at least one deterministic review criterion executed;
- every deterministic criterion passed; and
- at least one passing criterion independent of the worker's own claim.

If a condition fails, the goal returns to repair. It becomes human-blocked only
when the same no-progress condition reaches the configured threshold or a hard
boundary already requires a person.

Check-gated mode intentionally makes only a check-gated acceptance claim.
Specification-frozen and high-assurance embedded runs instead use the v0.12
harness-owned GoalSpec and typed coverage contract. Legacy external or v1
worker-derived requirement paths do not by themselves
prove that every semantic clause of the original objective appears in that
decomposition.

## Resource budgets

The operator may bound:

- autonomy cycles;
- elapsed seconds;
- total provider tokens;
- provider calls; and
- tool calls.

Usage is accumulated across cycles and shown in task metadata. Reaching a limit
persists `Blocked with reason` or `Failed with evidence`; it never converts
unfinished work into `Verified done`. A completion claim delivered exactly at a
permitted limit may still pass the independent audit.

## Concurrency, cancellation, and recovery

`state.lock` protects short atomic transitions. `autonomy.lock` leases the full
decision cycle to one driver, so CLI, GUI, and scheduler processes cannot
interleave mutations. A project supports one active goal pointer; separate
project roots are required for independent concurrent goals.

Stop is cooperative. The cancellation token is checked at safe boundaries and
again before acceptance. Evidence remains, the terminal state is recorded, and
a late worker result cannot turn a stopped task into `Verified done`. Continue
preserves the original objective and accepts an optional operator note as new
context.

## Safety boundary

The embedded model worker has bounded text, Git-inspection, and configured-check
actions. It has no arbitrary shell, delete, install, service control, publish,
or general network tool. It enforces workspace containment, selected path
scope, protected secret/state files, optimistic-concurrency hashes, and
pre-existing-change ownership.

An installed coding-agent executable runs under that tool's own authorization
and sandbox policy; Agentic Harness can pass scope and inspect results but
cannot enforce the embedded path policy inside another executable. The GUI
states that distinction.

Remote-compatible providers require explicit data-transfer consent. API keys
are environment references or memory-only session values. Destructive machine
operations, account changes, billing, provider dashboards, secrets, and public
deployment remain outside implied goal authority.

## Turnstone boundary

Turnstone may be used as an optional external orchestration backend. It is not
bundled, is not required for the default CLI or GUI flow, and cannot self-accept
a result: normalized evidence must still cross the harness completion gate. See
[Turnstone integration](TURNSTONE_INTEGRATION.md).

## Upgrade verification

Changes to the goal system must test:

1. More than five evidence-backed progress cycles without false loop failure.
2. Repeated identical no-progress escalation at the configured threshold.
3. Restart from a persisted checkpoint.
4. Competing drivers and single-lease enforcement.
5. Missing or invented evidence rejection.
6. Deterministic review failure followed by repair.
7. Exact budget boundaries and non-completion on exhaustion.
8. Cooperative cancellation before acceptance.
9. Durable, ordered, sanitized progress events.
10. Local no-key and cloud bearer provider flows with arbitrary model IDs.
11. Session credential absence from state, events, history, URLs, exports, and
    transcripts.
12. A clean installed-wheel start-to-result journey through both interfaces.
