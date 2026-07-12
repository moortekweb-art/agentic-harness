# Invalid harder Codex pilot — 2026-07-12

**Invalid for primary outcome claims.** Post-run adversarial review found that
three behavioral verifiers did not enforce every stated task invariant. These
artifacts are retained for auditability, but the 9/10 figures below must not be
used as evidence. A separately frozen revision-2 protocol replaces this pilot.

This separately preregistered follow-up compared Codex CLI 0.144.1 with
`gpt-5.6-sol` on ten synthetic multi-file and edge-preservation tasks. It is a
small end-to-end systems comparison, not statistical evidence, broad model
evaluation, or an adoption claim.

| Arm | Verifier pass | Accepted | False accepts | Mean time | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 9/10 | 10/10 | 1 | 28.54s | 17,411.7 |
| Harness | 9/10 | 9/10 | 0 | 48.62s | 20,726.5 |

Both arms made the same compatibility-API change, but the under-specified
verifiers mean this run supports no comparative correctness conclusion. The
acceptance difference is also mechanically induced by the treatment: Harness
requires this verifier, while direct execution trusts exit-zero. It is a
policy-integrity check, not evidence of better agent judgment.

The user objective and starting workspace were identical across arms. Harness
necessarily added lifecycle and structured-evidence instructions, so this tests
the complete systems rather than byte-identical prompts. The verifier was hidden
from the task prompt, not protected by operating-system isolation.

The raw `unintended_paths` diagnostic is not an agent-change metric in this run:
behavioral verification imported candidate modules and generated `__pycache__`,
and one agent ran pytest, which generated `.pytest_cache`. These artifacts were
recorded honestly but must not be interpreted as eight incorrect-edit events.

`raw.jsonl` contains all 20 task-arm records. `transcripts/` contains 22
redacted attempt transcripts; `transcript_manifest.json` binds each to a SHA-256
digest and transcript-reported token count. Packaging verified 20 unique
task-arm pairs, the exact attempt sequence, complete token observations, and no
recognized sensitive pattern. That automated pattern scan is not a general
privacy certification.
