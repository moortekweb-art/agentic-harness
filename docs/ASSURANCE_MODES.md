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
first worker cycle. Explicit objective clauses are conservatively derived into
ordered requirement IDs; ambiguous prose remains one full-objective condition.
Every frozen requirement must be reported satisfied and must cite eligible
evidence with predeclared coverage.

Coverage is never inferred from the existence or success of a review command.
If `review_covers` is omitted, the check still runs but has empty coverage and
cannot close a frozen requirement. Use explicit IDs when a check is narrow, or
explicit `review_covers: ["*"]` only when that check verifies every condition.

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

GUI approvals also carry the reviewed goal ID, GoalSpec hash, and revision.
If the current task or specification changes while the dialog is open, the
approval is rejected and the operator must review the current conditions.

If the worker later reports `specification_change_required`, execution pauses
again. The GUI and `approve-spec` command show the proposed conditions and allow
plain-language edits. Approval appends a new immutable specification revision;
rejection or an invalid proposal leaves the current specification unchanged.
All evidence tied to the previous specification hash becomes ineligible and is
recorded as invalidated before execution resumes.
