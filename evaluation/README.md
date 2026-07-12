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

## First Real-Agent Study

The [2026-07-12 Codex comparison](results/real-agent-20260712/README.md)
preregistered ten small tasks and reran both direct and Harness arms in full.
Both passed 10/10 hidden verifiers with zero false accepts. Harness added
latency and token use, so this easy task set did not demonstrate a correctness
benefit. Raw records, redacted transcripts, hashes, and the complete excluded
[invalid pilot](results/real-agent-pilot-invalid-20260712/README.md) are
published with the result.

Future reruns must pin the evaluated model explicitly:

```bash
python evaluation/run_real_agent_comparison.py \
  --tasks evaluation/real_agent_tasks.json \
  --output-dir /tmp/agentic-harness-real-agent \
  --seed 20260712 \
  --model gpt-5.6-sol
```

## Harder Real-Agent Follow-up

The first [harder pilot](results/hard-real-agent-20260712/README.md) was
invalidated after adversarial review showed that three verifiers missed stated
invariants. It is retained but must not be used for primary claims. A separately
frozen [revision-2 pilot](results/hard-real-agent-v2-20260712/README.md) was also
invalidated when a prohibited literal special-case passed its boundary verifier.
Both invalid runs remain published for audit and are excluded from primary
claims. Revision 3 uses exact source-state verification for that exact-source
requirement, safe unique task IDs, and a bounded scoring verifier.

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
