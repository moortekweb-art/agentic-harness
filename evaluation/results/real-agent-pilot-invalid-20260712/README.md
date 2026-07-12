# Invalid real-agent pilot — excluded

This directory preserves the complete first 20-run pilot from protocol commit
`102787b`. It is published for auditability and must not be combined with the
valid comparison.

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

`raw.jsonl` contains all 20 pilot task-arm rows. `transcripts/` contains all 20
redacted final-attempt transcript files, and `transcript_manifest.json` binds
their published bytes and transcript-reported token counts. Harness transcript
files are overwritten per attempt by the adapter, so they preserve the final
attempt rather than three separate per-attempt model transcripts; the durable
attempt count remains in each raw row.
