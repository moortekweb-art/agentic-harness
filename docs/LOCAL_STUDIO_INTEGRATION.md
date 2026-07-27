# Local Studio integration contract

Status: design and contract-test scaffold; not enabled by default.

This document defines how [Local Studio](https://github.com/sybil-solutions/local-studio)
can become an execution lane for Agentic Harness without creating a second
completion authority.

## Decision

The systems remain separate:

```text
Hermes Controller
  cluster routing and registered-worker ownership
        |
Agentic Harness
  immutable specification, worker isolation, independent review, receipt
        |
Local Studio
  Workbench UI, model lifecycle, Pi sessions, subagents, and execution
```

Local Studio is the operator and execution surface. Agentic Harness owns the
definition of done. Hermes remains the cluster-level router. A Local Studio
message such as `GOAL_COMPLETE`, a non-empty response, or a successful session
exit is an untrusted worker signal; none is a Harness acceptance receipt.

This matches the existing Local Studio design, where the controller manages
models, sessions, goals, and automations, and the existing Harness design,
where worker output is untrusted and independent checks decide acceptance.

## First integration slice

The first live vertical should be deliberately small:

```text
Harness dispatch
  -> Local Studio session
  -> redacted evidence collection
  -> Harness independent review
  -> Harness receipt
```

The Local Studio adapter is opt-in and transport-neutral. This change adds the
contract types and fake-transport tests; it does not add an HTTP client, new
Local Studio endpoints, default configuration, Hermes routing, or unattended
automation.

## Adapter boundary

The future transport implements four operations:

```python
submit(spec) -> run_handle
poll(run_handle) -> execution_state
collect(run_handle) -> evidence_bundle
cancel(run_handle) -> execution_state
```

The versioned request shape is represented by
`LocalStudioRunSpec` in `agentic_harness.adapters.local_studio`:

```json
{
  "protocol_version": "local-studio-worker.v1",
  "run_id": "harness-run-id",
  "goal_id": "harness-goal-id",
  "objective": "change one fixture",
  "workspace": "/absolute/workspace",
  "model": "kimi-k3",
  "attempt": 1,
  "acceptance_requirements": ["fixture_changed", "checks_pass"]
}
```

The response is intentionally split into execution state and evidence:

- `LocalStudioRunState` reports queued/running/exited/failed/timed-out/
  cancelled execution state and preserves the worker's claim as untrusted data.
- `LocalStudioEvidenceBundle` carries a redacted transcript, artifact paths,
  and before/after workspace digests.
- `LocalStudioWorker` returns a normal Harness `WorkerResult`, which can move a
  goal to review after a clean process exit. It never creates a `Verified done`
  result.

The adapter must reject mismatched run identities and evidence that has not
been redacted. It must not copy secrets into durable reports, and it must not
accept a worker claim as a review result.

## Model lanes

Local Studio can expose Claude, Kimi K3, and other configured providers as
execution/model lanes. When a task merits comparison, Harness can dispatch the
same immutable specification and review criteria to multiple lanes, then keep
only candidates with fresh independent evidence. Model choice affects the
worker attempt; it does not affect the acceptance contract.

## UI and automation rules

Local Studio may own the human-facing progress view. Its states should be
derived from Harness evidence when a run is Harness-managed:

| Local Studio display | Source of truth |
| --- | --- |
| Running | active adapter/session state |
| Needs evidence | Harness review result |
| Rejected | Harness review result |
| Retrying | new Harness attempt |
| Verified done | matching Harness receipt only |

Local Studio's own scheduler can remain useful for explicitly local work. A
future Harness-managed automation must go through the same adapter and receipt
gate. Hermes remains responsible for cluster routing; there must not be two
independent completion writers for the same run.

## Security and acceptance gates

Local Studio's `read_only` policy is not an operating-system sandbox. The
Harness integration therefore requires isolation and authority outside the
Studio process: an allowlisted workspace, controlled process lifetime, bounded
network/tool access, redacted evidence, and a run identity that cannot be
reused for stale evidence.

Before this becomes a default or production path, the live canary must prove:

1. A real Local Studio task can be submitted, observed, and cancelled.
2. A correct fixture change passes an independent Harness check and produces a
   receipt.
3. A false worker completion claim is rejected.
4. Stale evidence, mismatched run IDs, and unredacted transcripts fail closed.
5. Retry and restart do not overwrite the evidence history of an earlier
   attempt.
6. Local Studio never displays Harness-managed completion without the receipt.

## Deferred work

- HTTP/WebSocket transport selection after the Local Studio API is frozen.
- Local Studio UI receipt/status bridge.
- Best-of-N Claude/Kimi K3 candidate selection.
- Harness-managed subagents and scheduled automations.
- Hermes registry/capability registration.

Those are follow-up changes after this contract is reviewed and a disposable
fake-transport suite passes. None should be enabled by default until the live
dispatch-to-receipt canary supplies direct behavioral evidence.
