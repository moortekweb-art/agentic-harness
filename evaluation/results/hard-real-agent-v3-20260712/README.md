# Hardened Codex comparison revision 3 — 2026-07-12

Revision 3 was frozen after two earlier pilots failed adversarial verifier review
and before any revision-3 result was observed. It compares Codex CLI 0.144.1
with `gpt-5.6-sol` on ten synthetic multi-file and edge-preservation tasks.
This is an auditable remote-model artifact, not exactly reproducible statistical
evidence, broad model evaluation, or an adoption claim.

| Arm | Verifier pass | Accepted | False accepts | Mean time | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 9/10 | 10/10 | 1 | 26.70s | 16,560.2 |
| Harness | 9/10 | 9/10 | 0 | 54.51s | 24,501.4 |

Both arms failed the compatibility-alias outcome check. Direct execution still
returned success and was therefore counted as one false accept. Harness failed
the same check across four attempts and refused acceptance. Harness did not
repair the task, did not improve final verifier-pass rate, and used materially
more time and transcript-reported tokens.

False-accept count is a treatment-integrity sanity check, not evidence that the
Harness makes the model more capable: the Harness arm requires the same outcome
verifier used for scoring, while direct execution trusts the coding-agent
process's exit-zero result. The observed difference confirms that the acceptance
policy operated on this miss.

The user objective and starting workspace were identical across arms, but the
full prompts and budgets were not. Harness supplied lifecycle/evidence
instructions and actually allowed up to four attempts, so this is an end-to-end system
comparison. The remote model/provider is mutable. Verifiers were omitted from
task prompts but were not protected by OS-level secrecy or workload isolation.

The preregistration called `AutonomyPolicy(max_cycles=3)` a three-attempt limit;
the implementation permits an initial attempt plus three cycles. This budget
description was wrong. The raw four-attempt sequence is retained, and no result
was recomputed to conceal the discrepancy.

`raw.jsonl` contains 20 task-arm records and an explicit scoring-verifier timeout
field. `transcripts/` contains all 23 redacted attempt transcripts;
`transcript_manifest.json` binds each to a SHA-256 digest and reported token
count. Packaging verified the complete attempt sequence, raw-derived aggregates,
and no recognized sensitive pattern. That scan is not privacy certification.

The `unintended_paths` diagnostic includes verifier-created `__pycache__` files.
One Harness run also created `test_routes.py`; the raw row preserves that fact.
The aggregate count therefore must not be interpreted as eight incorrect-edit
events.
