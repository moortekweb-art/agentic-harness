# Controlled completion-gate efficacy evaluation

This is a deterministic scripted gate evaluation, not real-model performance.

- Tasks: 24
- Repetitions: 1
- Seed: 20260711
- Token metrics available: false

| Arm | Verified accepts | Verifier pass | False-success rate | Caught false claims | Recovered | Mean attempts | Mean seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 6 | 25.0% | 100.0% | 0 | 0 | 1.00 | 0.0571 |
| harness | 18 | 75.0% | 0.0% | 12 | 12 | 2.00 | 0.1758 |

The baseline trusts an exit-zero structured completion claim. The harness arm uses 
`CodingAgentWorker`, `AutonomousRunner`, and an independent verifier process.
