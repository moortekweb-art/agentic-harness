# Controlled completion-gate efficacy evaluation

This is a deterministic scripted gate evaluation, not real-model performance.

- Task-behavior cases: 24
- Repetitions: 1
- Seed: 20260711
- Token metrics available: false

| Arm | Verified accepts | Verifier pass | False-success rate (false-claim cases) | Caught false claims | Recovered | Mean attempts | Mean seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 6 | 25.0% | 100.0% | 0 | 0 | 1.00 | 0.0589 |
| harness | 18 | 75.0% | 0.0% | 12 | 12 | 2.00 | 0.1426 |

The baseline trusts an exit-zero structured completion claim. The harness arm uses `CodingAgentWorker`, `AutonomousRunner`, and an independent verifier process.
The false-success rate denominator is the intentionally false-claim cases, not all runs.
