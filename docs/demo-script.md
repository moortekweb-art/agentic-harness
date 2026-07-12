# Agentic Harness Terminal Demo Script

Target: complete the recording in under two minutes.

Record the actual elapsed time with the review or release evidence. A timing
from one development machine is not a published-release performance guarantee.

This deterministic mechanics demo uses the packaged mock coding-agent worker.
It shows a real failing check, a repair, preserved evidence, and independent
verification without requiring a model account or API key. It is not a
model-quality benchmark.

## Setup

This recording path describes current source behavior. Install it with:

```bash
pipx install --force git+https://github.com/moortekweb-art/agentic-harness.git
```

The same installation provides `agentic-harness` and `agentic-harness-gui`.
The latest PyPI release remains 0.7.2 and can use earlier receipt wording until
a later release includes these changes.

## Canonical recording path

Run one command from a clean terminal:

```bash
agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force
```

The command creates an isolated demo project, installs its test dependency,
shows the deliberate pytest failure, runs the packaged repair worker, and runs
the independent pytest check again. Let the real output remain visible; do not
paste fabricated sample output.

The final trusted receipt should show:

- `Result: Verified done`
- the independent check and its passing result;
- the changed file;
- the number of attempts and retries; and
- the durable report path below `.agentic-harness/runs/<goal-id>/report.md`.

Agentic Harness uses three explicit terminal result categories:

- `Verified done` — independent verification passed.
- `Blocked with reason` — an operator decision, credential, authority, or
  resource is required.
- `Failed with evidence` — execution or independent verification failed and
  the evidence was preserved.

A worker saying “done” is an untrusted claim, not a result category.

## Optional variants

These variants come after the reproducible path above and are not part of its
timing target.

### Inspect the durable receipt

```bash
cd /tmp/agentic-harness-demo
agentic-harness report
```

The report should repeat the trusted result, independent verification, changed
file, attempts, and artifact location.

### Run a real configured coding agent

Use this only in a project where a coding-agent worker has already been
configured. Runtime and outcome depend on that external agent.

```bash
agentic-harness do "fix the failing tests" --check "python -m pytest tests/ -q"
agentic-harness report
```

The explicit `--check` remains the acceptance boundary. The task can end as
`Verified done`, `Blocked with reason`, or `Failed with evidence`; the coding
agent cannot select the trusted category itself.

## Recording tips

- Use `asciinema rec agentic-harness-demo.cast` for a terminal recording.
- Set the terminal font size to 16–18 px and width to roughly 100–120 columns.
- Use a high-contrast theme.
- Pause on the independent check and durable report path.
- Record the actual duration alongside the release evidence.
