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

Run the representative configuration from the repository root:

```bash
python evaluation/run_gate_benchmark.py \
  --tasks evaluation/tasks.json \
  --output-dir evaluation/results/representative \
  --seed 20260711 \
  --repetitions 1
```

The output directory contains raw JSONL, environment and source checksums, aggregate
JSON, and a Markdown summary. Token metrics are omitted unless the scripted process or
adapter reports them.
