# Evidence Contract

Agentic Harness does not treat worker-authored prose alone as completion
evidence. Strict completion converges at one engine boundary for embedded,
coding-agent, shell, and custom workers: a structured completion claim must
reference recognized current-run records, and configured independent review
must pass.

## Legacy v1 assurance level

This section documents the v0.11 compatibility contract. New v0.12 embedded
runs use the frozen GoalSpec and immutable evidence v2 contracts documented in
`GOAL_SPEC.md` and `EVIDENCE_V2.md`.

The v1 contract is **check-gated**, not a complete semantic proof of an
unrestricted natural-language objective. The worker derives the requirement
list that the completion audit inspects. The harness preserves the original
objective and rejects malformed, stale, prose-only, and check-failing claims,
but it does not independently establish that the worker-derived list captured
every clause of that objective.

Likewise, v1 requirement coverage is normalized by the completion audit after
the worker cites a recognized record. It is not immutable issuer-declared
coverage. The record shape below is the durable normalized audit record, not a
claim that the issuer independently determined the semantic mapping.

## Record

Accepted records use `agentic_harness.evidence.v1`:

```json
{
  "schema": "agentic_harness.evidence.v1",
  "id": "review:1:command_passes",
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
- is linked to the cited worker-derived requirement through `requirement_ids`.

Missing IDs, failed events, duplicate references, previous-run events, and
free-form descriptions fail the audit.

## Issuers

The embedded model agent receives durable `event:<sequence>` IDs after bounded
tools finish. The common audit re-reads those records and requires a passed
current-run tool event; the model cannot mint an accepted ID itself. A v1 tool
event proves that the recorded activity completed. It does not independently
prove that the activity semantically satisfies an objective clause.

Coding-agent and custom workers receive the stable IDs of configured
independent criteria in their completion instruction. Those IDs resolve only
after the harness actually runs the criterion and it passes. A structured
`HARNESS_RESULT_JSON` line may claim completion, but its prose cannot create a
trusted record.

Shell workers still need a structured strict-completion claim or a registered
adapter that supplies one. Merely exiting zero is not requirement evidence.

The optional `local-goal` GUI backend is an explicitly external, operator-
managed compatibility boundary. An external `accepted` or `done` state is
displayed as `Needs review`, not trusted completion. `Done` requires a matching
`agentic_harness.acceptance_receipt.v1` issued by a Harness-owned acceptance
controller for the same run and reviewed candidate digest, with at least one
passed deterministic command. The embedded engine does not mint that external
receipt or silently inherit the external executor's authority.

## Trust Limit

This is a local integrity contract, not a cryptographic attestation system. A
user with permission to rewrite the workspace's `.agentic-harness/` state can
also rewrite its evidence. The contract prevents a worker result from being
accepted merely because it supplied convincing text, and prevents evidence
from another goal cycle from satisfying the current one. It does not yet freeze
acceptance requirements before execution or give evidence immutable,
issuer-defined semantic coverage; those are the target of the next evidence
contract.
