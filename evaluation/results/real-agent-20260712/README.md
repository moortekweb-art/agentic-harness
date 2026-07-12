# Codex real-agent comparison — 2026-07-12

This is a preregistered end-to-end comparison of Codex CLI 0.144.1 using
`gpt-5.6-sol` on ten
small synthetic maintenance tasks. It is not broad model-quality, adoption, or
statistical evidence.

| Arm | Hidden verifier pass | False accepts | Mean time | Mean tokens | Unintended paths |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 10/10 | 0 | 15.15s | 14,990.7 | 0 |
| Harness | 10/10 | 0 | 26.94s | 16,711.1 | 0 |

No correctness advantage was observed because Codex completed every task
correctly in both arms. Harness added 11.79 seconds mean latency and 11.5% mean
token use. A harder, separately preregistered task set is needed to measure
false-claim prevention or recovery with a real agent.

The user objective was identical across arms. Harness necessarily supplied
additional lifecycle and structured-evidence instructions, so this compares
the complete systems rather than a byte-identical prompt with only a gate
toggled.

`raw.jsonl` contains all 20 task-arm records. `transcripts/` contains all 20
redacted terminal transcripts, and `transcript_manifest.json` binds each to a
SHA-256 digest and exact transcript-reported token count. The packaging check
found 20 unique task-arm pairs, no missing transcript, and no recognized secret
pattern.

An earlier local pilot is excluded because its evaluation wrapper omitted mandatory
fields from the existing external-worker completion contract. All ten pilot
Harness workspaces passed their verifier but were correctly rejected as
unproven. No pilot result was reused. Detailed pilot artifacts are not included
in this public package, so that pilot disposition is not independently auditable
from the repository.
