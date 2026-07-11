# Agentic Harness v0.7.1

v0.7.1 tightens completion integrity, fixes the optional legacy GUI bootstrap,
and makes the public repository easier to evaluate without adding another
execution backend or product mode.

## Completion integrity

- Persisted goal IDs now use one portable safe-identifier contract. Goal run
  directories are resolved and proved to remain below `runs/` before any write;
  poisoned markers, mismatched state IDs, traversal strings, Windows separators,
  overlong IDs, and symlink escapes are rejected.
- Failed independent commands no longer copy stdout, stderr, command arguments,
  or opaque output into terminal-facing review results. Review fields are also
  redacted before they enter durable goal state. The explicit `review` command
  returns only the goal ID, status, and review result, omitting stored check
  arguments and derived instructions.
- Strict completion accepts only typed `agentic_harness.evidence.v1` records.
  Records are bound to the current goal and worker run, must be passed and
  harness-verified, and carry explicit requirement coverage. Prose, duplicates,
  failed events, missing IDs, and evidence from an earlier run fail the audit.
- Mutable `Goal` objects are explicitly unhashable. Callers that need a stable
  key should use `goal.id`.

## Browser compatibility

The optional `local-goal` GUI backend now serves the shared read-only setup
contract. The browser renders the external workspace and runtime label, hides
the embedded-only Setup control, and attaches its status stream instead of
aborting startup on `/api/setup` with a JSON 404.

## Focus and public proof

- The README now opens with one problem, one install command, one controlled
  failure-to-verification demo, and one recommended real-project GUI path.
- Stale marketing and private-infrastructure idea files were removed. A full
  contributor guide, private-reporting security policy, common evidence
  contract, version-generic release checklist, and dated publishing receipts
  replace them.
- CI retains the full Linux, Windows, and macOS Python 3.11–3.14 test matrix but
  runs lint, typing, builds, wheel demos, and CLI smoke checks only on the cells
  that add distinct signal.
- `evaluation/` adds a reproducible, two-arm comparison covering 24
  task-behavior cases across six maintenance payloads, with pristine workspaces,
  seeded arm order, raw JSONL, checksums, environment metadata, and aggregate
  reports. It is deliberately labeled as a controlled gate-efficacy evaluation,
  not real-model performance. In the representative run, the direct baseline
  produced 12 false accepts and 6 verified accepts; the harness produced no
  false accepts, 18 verified accepts, caught all 12 premature claims, and
  recovered all 12 repairable tasks. Mean attempts rose from 1.0 to 2.0, making
  the added work explicit.
- The declared Setuptools floor now supports the PEP 639 license expression,
  and the source distribution includes the documentation, evaluation, examples,
  workflows, and tests needed for its shipped test suite to remain coherent.

## Compatibility notes

Safe legacy IDs containing letters, numbers, dots, underscores, and hyphens
remain readable. State with path separators or a run-directory/ID mismatch is
treated as corrupt. Review failures now report the exit code without echoing
the failing command's output; inspect the command directly in the workspace
when additional diagnostics are needed.
