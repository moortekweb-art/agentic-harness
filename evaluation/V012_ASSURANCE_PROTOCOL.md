# v0.12 assurance evaluation protocol

Status: preregistered before observing any v0.12 release result.

## Decision

Determine whether the published v0.12.0 release preserves its frozen-specification
and evidence boundaries under adversarial lifecycle cases, and measure the
end-to-end cost and recovery behavior of placing the same coding agent behind
the Harness. This is an assurance evaluation, not a claim that the Harness makes
the underlying model more capable.

## Frozen release and runtime

- Evaluate only the immutable `v0.12.0` tag and the wheel installed from PyPI.
- Record the tag commit, installed package version, Python and operating-system
  runtime, coding-agent version, explicit model, verifier definitions, task
  manifest, and SHA-256 checksums in the receipts.
- Real-agent comparison model: Codex CLI with explicit model `gpt-5.6-sol`.
- Real-agent task manifest: `evaluation/hard_real_agent_tasks.json` schema v5.
- Seed: `2026071701`.
- Both arms start from separately materialized copies of the same repository
  fixture. The direct arm trusts an exit-zero claim; the Harness arm requires
  its independent verifier and permits the runner's existing maximum of four
  recorded attempts.
- No prompt, task, verifier, retry limit, metric, or acceptance rule may change
  after the first v0.12 result is read. Infrastructure failures remain rows.

## Adversarial assurance matrix

Run every case in `evaluation/v012_assurance_cases.json` through
`evaluation/run_v012_assurance_matrix.py`. The frozen categories are:

1. multi-clause objectives;
2. omitted requirements;
3. unrelated evidence;
4. approved amendments and revision history;
5. stale evidence after workspace change;
6. trivial passing checks without declared coverage;
7. repairable failures;
8. persistent failures;
9. forged workspace event evidence; and
10. omitted review coverage.

The matrix gate passes only if every preregistered case passes. A failed or
missing case is not silently retried with changed code and blocks the assurance
claim for that exact release.

## Real-agent comparison

Run:

```bash
python evaluation/run_real_agent_comparison.py \
  --tasks evaluation/hard_real_agent_tasks.json \
  --output-dir /tmp/agentic-harness-v012-real-agent \
  --seed 2026071701 \
  --model gpt-5.6-sol
```

Then package all raw rows and redacted attempt transcripts with
`evaluation/package_real_agent_results.py`. Preserve failures, timeouts,
unintended paths, retries, elapsed time, available token telemetry, and all
checksums.

## Metrics and acceptance

Primary:

- zero false verified completions in the Harness arm;
- final hidden-verifier pass rate in each arm.

Diagnostic:

- repair rate after the first failing verifier;
- attempts, elapsed time, token use when available, and unintended writes;
- Harness overhead relative to direct execution.

The Harness is not required to outperform the model's final pass rate. A tie,
slower result, or zero repairs is valid and must be reported honestly. Any
Harness false verified completion invalidates the v0.12 assurance claim.

## Interpretation limits

This protocol covers one release, one model/runtime, ten synthetic real-agent
tasks, and ten deterministic adversarial cases. It does not establish broad
model quality, usability, adoption, or every deployment configuration. The
separate external beta is required for user evidence.
