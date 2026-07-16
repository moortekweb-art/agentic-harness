# GUI Deployment Guide

`agentic-harness-gui` is the service executable from the same
`local-agentic-harness` install as the CLI. It serves packaged static assets and
uses the selected workspace's `.agentic-harness/` state.

This guide deploys the public package as a single-user self-hosted application.
It does not convert the process into a shared public SaaS. Do not point anonymous
internet traffic at a real workstation, its source tree, or its provider keys.

## Local service

Install the release in a dedicated environment, choose the workspace, and copy
[`agentic-harness-gui.service.template`](agentic-harness-gui.service.template).
Replace:

- `<USER>` with the unprivileged service account;
- `<WORKDIR>` with the workspace;
- `<EXECUTABLE>` with the absolute `agentic-harness-gui` path;
- `<PORT>` with a stable local port; and
- `<TOKEN_ENV_FILE>` with an owner-readable environment file, or remove that
  line when the service is strictly loopback-only and no proxy will access it.

The template binds to loopback and passes `--project-dir <WORKDIR> --no-open`.
The service user needs write access to the workspace and `.agentic-harness/`
state, but should not have broader machine privileges.

Verify the exact installed candidate before switching a service:

```bash
agentic-harness-gui --help
agentic-harness --version
agentic-harness selftest
```

## Provider credentials

Prefer an environment-variable reference for a background service. Put the key
in an owner-readable environment file outside the repository and enter only the
variable name in Settings. Do not place a plaintext key in
`.agentic-harness/config.yml`, the unit file, command-line arguments, or a URL.

A session key is intentionally memory-only. It disappears on restart and is
suitable for an interactive loopback launch, not an unattended service.
Model connection validation is also session-scoped: after a GUI restart,
Settings must pass the structured-action test before a new model-backed task can
start.

## Network boundary

Keep the process on `127.0.0.1` whenever possible. A non-loopback bind is
refused unless `AGENTIC_HARNESS_GUI_TOKEN` is set. For a reverse proxy:

- set a strong GUI token and send it as an `Authorization: Bearer` header;
- add each proxy-facing hostname to `AGENTIC_HARNESS_GUI_ALLOWED_HOSTS`;
- preserve a trustworthy `Host` header;
- enforce authentication and transport encryption at the proxy or private
  network; and
- do not expose the control surface directly to the public internet.

## Hosted product boundary

A public demo or multi-user service must use a separate execution plane. At a
minimum it needs authenticated identities, a disposable isolated workspace per
run, per-user secret injection, outbound-network policy, concurrency and spend
quotas, request and artifact size limits, abuse monitoring, audit records, and
automatic teardown. The local GUI process is not that boundary, even when a
reverse proxy adds TLS and login. See [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) for
the release split and required acceptance gates.

Tailscale Serve can proxy a loopback service to an authenticated tailnet. Use
the current Tailscale documentation for the exact command and ACL syntax,
because those interfaces may change. Keep the harness itself loopback-bound and
verify the health, status alias, authenticated task reads, and unknown-route
behavior through the private URL.

## Safe rollout

1. Record the currently installed version, executable path, unit contents, and
   health response.
2. Back up `.agentic-harness/config.yml` and preserve every existing run
   directory.
3. Install the candidate in a separate virtual environment.
4. Run the full source or release-smoke verification before changing the unit.
5. Update the executable path, reload the unit manager, and restart once.
6. Verify `GET /api/health`, compatibility `GET /api/status`, readiness, setup,
   history, security headers, and JSON 404 behavior.
7. Open all four views, confirm Settings identifies **This project**, and verify
   managed deployments render it read-only.
8. Complete a harmless bounded goal and confirm the final independent check and
   evidence preview.

Rollback by restoring the prior executable path and restarting. Do not delete
the workspace state during rollback; a schema-compatible prior version can
still inspect preserved evidence, and an incompatible case should be diagnosed
from a copy.

## Recovery

Configuration lives at `.agentic-harness/config.yml`. Run
`agentic-harness report` before changing failed state. `agentic-harness restart`
retries the same failed goal while preserving its evidence and original
workspace baseline. A GUI Continue action retains the same goal identity and
may add an operator note.

After a process interruption, history and checkpoints remain durable. If the
profile used a session credential, re-enter it before continuing. Start a fresh
goal only when the work is intentionally separate or the prior goal is already
terminal.

## Release authority

Publishing and production restart are separate approvals. Before publishing,
protect both the `github-release` and `pypi` GitHub environments with an
appropriate deployment-branch/tag policy and required reviewers. The workflow
itself verifies the package version, exact tag commit, default-branch ancestry,
trusted CI result, distributions, installed package, release assets, and
checksums; repository owners must still configure the external environment and
branch protections.
