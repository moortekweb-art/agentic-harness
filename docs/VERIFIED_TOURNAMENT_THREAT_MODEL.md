# Verified tournament threat model

Verified Best-of-N treats every implementation candidate as capable of changing
any project file available to its configured worker. Git worktrees isolate
candidate file state; they do not reduce the worker process's operating-system
authority.

## Protected decisions

A candidate may be selected only when all of these invariants hold:

1. Every candidate starts from the same commit and immutable GoalSpec.
2. Repository-controlled verifier executables, test definitions, runner
configuration, explicit .NET/MSBuild input closure, protected directory membership, paths required to remain
   absent, and explicit custom assets are frozen before workers start.
3. A candidate that changes, adds, removes, or symlink-replaces any protected
   verifier input is ineligible even when its check exits successfully.
4. The selected patch passes the frozen checks in a fresh worktree whose full
   tracked and non-ignored state does not change during verification.
5. The original workspace remains clean until final verification succeeds, and
   the state applied there exactly matches the verified fingerprint.
6. An interrupted application is never accepted implicitly. Recovery restores
   the preimage only when receipt checksums and workspace fingerprints prove the
   state; otherwise operator review is required.

## Verifier boundary

The manifest includes repository-local command arguments, including argument
zero, plus known definitions for Python, Rust, Go, Maven, Gradle, and RSpec.
Package-manager test scripts are opaque and require explicit
`review.assets`; freezing only `package.json` is not treated as freezing the
script it delegates to. `dotnet test` also requires explicit `review.assets`
because filename conventions cannot represent the evaluated MSBuild input
closure. Standard Maven and Gradle test trees and repository directories named
directly in a verifier command have protected membership.

An acceptance boundary that a filename convention cannot represent is refused
rather than assumed:

- Cargo manifests and external integration tests under `tests/` can be frozen.
  Inline `#[cfg(test)]` modules, `#[test]` and `#[bench]` functions, and
  doctests inside editable Rust sources are not an independent acceptance
  boundary, because a candidate rewrites the assertion and the implementation
  in the same file. A `cargo` check refuses automatic inference when an
  editable tracked Rust source contains one; the operator moves the acceptance
  tests into a frozen integration-test directory or declares `review.assets`.
- Pytest `testpaths` are read from the first effective configuration file
  (`pytest.ini`, then `pyproject.toml`, `tox.ini`, and `setup.cfg`) and the
  selected trees are frozen with their membership, so a suite outside
  `tests/` is not silently editable. An entry that cannot be resolved to
  tracked files, an unreadable or dynamic declaration, and plugin loading
  through `pytest_plugins` or an `addopts` `-p` option all require explicit
  `review.assets`; the plugin module graph is not resolved automatically.
- Gradle build logic delegated through `apply from`, `apply(from = ...)`,
  `includeBuild`, or a `buildSrc` directory requires explicit `review.assets`,
  because freezing `build.gradle` does not freeze the scripts and included
  builds it delegates to.

The manifest records
both file hashes and protected path-set membership,
including sensitive paths that were absent at tournament start. Lexical
symlinks, symlinked parent components, Windows reparse points, and parent
traversal are rejected before hashing and on every drift check. Python
`-m pytest` and `-m unittest` checks are started with safe-path mode as a second
line of defense against repository-root module shadowing.

Custom runners whose repository dependency closure cannot be inferred must
declare `review.assets`, including the runner and every imported, sourced, or
data-file dependency. Declared directories freeze their complete tracked and
non-ignored membership. Those assets apply only when the configured review
command is the command being run; ad-hoc command overrides do not inherit an
unrelated manifest. An empty, untracked, or undeclared custom-verifier boundary
blocks the tournament.

## Transaction and recovery

The receipt records the base commit, preimage fingerprint, selected patch
checksum, expected verified fingerprint, and transaction phase. The phase is
durably written as `applying_verified` before the original workspace changes.
On restart, the GUI compares the actual state with both recorded fingerprints.
It reverses an interrupted application only from the exact staged state. If the
receipt had already reached `verified`, the GUI validates the immutable
GoalSpec, final review, base commit, and applied workspace fingerprint before
finishing the durable goal as done. Any divergent state remains blocked and is
not modified automatically.

## Non-goals

- Worktrees are not a sandbox for external coding agents.
- The harness does not prove that a verifier is semantically complete or useful.
- Ignored build caches may be created in temporary worktrees, but they are not
  included in candidate patches or applied to the original workspace.
- High-assurance specification approval remains outside the tournament path.
