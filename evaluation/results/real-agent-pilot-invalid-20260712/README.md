# Invalid real-agent pilot — excluded

This directory preserves the available evidence from the first 20-run pilot at
protocol commit `102787b`. It is published for auditability and must not be
combined with the valid comparison.

The evaluation wrapper emitted only a minimal completion object. It omitted the
plan, current subgoal, checkpoint, requirements, and blockers fields required
by the existing external-worker contract. Consequently:

- direct Codex passed 10/10 verifiers and was accepted 10/10;
- Harness workspaces passed 10/10 verifiers but were accepted 0/10;
- every Harness run exhausted three attempts and ended failed because the
  structured completion claim remained unproven.

This is evidence that Harness rejected an incomplete evidence envelope, not a
valid measurement of Harness versus direct Codex. Both arms were rerun from
scratch after protocol revision 2; no pilot result was reused.

`raw.jsonl` contains all 20 pilot task-arm rows. `transcripts/` contains 20
redacted final-attempt transcript files, and `transcript_manifest.json` binds
their published bytes and transcript-reported token counts. The pilot runner
overwrote the Harness transcript on each retry, so the first two model
transcripts for each Harness task are unavailable. The raw rows retain the
three-attempt count but not per-attempt output, timing, or tokens. Revision 3
fixes future runs by assigning every attempt a distinct transcript file.
