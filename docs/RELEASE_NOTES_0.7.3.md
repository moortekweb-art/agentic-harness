# Agentic Harness v0.7.3

This release makes the first verified task easier to understand and hardens
the optional external-orchestrator boundary. Executors may propose candidate
work; only current-run Harness evidence can produce `Verified done`.

## First-run experience

- The README, GUI, CLI receipts, and packaged demo now lead with one workflow:
  choose a project, state one objective, configure one independent command,
  and inspect the resulting evidence.
- Worker completion prose is labeled untrusted. Receipts name changed files,
  exact checks, attempts, retries, and the durable report path.
- Desktop and mobile result captures reflect the same packaged GUI.

## Acceptance integrity

- Interrupted completion audits always rerun deterministic review before
  acceptance; stale review state cannot be reused after resume.
- The optional long-running external route negotiates the versioned
  `agentic_harness.external_candidate.v1` contract and fails closed when the
  wrapper does not advertise it.
- Legacy monitoring no longer requests automatic acceptance.
- External `accepted` or `done` text is shown as `Needs review` unless the same
  run includes a valid `agentic_harness.acceptance_receipt.v1` with a reviewed
  candidate digest and passed deterministic command.

## Evaluation scope

The checked-in 24-task/48-record v0.7.2 snapshot remains an immutable
controlled gate evaluation. It is not relabeled as a real-model benchmark or
an adoption claim.
