# Evidence Contract

Agentic Harness does not treat worker-authored prose as completion evidence.
Strict completion converges at one engine boundary for embedded, coding-agent,
shell, and custom workers: every satisfied requirement must reference a current-
run record issued or verified by the harness.

## Record

Accepted records use `agentic_harness.evidence.v1`:

```json
{
  "schema": "agentic_harness.evidence.v1",
  "id": "review:1",
  "goal_id": "0123456789abcdef0123456789abcdef",
  "run_id": "fedcba9876543210fedcba9876543210",
  "requirement_ids": ["R1"],
  "kind": "independent_review",
  "result": "passed",
  "issuer": "harness.review",
  "validation": {"level": "harness_verified"}
}
```

The completion audit persists its evidence registry in durable goal state. A
reference is accepted only when its record:

- belongs to the same `goal_id` and current `run_id`;
- is unique within the requirement claim;
- has `result: passed` and `validation.level: harness_verified`; and
- covers the requirement through `requirement_ids`.

Missing IDs, failed events, duplicate references, previous-run events, and
free-form descriptions fail the audit.

## Issuers

The embedded model agent receives durable `event:<sequence>` IDs after bounded
tools finish. The common audit re-reads those records and requires a passed
current-run tool event; the model cannot mint an accepted ID itself.

Coding-agent and custom workers receive the stable IDs of configured
independent criteria in their completion instruction. Those IDs resolve only
after the harness actually runs the criterion and it passes. A structured
`HARNESS_RESULT_JSON` line may claim completion, but its prose cannot create a
trusted record.

Shell workers still need a structured strict-completion claim or a registered
adapter that supplies one. Merely exiting zero is not requirement evidence.

The optional `local-goal` GUI backend is an explicitly external, operator-
managed compatibility boundary. It displays the external runtime's accepted
state; it does not relabel that state as evidence verified by the embedded
engine.

## Trust Limit

This is a local integrity contract, not a cryptographic attestation system. A
user with permission to rewrite the workspace's `.agentic-harness/` state can
also rewrite its evidence. The contract prevents a worker result from being
accepted merely because it supplied convincing text, and prevents evidence
from another goal cycle from satisfying the current one.
