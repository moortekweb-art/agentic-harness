# Assurance modes

Assurance describes the integrity guarantee. It is separate from Quick,
Standard, or Thorough execution effort.

```yaml
assurance_mode: specification_frozen
```

## `check_gated`

The worker must return a structured completion claim and the configured
independent checks must pass. Check evidence does not need declared
requirement coverage. The result means the worker reported completion and the
checks passed; it does not claim every natural-language clause was proven.

## `specification_frozen`

This is the default. The harness freezes the completion conditions before the
first worker cycle. Every frozen requirement must be reported satisfied and
must cite eligible evidence with predeclared coverage.

## `high_assurance`

The harness writes a specification proposal and pauses before the first worker
action. The operator must approve it explicitly:

```bash
agentic-harness approve-spec
```

The operator may replace the proposal with plain-language conditions:

```bash
agentic-harness approve-spec \
  --requirement "Invalid input returns a useful error." \
  --requirement "Regression tests cover valid and invalid input."
```

Approval creates a separate immutable `goal-spec-approved.json` with a new
hash. The original proposal remains intact for audit history. Execution uses
only the approved specification, and no worker runs before approval.
