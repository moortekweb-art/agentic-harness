# Corrected harder Codex comparison — 2026-07-12

This revision-2 study was frozen after adversarial review invalidated the first
harder pilot and before any revision-2 result was observed. It compares Codex
CLI 0.144.1 with `gpt-5.6-sol` on ten synthetic multi-file and
edge-preservation tasks. It is an auditable remote-model artifact, not exactly
reproducible statistical evidence, broad model evaluation, or an adoption claim.

| Arm | Verifier pass | Accepted | False accepts | Mean time | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 10/10 | 10/10 | 0 | 28.03s | 20,517.5 |
| Harness | 9/10 | 9/10 | 0 | 48.43s | 25,839.7 |

Direct execution passed every outcome verifier. The independently sampled
Harness arm failed the compatibility-alias task on all three attempts and
correctly refused to accept it. Thus this run shows no correctness advantage
for Harness; it shows higher latency, higher token use, and one fewer completed
task. With no failed direct result, it does not exercise the fail-closed policy
against a direct false claim.

False-accept count is a treatment-integrity sanity check, not an agent-quality
metric: Harness requires the same outcome verifier used for scoring, while the
direct arm trusts the coding-agent process's exit-zero result. Zero Harness
false accepts is therefore expected unless the acceptance implementation is
defective.

The user objective and starting workspace were identical across arms, but the
complete prompts and budgets were not. Harness supplied lifecycle and evidence
instructions and allowed up to three attempts, so this is an end-to-end system
comparison. The remote model/provider is mutable and the verifier is omitted
from the task prompt but not protected by OS-level secrecy or isolation.

`raw.jsonl` contains all 20 task-arm records. `transcripts/` contains all 22
redacted attempt transcripts; `transcript_manifest.json` binds them to SHA-256
digests and transcript-reported token counts. Packaging verified the complete
attempt sequence, raw-derived aggregates, and no recognized sensitive pattern.
The latter is a narrow automated check, not privacy certification.

The `unintended_paths` diagnostic includes verifier-generated `__pycache__`
files and is not an agent-change-quality measure. It is retained in raw data but
should not be interpreted as incorrect-edit incidence.
