# GUI Deployment Guide

`agentic-harness-gui` is the long-running GUI service executable from the same
`local-agentic-harness` install as the `agentic-harness` CLI. Both use the
shared Python engine, `.agentic-harness/` project state, and packaged static
assets; deploying the GUI does not create a second package or repository.

## Local Service

Install `local-agentic-harness` in the environment you want the service to use,
then copy and customize
[`agentic-harness-gui.service.template`](agentic-harness-gui.service.template).
Replace its placeholders for the service user, working directory, executable or
virtual environment, and port. Remove the optional `--doc-root` argument when
the optional local-goal backend is not in use. Remove the optional token
environment-file line when no token is configured.

The template binds to `127.0.0.1` and includes `--no-open`, which is suitable
for a background service. Keep loopback binding by default. If you deliberately
expose the service beyond the host, use the GUI token and appropriate network
access controls.

## Private-Network Access

Tailscale Serve can publish a loopback-bound GUI to an authenticated private
network without changing the service binding. Configure Tailscale Serve for the
local GUI URL according to the Tailscale documentation, preserve the original
`Host` header, and treat Tailnet membership or a GUI token as the access-control
boundary. This is generic guidance: choose the Serve configuration, access
policy, and operational ownership appropriate for your machine.

## Recovery

Run `agentic-harness init` in a project to write
`.agentic-harness/config.yml`. If a goal fails, inspect `agentic-harness report`
first. `agentic-harness restart` retries the same failed goal while preserving
its evidence. Start a fresh goal only when the work is intentionally separate.
