# GUI Architecture

## Release Shape

Agentic Harness v0.6.29 is a Python application with a local browser interface.
The Python process serves packaged HTML, CSS, and JavaScript from loopback and
adapts an optional local-goal installation into a stable human-facing API.

The v1 release deliberately does not require Electron, Tauri, Qt, GTK, a Node
runtime, or a separately hosted web service.

## Components

### CLI Entry Point

`agentic-harness gui` starts the local server. It binds to `127.0.0.1` and asks
the operating system for a free port unless `--port` is supplied. The selected
URL is printed after the bind succeeds and is opened automatically unless
`--no-open` is used.

### Local Goal Bridge

`agentic_harness/core/local_goal_bridge.py` is the replaceable backend adapter.
It discovers the optional worker, maps the four human modes to supported
execution routes, invokes commands without a shell, and returns structured
command results.

Discovery order is explicit `--doc-root`, non-empty
`AGENTIC_HARNESS_DOC_ROOT`, then the current directory. The executable can be
overridden with `AGENTIC_HARNESS_LOCAL_GOAL`.

### GUI API

`agentic_harness/gui/api.py` converts backend output into stable states:

- `ready`
- `starting`
- `working`
- `checking`
- `needs_review`
- `done`
- `blocked`
- `stopped`

The API keeps human summaries separate from raw backend evidence. Internal
actor names and route details are retained only in `advanced_details`.

### Local Server

`agentic_harness/gui/server.py` serves package resources and JSON routes using
the Python standard library. It also provides a small WebSocket status stream
and an in-memory GUI session with export/import support.

### Browser Client

`agentic_harness/gui/static/` contains the browser application. It renders the
four modes, readiness gate, current work, evidence, history search, session
import/export, theme choice, shortcuts, and local form undo/redo.

## API Surface

Read routes:

- `GET /api/health`
- `GET /api/status` (deprecated compatibility alias for `/api/health`)
- `GET /api/modes`
- `GET /api/readiness`
- `GET /api/tasks`
- `GET /api/tasks/current`
- `GET /api/tasks/history?q=...`
- `GET /api/tasks/current/details`
- `GET /api/session`
- `GET /api/tasks/stream` with a WebSocket upgrade

Write routes:

- `POST /api/tasks`
- `POST /api/tasks/bulk`
- `POST /api/tasks/current/watch`
- `POST /api/tasks/current/accept`
- `POST /api/tasks/current/continue`
- `POST /api/tasks/current/stop`
- `POST /api/session/import`

The main UI emphasizes one task decision at a time even though the API retains
bulk support for controlled integrations.

`/api/health` is the canonical liveness and diagnostic route. `/api/status`
returns the same payload for compatibility with older integrations; new clients
should use `/api/health`. `/api/readiness` remains the readiness-specific route.

## Task Contract

Normalized task records include:

- Human title, status, label, summary, and progress.
- Whether a human decision is required.
- Changed files, verification, and artifacts.
- Current local-loop stage and readiness gate.
- Updated time and command metadata.
- Advanced details containing raw evidence.

The GUI consumes normalized fields instead of depending directly on the
local-goal JSON shape. This boundary allows the worker implementation to evolve
without rewriting the human interface.

## Safety Model

- Default network exposure is loopback only.
- Non-loopback binding prints an explicit warning.
- `AGENTIC_HARNESS_GUI_TOKEN` gates API actions and the WebSocket stream when
  configured.
- Token comparison is constant-time; browser token state is session-only.
- Static assets never contain the configured token.
- State-changing browser requests and WebSocket upgrades must be same-origin.
- API writes require `application/json` bodies no larger than 1 MiB.
- Requests are rate-limited and unknown API routes return JSON 404 responses.
- New starts are blocked when current work requires review.
- Raw commands, paths, and backend output stay in Advanced details.

Bearer tokens and private-network membership are access gates, while the
same-origin and JSON requirements defend the browser boundary. The server is
still a local control surface, not a hardened public-internet deployment.

## Upgrade Path

The local-goal bridge is backend adapter v1, not a permanent internal design.
Future versions can move toward a Codex `/goal`-style experience by adding:

- Goal decomposition and explicit subgoal state.
- Safe self-directed continuation after failed checks.
- Recovery across worker exits and machine restarts.
- Context summarization over long work windows.
- Task-aware worker selection.
- Durable session history and schema migrations.
- Stronger normalized blocker and final-result contracts.

These changes should preserve the current GUI API or introduce versioned
contracts so old evidence and clients remain readable.

## Release Verification

GUI releases must prove:

- Mode and route mappings with unit tests.
- Every API action and token boundary with server tests.
- Frontend token-race behavior with the JavaScript harness.
- Desktop and narrow-browser rendering without overflow.
- Wheel and source distributions both contain all static assets.
- Each distribution installs and runs in a fresh virtual environment.
- Loopback and explicit-port launch behavior work as documented.
