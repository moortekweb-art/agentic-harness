# Frozen Goal Specification

`agentic_harness.goal_spec.v1` is the immutable acceptance identity for one
autonomous goal. It is created before the first worker cycle and stored at:

```text
.agentic-harness/runs/<goal-id>/goal-spec.json
```

The canonical specification is deliberately separate from mutable
`state.json` autonomy progress. Mutable state stores only the specification
hash needed to detect identity drift.

## Contract

```json
{
  "contract": "agentic_harness.goal_spec.v1",
  "objective": "Add input validation.",
  "requirements": [
    {"id": "R1", "text": "Add input validation."}
  ],
  "derivation": "harness_preserved_objective",
  "approval": "automatic",
  "created_at": "2026-07-17T00:00:00Z",
  "sha256": "..."
}
```

The SHA-256 digest covers every field except the digest itself using canonical
UTF-8 JSON. Requirement IDs are unique safe identifiers. Objective and
requirement text are non-empty and cannot contain surrounding whitespace.

Once written, the store accepts an identical idempotent write but rejects a
different specification at the same path. A changed objective, requirement,
derivation, approval state, timestamp, or hash is therefore observable instead
of silently replacing the acceptance contract.

## Foundation boundary

The first implementation uses one requirement containing the complete original
objective. This is a conservative no-shrink baseline named
`harness_preserved_objective`; it is not presented as semantic clause
decomposition. Later assurance work may derive multiple plain-language
requirements before freezing them.

## Worker status contract

The frozen requirements are authoritative in strict completion. Workers report
mutable progress only through `requirement_status`:

```json
{
  "requirement_status": [
    {"id": "R1", "status": "satisfied", "evidence": ["review:1"]}
  ]
}
```

The completion audit rejects replacement `requirements` lists, unknown IDs,
missing frozen IDs, duplicates, and any status row containing replacement
requirement text. The GUI hydrates the immutable text from `goal-spec.json` and
combines it with the separate mutable status projection.

Evidence coverage follows the immutable `agentic_harness.evidence.v2` contract
documented in `EVIDENCE_V2.md`. Assurance modes and specification amendments
remain separate behavior changes built on this storage identity.
