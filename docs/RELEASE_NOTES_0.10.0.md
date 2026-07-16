# Agentic Harness v0.10.0

This release turns the self-hosted GUI into a clearer product for new users
while preserving the fail-closed execution, verification, and local/cloud
boundaries required by advanced managed installations.

## Predictable product flow

- Home, Tasks, History, and Settings now provide a familiar navigation model
  around one plain-language task field.
- Task effort, execution route, and model profile are separate decisions
  instead of being mixed into one technical mode selector.
- The **What to expect** summary explains the assistant, execution location,
  change boundary, setup state, and current route availability before work can
  start.
- Local model setup can detect Ollama, LM Studio, vLLM, and llama.cpp on fixed
  loopback endpoints, while discovery remains distinct from a successful
  structured-action connection test.
- Desktop and narrow mobile layouts include clearer unavailable-route reasons,
  keyboard-focus preservation, and updated public screenshots.

## Truthful managed routes

- Mode 1 is the selectable local implementation route.
- Mode 2 remains visible as a supervision policy and cannot masquerade as an
  independent start command.
- Mode 3A requires explicit scope plus verified registry, lane, worker, and
  adapter readiness.
- Mode 4 stays unavailable until the backend advertises a distinct audit-only
  dispatch contract.
- Mode 4B and the retired experimental alias cannot reactivate a disabled
  implementation canary.
- Legacy aliases pass through the same current safety checks, and local choices
  cannot silently fall back to a cloud route.

## Model-profile and session safety

- Managed Qwen and Ornith starts are serialized across profile inspection,
  activation or restoration, goal start, and run attachment.
- Ornith attachment is checked against the exact started run; uncertain
  attachment or recovery state requires reconciliation instead of inviting a
  duplicate start.
- Managed GUI labels and history survive restarts only when bound to the exact
  durable run identity.
- Persisted GUI state is size-limited, recursively redacted, atomically written,
  hardened to POSIX mode `0600`, and rejects unsafe symlink or reparse paths.
- Session credentials remain out of URLs, storage, responses, exports, and
  persisted task state.

## Portable setup and verification

- Invalid or unsafe existing configuration fails closed without overwriting
  the user's bytes.
- Automatic project-check detection covers common Python, JavaScript, Rust,
  Go, Java, .NET, and Ruby projects while retaining a technical override.
- Model connections must pass the same structured `report_outcome` contract
  used by real execution before the workspace becomes ready.
- Release smoke now installs and exercises wheel and source distributions in
  isolated environments without inheriting misleading parent `PYTHONPATH`
  dependencies.

## Release verification

The release gate includes the complete Python suite, frontend race tests,
Ruff, mypy, compile checks, wheel and source-distribution smoke tests, and the
desktop/mobile browser journey defined in `docs/RELEASE_CHECKLIST.md`.
