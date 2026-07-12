# Harder Codex real-agent comparison — 2026-07-12

This separately preregistered follow-up compared Codex CLI 0.144.1 with
`gpt-5.6-sol` on ten synthetic multi-file and edge-preservation tasks. It is a
small end-to-end systems comparison, not statistical evidence, broad model
evaluation, or an adoption claim.

| Arm | Verifier pass | Accepted | False accepts | Mean time | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 9/10 | 10/10 | 1 | 28.54s | 17,411.7 |
| Harness | 9/10 | 9/10 | 0 | 48.62s | 20,726.5 |

Both arms made the same incorrect compatibility-API change. Direct execution
returned success and therefore falsely accepted it. Harness ran three attempts,
never obtained a passing independent check, and refused acceptance. Harness did
not repair the defect within its attempt budget. The other nine tasks passed in
both arms. The narrow supported conclusion is that Harness prevented one false
accept in this task set; it did not improve final verifier-pass rate and added
latency and token use.

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
