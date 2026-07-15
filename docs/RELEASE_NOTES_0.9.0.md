# Agentic Harness v0.9.0

This release gives a first-time user a useful, verified result before asking
them to configure a coding agent, model server, cloud account, or API key.

## One-click first success

- A prominent **Try safe demo** action now appears in a fresh self-hosted
  workspace instead of forcing the Setup dialog open immediately.
- The demo runs the real harness engine in a permission-restricted temporary
  practice project. It cannot read or modify the selected project and is
  removed when the app process closes or real work starts.
- A scripted practice worker intentionally makes a false completion claim. The
  harness rejects it, repairs the calculator on the second pass, and accepts
  the task only after an independent command passes.
- Every demo surface says that the worker is scripted, no AI model is used,
  and the run demonstrates workflow and evidence rather than model quality.
- The completion receipt shows the failed first check, passing second check,
  changed file, attempt count, retry count, and a portable verification command.

## Detected local-model handoff

- Self-hosted Setup checks fixed loopback endpoints for Ollama and LM Studio
  through their bounded `/v1/models` APIs. It does not probe arbitrary hosts,
  follow redirects, or treat discovery as a successful model-generation test.
- A detected server is labeled in the provider list and can populate its local
  endpoint and reported model ID with one click.
- The existing connection test remains the readiness gate, so a server that
  lists models but cannot generate a valid response is reported as a failure.

## Deployment and safety boundaries

- The demo and local-model detection routes exist only on the portable embedded
  backend. Managed deployments reject them explicitly instead of silently
  changing their controller behavior.
- Session API-key handling, same-origin checks, trusted-host checks, and
  credential non-persistence are unchanged.
- In-flight demo progress stays indeterminate until independent verification
  finishes, and setup refreshes cannot overwrite the visible demo execution
  label with a misleading `Setup required` state.

## Verification

- The complete test suite passes with 1,131 tests and two expected platform
  skips.
- Ruff, strict mypy, Python compilation, frontend syntax, and the browser token
  race regression pass.
- A fresh browser journey completed the demo, displayed the rejected and passed
  verification attempts, opened the real-work setup handoff, detected Ollama,
  and populated its endpoint and model without browser or console errors.
