# Agentic Harness v0.11.0

v0.11 turns the browser interface into a clearer product for first-time users
without changing its fail-closed execution contract.

## Product clarity

- Home uses plain-language task starters, work-area and completion-check labels,
  and friendly execution names. Backend mode identifiers remain available in
  Advanced details and diagnostics.
- The execution summary never invents a fallback. Unavailable local, mixed, or
  cloud routes remain disabled with a concrete reason.
- Tasks retain the trusted Outcome, What changed, and Independent verification
  hierarchy. History adds status and route filters plus evidence-oriented cards.

## Guided setup

- Settings now presents a visible Choose, Connect, Verify journey.
- Fixed-loopback discovery still supports Ollama, LM Studio, vLLM, and llama.cpp,
  including multiple-model selection and a required structured-action test.
- Setup and recovery states use specific guidance while invalid or symlinked
  configuration remains read-only and fail-closed.

## Refined workbench identity

- The linked-workflow mark remains the product identity with a normalized SVG
  control-icon family.
- Three build-time img2img illustrations cover local connection, recoverable
  setup, and verified evidence/archive states. They are optimized, packaged,
  self-contained WebP assets with recorded provenance.
- No image generation, external font, CDN, or art service is used at runtime.

## Compatibility and correctness

- Managed route keys, backend IDs, legacy aliases, and safety checks are
  unchanged. `/api/modes` gains additive friendly-name, purpose, location,
  availability, capability, and advanced-detail metadata.
- Managed model profiles expose capability evidence and only report runtime
  context, concurrency, MTP, and quantization facts when the backend supplies
  them.
- `agentic-harness selftest` uses the running interpreter for its internal
  worker and review processes, so an absolute virtual-environment executable
  works even when its `bin` directory is absent from the parent `PATH`.
