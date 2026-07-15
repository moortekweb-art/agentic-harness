# Agentic Harness v0.8.3

This release makes local-versus-cloud execution explicit and gives first-time
users a clearer path when they do not have a cloud account.

## Clear execution and data location

- Configured local models now say that data stays local in the workspace strip.
- Installed coding agents explain that the selected agent owns its model and
  local-or-cloud routing.
- Managed local-goal tasks show whether an active route uses local execution,
  cloud planning, or both. The private hybrid route now reads as a cloud planner
  plus local model instead of the ambiguous `Verified agent ready` label.

## Better no-cloud setup

- Local setup explains that no cloud account is required and that a compatible
  model server must already be running.
- New presets cover Ollama, LM Studio, and vLLM-compatible local servers.
- Local and cloud templates are kept in separate lists so a cloud-only provider
  is not presented as a local setup option.
- Changing between local and cloud execution clears incompatible endpoint and
  credential values instead of silently carrying them across.
- Users without a model or account are pointed to the controlled offline demo,
  which demonstrates failure, repair, and independent verification with a mock
  coding agent.

## Verification

- The complete test suite passes with 1,126 tests and two expected platform
  skips.
- Ruff, strict mypy, Python compilation, frontend syntax, and the browser token
  race regression pass.
- A fresh browser run configured a keyless local endpoint, submitted a normal
  sentence, created a file, independently verified it, and displayed the final
  evidence receipt.
- The setup and receipt have no horizontal overflow at a 390 by 844 viewport.
