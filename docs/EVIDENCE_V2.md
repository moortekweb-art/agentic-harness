# Evidence v2

`agentic_harness.evidence.v2` is an immutable statement issued for one worker
run against one frozen GoalSpec. It separates activity from independent proof:

```json
{
  "schema": "agentic_harness.evidence.v2",
  "id": "review:1",
  "goal_id": "goal-123",
  "run_id": "run-456",
  "goal_spec_sha256": "...",
  "issuer": "harness.review",
  "kind": "deterministic_check",
  "result": "verified",
  "covers": ["R1", "R3"]
}
```

The result vocabulary is `observed`, `produced`, `verified`, `failed`, and
`invalidated`. Only `verified` evidence with matching goal, run, GoalSpec hash,
and predeclared requirement coverage can close a frozen requirement.

Review criteria declare `covers` before their check executes. The completion
audit reads that coverage but never adds to or rewrites it. A passing criterion
with `covers: []` is still a passing check, but it proves no frozen requirement.

Project configuration can declare coverage for its deterministic review gate:

```yaml
review:
  command: [python, -m, pytest, -q]
  covers: [R1, R3]
```

`covers: ["*"]` declares that the criterion covers every requirement in the
already-frozen GoalSpec. The wildcard is resolved to concrete IDs before the
check result is issued, so evidence records never contain a mutable wildcard.
Explicit IDs remain preferable when checks cover only part of a specification.
Setting `covers: []` keeps a general command check-gated without claiming
requirement coverage.

Tool events are issued as `observed` with `covers: []`. Reading a file, editing
text, or invoking a worker-side check records useful activity without claiming
that the objective is correct. Evidence from another run or specification hash,
failed evidence, and invalidated evidence are ineligible for completion.
An approved specification revision changes the GoalSpec hash and persists
invalidated copies of prior evidence for audit history. New checks issue new
records against the revised hash.
