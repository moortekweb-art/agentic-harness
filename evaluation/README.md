# Completion-gate efficacy evaluation

This directory contains a controlled, deterministic evaluation of completion-gate
behavior. It is not a benchmark of real models, coding quality, or agent intelligence.

The same scripted coding-agent process runs in two pristine copies of each fixture:

- `baseline` trusts an exit-zero structured completion claim.
- `harness` runs the process through `CodingAgentWorker`, `AutonomousRunner`, and a
  separate deterministic verifier.

The 24 maintenance-style fixtures cross six payloads with four scripted behaviors:

- correct completion on the first attempt;
- an exit-zero false claim followed by a correct repair;
- a persistent exit-zero false claim; and
- an exit failure followed by a correct repair.

Arm order is randomized from the recorded seed. The matrix measures gate rejection and
recovery behavior without making statistical or model-quality claims.

## Checked-in Release Snapshot

[`results/representative/`](results/representative/README.md) is an immutable
v0.7.2 release snapshot copied from the digest-verified public PyPI sdist. It is
bound to tag commit `751aead465edbdd09c2a93cc2162164c70a998ce`, package
version 0.7.2, and a clean Git baseline.

Validate its source checksums against that tag commit, not current main. The
default branch can advance after a release, so comparing release-bound checksums
to a newer checkout would be misleading.

## Run a New Comparison

Run the representative configuration from the repository root, but write new
results outside the checked-in release snapshot:

```bash
python evaluation/run_gate_benchmark.py \
  --tasks evaluation/tasks.json \
  --output-dir /tmp/agentic-harness-gate-evaluation \
  --seed 20260711 \
  --repetitions 1
```

The output directory contains raw JSONL, environment and source checksums, aggregate
JSON, and a Markdown summary. Token metrics are omitted unless the scripted process or
adapter reports them. A new run describes the checkout that produced it; it does
not replace the immutable v0.7.2 release snapshot.
