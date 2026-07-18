# Agentic Harness v0.13.1

Version 0.13.1 is a security and correctness update for Verified Best-of-N.
Version 0.13.0 should not be used for unattended tournament acceptance because
repository-controlled verifier inputs could be omitted from its frozen asset
boundary.

## Acceptance-integrity fixes

The enforced invariants and non-goals are recorded in the
[verified tournament threat model](VERIFIED_TOURNAMENT_THREAT_MODEL.md).

- Freezes repository-local executables in command argument zero, including
  direct checks such as `./verify.sh`.
- Freezes verifier definitions for Go, Maven, Gradle, .NET, and RSpec in
  addition to the existing Python, JavaScript, and Rust definitions.
- Adds `review.assets` / `review_assets` for custom verifier dependency
  boundaries and refuses a tournament when the boundary cannot be inferred.
- Rejects lexical verifier symlinks, symlinked parents, and Windows reparse
  points before resolution and repeats those checks in every candidate.
- Runs final verification in a fresh worktree, fingerprints the full tracked
  and non-ignored project state, and blocks if verification changes that state.
- Records a verified-staged and applying-verified transaction phase before
  touching the original workspace. GUI recovery restores the clean preimage
  after an interrupted application when the recorded state still matches.
- Publishes deep-copied progress snapshots so presentation callbacks cannot
  mutate tournament decisions.

## Configuration safety

- CLI `init`, `quickstart`, and `init-agent` writes reject symlinked or
  reparse-point project configuration paths, including dangling links and
  `--force` replacement.
- Configuration files are written through an owner-only temporary file and
  atomically replaced only after the destination is confirmed as a regular
  file.

## Compatibility

- GoalSpec, evidence-v2, assurance-mode, and amendment schemas are unchanged.
- Known test runners continue to work without additional configuration.
- Custom or opaque verifier runners must declare `review.assets`; this is an
  intentional fail-closed requirement for Verified Best-of-N only.
