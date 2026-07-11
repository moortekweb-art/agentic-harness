# Agentic Harness v0.7.0

v0.7.0 turns Agentic Harness into a provider-neutral, end-to-end local goal
product. The CLI and browser GUI now share the public execution engine instead
of requiring a machine-specific sidecar.

## Provider-neutral setup

- Choose an installed coding agent, a local OpenAI-compatible endpoint, or a
  cloud OpenAI-compatible endpoint.
- Use arbitrary model IDs rather than product-specific model presets.
- Store only an environment-variable reference, or enter a memory-only session
  key that disappears on restart.
- Require explicit consent before selected workspace excerpts and tool results
  are sent to a remote endpoint.
- Test reachability and the structured-action protocol before starting work.

Native provider-specific APIs are not bundled in this release. They can be used
through an OpenAI-compatible gateway or a separately installed execution
adapter.

## Real goal journey

- The default GUI backend now uses `Supervisor`, `AutonomousRunner`, and
  `ArtifactStore`, the same engine and project state as the CLI.
- One plain-language goal persists its plan, requirements, current subgoal,
  checkpoint, resource usage, result, and acceptance evidence.
- Ordered, sanitized task events show real tool activity while work is running.
- Progress is determinate only when a persisted plan or requirement count makes
  it measurable; Done requires independent deterministic review.
- Changed files and recorded artifacts have bounded previews, including
  read-only evidence browsing for earlier history entries.
- History survives browser and service restarts. Orphaned active work resumes
  when its credential and verification command are available.
- Stop is cooperative, preserves evidence, and prevents a late completion from
  being accepted.

## Bounded embedded agent

The built-in model worker can list, search, and read bounded workspace text;
create text files; perform compare-and-swap replacements; inspect Git state;
run operator-configured checks; and report a structured outcome. It has no
arbitrary shell, delete, install, service-control, publish, or general network
tool.

Workspace containment protects harness and VCS state, secret-file variants,
key material, symlink escapes, oversized or binary input, out-of-scope paths,
and unowned pre-existing changes. Text replacement uses the raw file hash and
preserves line endings. Requirement evidence from the embedded worker must cite
a successful durable event.

## Safety and resilience

- Whole-goal budgets cover cycles, elapsed time, provider tokens, provider
  calls, and tool calls. Exhaustion never counts as completion.
- Repeated identical observations have a stable semantic fingerprint and
  trigger the no-progress guard instead of appearing novel because of time or
  sequence fields.
- Configured checks run without a shell, with bounded output and a minimal
  non-secret environment.
- Worker transcripts, event files, and state writes use private atomic files;
  provider credentials are excluded from check environments and exact
  credentials reflected by a provider are scrubbed before tool execution or
  outcome persistence.
- Remote HTTP endpoints, URL credentials/query strings, and redirects are
  rejected; provider responses and timeouts are bounded.
- Non-loopback GUI binding requires a bearer token. Host validation,
  same-origin JSON writes, rate limits, no-store API responses, security
  headers, and header-only WebSocket authentication protect the local control
  surface.

## Portable release pipeline

- CI covers Python 3.11 through 3.14 on Linux, macOS, and Windows.
- Third-party actions are pinned to current full release commits and checkout
  credentials are not persisted.
- A version tag must match package metadata and the immutable triggering event
  SHA, be reachable from the default branch, and have successful
  default-branch CI for the exact commit.
- The workflow stages a draft release, keeps checksums out of PyPI inputs,
  serializes runs per tag, refuses mismatched existing draft assets, publishes
  with OIDC, and makes the GitHub release public only after protected
  publication succeeds.

Repository owners must still configure branch/ruleset protection and protected
`pypi` and `github-release` environments before publishing.

## Optional Turnstone boundary

[Turnstone](https://github.com/turnstonelabs/turnstone) remains an optional
external orchestration framework, not a bundled dependency or prerequisite.
See [Turnstone integration](TURNSTONE_INTEGRATION.md) for the compatibility
boundary and the direct-adapter work that is explicitly not included in v0.7.0.

## Upgrade notes

- Existing `.agentic-harness/` goal state remains the durable source of truth.
- The public GUI default changes to the embedded backend. Select
  `--backend local-goal` only when intentionally using the optional legacy
  command adapter.
- Model-agent configuration rejects plaintext API keys and remote endpoints
  without persisted data-transfer consent.
- Python 3.11–3.14 is supported; Python 3.15 and later are intentionally outside
  this release's declared range until CI coverage is added.
