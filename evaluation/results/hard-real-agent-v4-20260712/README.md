# Invalid revision-4 harder Codex pilot — 2026-07-12

**Invalid for primary outcome claims.** Candidate import-time code could replace
the process-global random functions before challenges were drawn, and behavioral
module paths bypassed the symlink guard. These artifacts remain for audit only;
the 10/10 and 9/10 figures below must not be used as outcome evidence.

Revision 4 was frozen after adversarial review invalidated three earlier pilots
and before any revision-4 result was observed. General behavioral checks use
runtime-generated values; exact-state tasks use symlink-aware workspace-confined
paths. It compares Codex CLI 0.144.1 with `gpt-5.6-sol` on ten synthetic tasks.
This is an auditable remote-model artifact, not exactly reproducible statistical
evidence, broad model evaluation, or an adoption claim.

| Arm | Verifier pass | Accepted | False accepts | Mean time | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 10/10 | 10/10 | 0 | 33.68s | 23,452.3 |
| Harness | 9/10 | 9/10 | 0 | 44.56s | 24,674.1 |

Direct passed every outcome check. The independently sampled Harness arm failed
the compatibility-alias task across four attempts and refused acceptance. In
this run Harness completed one fewer task, took longer, and used more tokens.
There was no direct false claim, so the run did not exercise a fail-closed
advantage against direct execution.

False-accept count is a treatment-integrity sanity check, not an agent-capability
metric. Harness requires the scoring outcome verifier while direct execution
trusts process exit-zero. Zero Harness false accepts is expected unless that
acceptance implementation is defective.

Objectives and starting workspaces were identical, but full prompts and budgets
were not. Harness added lifecycle/evidence instructions and allowed an initial
attempt plus three repair cycles. The provider/model is mutable. Verifiers were
omitted from prompts but not protected by OS-level workload isolation.

`raw.jsonl` contains all 20 rows and scoring-timeout fields. `transcripts/`
contains all 23 redacted attempt transcripts; `transcript_manifest.json` binds
each to a SHA-256 digest and reported token count. Packaging verified complete
attempt sequences, raw-derived aggregates, and no recognized sensitive pattern.
That narrow scan is not privacy certification.

The `unintended_paths` diagnostic includes verifier-created caches and is not an
incorrect-edit rate. One Harness run generated pytest cache files; raw evidence
retains them.
