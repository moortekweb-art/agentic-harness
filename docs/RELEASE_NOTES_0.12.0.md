# Agentic Harness v0.12.0

v0.12 makes completion conditions a harness-owned, immutable contract rather
than a worker-owned interpretation.

## Assurance contract

- Derives stable completion conditions from explicit lists, sentence boundaries,
  and imperative action series before the first worker cycle.
- Preserves ambiguous objectives intact instead of guessing at a decomposition.
- Keeps check-gated, specification-frozen, and high-assurance guarantees separate
  from Quick, Standard, and Thorough execution effort.
- Requires typed, current-run evidence with predeclared requirement coverage.
- Resolves `covers: ["*"]` to concrete frozen IDs before issuing evidence.
- Lets high-assurance operators review or edit conditions before execution.
- Supports operator-approved mid-run amendments as immutable revisions with new
  hashes and explicit invalidation of evidence tied to the previous revision.
- Reconstructs task-event evidence from harness-owned fields. A coding worker
  cannot promote a workspace event to `verified`, change its issuer, or grant it
  requirement coverage by editing serialized event JSON.
- Makes omitted review coverage fail closed. Project files must declare
  `review_covers`; generated starter profiles and the GUI write an explicit
  `covers: ["*"]` only when the user chooses an all-condition project check.

## Product and reliability

- Adds an editable plain-language approval dialog for initial and amended
  high-assurance specifications.
- Removes URL credential ingestion, supports environment-referenced GitHub
  credentials, and keeps browser session credentials in memory only.
- Adds frontend checks to CI and preserves cross-origin JSON errors on Windows.
- Binds each GUI specification approval to the reviewed goal ID, GoalSpec hash,
  and revision, preventing a stale dialog from approving a replacement task.
- Rejects cross-origin requests before reading their bodies and closes the
  connection, so partial request bodies cannot occupy handler threads.
- Makes GitHub Actions wait mode require a dispatch response containing a run
  ID or run URL. It no longer guesses from a concurrently changing workflow list.
- Tolerates files that disappear during concurrent workspace snapshots.
- Splits assurance UI, specification amendment, CLI assurance, reporting,
  autonomy support, and GUI authentication responsibilities into focused modules.

## Verification target

The release gate runs the complete Python suite, Ruff, strict mypy, Python
compilation, frontend syntax and behavior checks, package build/install smoke,
and the GitHub matrix on Linux, Windows, and macOS with Python 3.11 through 3.14.

## Compatibility

- `LocalLLMAdapter` remains importable but deprecated. New integrations should
  use the structured `model_agent` worker.
- `github_token` remains readable only as a legacy migration path. New and
  updated configuration should use `github_token_env`; the GUI does not offer a
  plaintext GitHub-token field.
- GoalSpec v1, evidence v2, assurance-mode names, and amendment contracts are
  unchanged by these correctness fixes.
