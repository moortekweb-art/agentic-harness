# Import-safe property comparison revision 5 — 2026-07-12

Revision 5 was frozen after four earlier pilots failed adversarial review and
before any revision-5 result was observed. Runtime challenges are drawn before
candidate import; all scored paths use one symlink-aware workspace-containment
helper. It compares Codex CLI 0.144.1 with `gpt-5.6-sol` on ten synthetic tasks.
This is an auditable remote-model artifact, not exactly reproducible statistical
evidence, broad model evaluation, or an adoption claim.

| Arm | Verifier pass | Accepted | False accepts | Mean time | Mean tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 9/10 | 10/10 | 1 | 31.52s | 19,430.3 |
| Harness | 9/10 | 9/10 | 0 | 50.80s | 27,283.9 |

Both arms failed the compatibility-alias outcome check. Direct execution
returned success and was counted as one false accept. Harness failed across four
attempts and refused acceptance. Harness did not repair the task or improve
final verifier-pass rate and used materially more time and tokens.

False-accept count is a treatment-integrity sanity check, not evidence that
Harness makes the model more capable. Harness requires the scoring verifier;
direct execution trusts process exit-zero. The difference confirms that this
acceptance policy operated on the observed miss.

Objectives and starting workspaces were identical; full prompts and budgets
were not. Harness added lifecycle/evidence instructions and allowed an initial
attempt plus three repair cycles. The remote provider/model is mutable.
Verifiers were omitted from task prompts but were not protected by OS-level
workload isolation. Runtime-random checks reduce fixed-vector gaming but are not
a security boundary against arbitrary hostile code in the same user account.

`raw.jsonl` contains all 20 rows and scoring-timeout fields. `transcripts/`
contains all 23 redacted attempt transcripts; `transcript_manifest.json` binds
each to a SHA-256 digest and reported token count. Packaging verified complete
attempt sequences, raw-derived aggregates, and no recognized sensitive pattern.
That narrow scan is not privacy certification.

The `unintended_paths` diagnostic includes verifier-created caches and is not an
incorrect-edit rate. One Harness run also created `test_settings.py`; raw
evidence retains it.
