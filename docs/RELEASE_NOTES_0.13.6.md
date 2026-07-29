# Agentic Harness v0.13.6

Version 0.13.6 closes verifier and managed-GUI trust-boundary gaps found during
maintainer review with automated, adversarial, and AI-assisted testing. It is
not a claim of an independent security audit or completed external beta.

## Verifier boundaries fail closed

- Requires explicit `review.assets` for `npm test`, `pnpm test`, `yarn test`,
  and `bun test`, so freezing a package manifest cannot silently trust a
  mutable delegated script.
- Freezes membership for standard Maven and Gradle test trees and repository
  directories named directly in verifier commands.
- Treats `dotnet test` as an evaluated MSBuild boundary that cannot be inferred
  safely from filenames. It now requires explicit `review.assets` covering the
  selected test projects, source directories, imported build files, and other
  repository-controlled inputs.
- Adds adversarial regressions for modified package scripts and
  candidate-added test sources.

## Managed GUI request integrity

- Keys persisted GUI sessions by the canonical controller documentation root,
  canonical `local-goal` executable, current OS user, and selected project.
  Stored state with a different identity is rejected.
- Makes task status viewing and WebSocket streaming observation-only. Progress
  remains the responsibility of the configured background supervisor.
- Gives each managed GUI start a cryptographically random request ID and binds
  the objective, route, work area, verification, model profile, and supervision
  selections into the created controller ticket before accepting the start.
- Requires every cloud-build and audit enqueue to return an authoritative queue
  ID, retains that ID as the GUI-owned task identity, and refuses to adopt or
  send guidance to a different active task.
- Rejects model-profile attachment receipts that say only `attached: true`
  without identifying the run that owns the attachment.
- Uses one credential-preview denylist in both embedded and managed GUI paths,
  including common cloud, OAuth, service-account, vault, Docker, secrets-file,
  and private-key filenames. Permitted text is redacted in native form before
  JSON response serialization, including quoted JSON credential fields.

## Compatibility

Custom package-manager checks that previously relied on inferred
`package.json` coverage must add their delegated verifier files and dependency
directories to `review.assets`. `dotnet test` configurations must declare their
evaluated repository input closure explicitly. Existing managed GUI session
files are not loaded across a different controller workspace identity.
