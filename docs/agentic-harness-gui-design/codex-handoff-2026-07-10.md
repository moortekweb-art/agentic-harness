# Agentic Harness GUI Codex Handoff - 2026-07-10

Generated from live checks on `ai-inference` at:

- Local time: `2026-07-09T18:28:02-07:00`
- UTC time: `2026-07-10T01:28:03+00:00`
- Repo: `/mnt/raid0/home-ai-inference/agentic-harness`
- Branch: `productize-v0.6.11`
- Current HEAD at time of handoff: `14875c8 feat: redact secrets in run artifacts`

This packet is for another Codex or local agent taking over the Agentic Harness GUI work. It intentionally includes product decisions, implementation facts, live runtime state, validation evidence, known risks, and next-step guidance.

## Executive Summary

Agentic Harness now has a local browser GUI implementation in the `ai-inference` checkout. The GUI is intended to let a non-programmer choose a human-friendly work mode, type a plain-English request, start work, and see simple progress without seeing planner names, executor names, shell commands, queue JSON, run directories, logs, or `Mode 3A` wording in the main UI.

The selected product direction is **Calm Local Operations**: a quiet local control panel that exposes only the decisions the human needs to make. Technical evidence remains available in Advanced details.

The live GUI currently runs at:

```text
http://127.0.0.1:8769/
```

Do not assume `8765` is the Agentic Harness GUI on `ai-inference`. It is currently occupied by Utility Hub. The GUI server now has deterministic fallback-port behavior, so the correct URL is the one printed by `agentic-harness gui` at startup.

The current runtime readiness state is:

```text
state: needs_review
can_start: false
requires_review: true
production_ready: false
agent_loop.stage: Review
```

That means the GUI is running, but the local-goal lane has work waiting for review. Do not start another simple-UI task or claim full production readiness until that review state is resolved deliberately.

## Source Priority For Future Agents

Use current/live sources before old session summaries.

Start with the CodexLane mirror when dealing with this local AI infrastructure:

```text
/Users/MikeMacMini/CodexLane/knowledge/CLUSTER_LEARNING_BRIEF.md
/Users/MikeMacMini/CodexLane/knowledge/cluster_docs_index.json
```

Then open the matching source docs under:

```text
/Users/MikeMacMini/CodexLane/documentation/
```

The most relevant source docs for this project are:

```text
/Users/MikeMacMini/CodexLane/documentation/codex-ai-inference-access-runbook.md
/Users/MikeMacMini/CodexLane/documentation/projects/HERMES_CONTROLLER_FOUR_MODE_HARNESS_TRANSFER_2026-07-01.md
/Users/MikeMacMini/CodexLane/documentation/reference/LOCAL_NODE1_CODEX_LIKE_GOAL_WORKER.md
/Users/MikeMacMini/CodexLane/documentation/HERMES_CONTROLLER_ORCHESTRATION_RUNBOOK_2026-05-24.md
```

For actual behavior, inspect the live code and runtime, not only docs:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && git status --short'
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m pytest tests/test_gui_api.py tests/test_local_goal_bridge.py -q'
ssh ai-inference 'curl -s http://127.0.0.1:8769/api/health | python3 -m json.tool'
```

## Current Git State

At handoff time the repo is dirty and contains substantial uncommitted/untracked GUI work. Do not run broad staging, cleanup, reset, checkout, or format commands without first deciding ownership.

Current status:

```text
 M README.md
 M agentic_harness/cli.py
 M pyproject.toml
?? agentic_harness/core/local_goal_bridge.py
?? agentic_harness/gui/
?? codex-app-design-workflow-plan.md
?? codex-app-gui-design-goal.md
?? codex-app-gui-design-result.md
?? docs/assets/gui-concepts/
?? gui-implementation-plan.md
?? linux-gui-agentic-harness-plan.md
?? tests/test_gui_api.py
?? tests/test_gui_api.py.backup
?? tests/test_local_goal_bridge.py
```

This handoff file itself is also intentionally uncommitted:

```text
?? AGENTIC_HARNESS_GUI_CODEX_HANDOFF_2026-07-10.md
```

The directory `agentic_harness/gui/` currently contains Python source, static frontend files, backup files, and `__pycache__` files because the whole directory is untracked. Before committing, inspect and selectively stage only intended files. Likely exclude `__pycache__` and decide whether backup files should be deleted or left out.

## Live Runtime State

Current relevant listeners on `ai-inference`:

```text
0.0.0.0:8765       Utility Hub / clawd utility-site server, pid 245232
0.0.0.0:8766       unified portal, pid 1938532
127.0.0.1:8767     cluster portal, pid 10178
127.0.0.1:8768     hub API proxy, pid 2363
127.0.0.1:8769     Agentic Harness GUI, pid 389026
```

Current tmux process:

```text
session: pop-os
window: 6:harness-gui
pane pid: 389026
command: python3
```

The GUI was restarted with:

```bash
tmux respawn-pane -k -t pop-os:harness-gui "cd /mnt/raid0/home-ai-inference/agentic-harness && PYTHONUNBUFFERED=1 python3 -m agentic_harness.cli gui --host 127.0.0.1 --no-open"
```

Expected startup behavior:

- The GUI first attempts `127.0.0.1:8765` unless another port is explicitly provided.
- If that port is unavailable and fallback is allowed, it tries `8766`, `8767`, and upward in order.
- On current `ai-inference`, `8765` through `8768` are occupied, so the GUI lands on `8769`.
- If an explicit busy port is requested with fallback disabled, the server raises a clear port-unavailable error.

## Current Readiness Gate

Live `GET /api/health` includes:

```json
{
  "app": "agentic-harness",
  "local_goal_available": true,
  "local_goal_path": "/mnt/raid0/documentation/scripts/local-goal",
  "ok": true,
  "readiness": {
    "active_run_dir": "/mnt/raid0/documentation/reports/local-node1-goal-harness/runs/20260710T003059Z-transferred-codex-goal-continue",
    "can_start": false,
    "label": "Needs review",
    "next_action": "Review or continue the current work before starting another task.",
    "production_ready": false,
    "requires_review": true,
    "state": "needs_review",
    "summary": "Worker stopped and says it is done. Hermes watcher will review it automatically before any new Node1 goal starts."
  }
}
```

Live `GET /api/readiness` exposes the same readiness object directly.

Interpretation:

- The GUI backend and local-goal bridge are available.
- The active local-goal run is awaiting review.
- The simple Start flow should block new work until this is resolved.
- This is deliberate; it prevents hidden queue piling and premature "done" claims.

The active run path is:

```text
/mnt/raid0/documentation/reports/local-node1-goal-harness/runs/20260710T003059Z-transferred-codex-goal-continue
```

## Product Goal

The GUI exists because the command-line harness is powerful but too backend-shaped for a non-programmer. The user-facing goal is:

1. Choose one of four human modes.
2. Type the desired outcome in plain English.
3. Start the work.
4. See simple progress.
5. Review, accept, continue, stop, or understand blocked state.

The main UI must hide:

- Planner names.
- Executor names.
- Worker names.
- Queue JSON.
- Shell commands.
- Logs.
- Run directories.
- `Mode 3A` wording.

Those details are allowed only in Advanced details.

## Required Main Screens

The design package required these screens, and the implementation follows that state-based shape:

1. **Start Work**
   - Four mode cards.
   - Plain-English task box.
   - Optional boundaries / checks.
   - Start button.
   - Local readiness indicator.

2. **Active Work**
   - Status band.
   - Progress indicator.
   - Current summary.
   - Local loop stage.
   - Buttons for status/watch/continue/stop when appropriate.
   - Advanced details collapsed by default.

3. **Needs Review**
   - Human-readable review state.
   - Clear decision actions: Accept, Ask it to continue, Stop.
   - Review/blocking explanation.
   - Advanced details available but not primary.

4. **Done / Blocked**
   - Done state only when review/acceptance says it is done.
   - Blocked state when the worker cannot proceed or the backend is unavailable.
   - Honest next action, not generic success copy.

## Required Human Modes

The main UI exposes exactly these four labels:

| Key | Number | Main label | Purpose |
| --- | ---: | --- | --- |
| `local` | 1 | Use this computer | Small, bounded work that should stay on the Linux machine. |
| `guided` | 2 | Let GLM guide the plan | Important local work where GLM helps shape the approach. |
| `cloud` | 3 | Let GLM carry a long task | Longer work where GLM carries most of the task under review gates. |
| `experimental` | 4 | Try experimental GLM | Tiny safe experiments on a newer GLM path. |

Implementation mapping in `agentic_harness/core/local_goal_bridge.py`:

- `local` -> `local-goal quick-start --executor opencode --goal ...`
- `guided` -> `local-goal premium-start --planner glm-5.2 --executor opencode --goal ...`
- `cloud` -> `local-goal enqueue --planner glm-5.2 --executor opencode --executor-worker opencode-glm-build --goal ...`
- `experimental` -> `local-goal enqueue --planner none --executor opencode --executor-worker glm52-direct-implementation-canary --goal ...`

Important product distinction: these backend route names are not main-UI language.

## Design Package Work

Initial design work was produced in the Mac clone and then exists in the remote repo:

```text
codex-app-gui-design-goal.md
codex-app-design-workflow-plan.md
linux-gui-agentic-harness-plan.md
codex-app-gui-design-result.md
docs/assets/gui-concepts/workbench-start.png
docs/assets/gui-concepts/run-room-active.png
docs/assets/gui-concepts/review-desk.png
```

The design result compares three visual directions:

1. **Calm Local Operations**
   - Recommended direction.
   - Quiet local control panel.
   - Best first-run experience for non-programmers.
   - Keeps mode selection, task input, and safety scope visible together.

2. **Friendly Assistant Control**
   - Not selected as the full direction.
   - Keep its reassuring copy for Needs Review, Blocked, quiet-worker states, and error recovery.
   - Avoid making the whole app chat-like or vague.

3. **Power User Cockpit**
   - Not selected as the full direction.
   - Keep its compact timeline, evidence grouping, and Advanced details placement.
   - Avoid turning the main surface into a developer dashboard.

Recommended visual rules:

- Use warm off-white or very light neutral canvas.
- Use charcoal high-contrast text.
- Use muted teal for primary progress/action.
- Use amber for needs-review/attention/experimental states.
- Use restrained red only for destructive stop actions.
- Keep cards at 8px radius or less.
- Do not use terminal-black main panels, giant hero sections, decorative blobs, or raw log surfaces.

## Implementation Files

Core bridge:

```text
agentic_harness/core/local_goal_bridge.py
```

GUI backend:

```text
agentic_harness/gui/__init__.py
agentic_harness/gui/api.py
agentic_harness/gui/server.py
```

GUI frontend:

```text
agentic_harness/gui/static/index.html
agentic_harness/gui/static/app.js
agentic_harness/gui/static/styles.css
```

CLI/package integration:

```text
agentic_harness/cli.py
pyproject.toml
README.md
```

Tests:

```text
tests/test_gui_api.py
tests/test_local_goal_bridge.py
```

Design/planning docs:

```text
codex-app-gui-design-result.md
gui-implementation-plan.md
```

## Backend API Contract

Implemented GET routes:

```text
GET /api/health
GET /api/modes
GET /api/readiness
GET /api/tasks
GET /api/tasks/current
GET /api/tasks/history?q=...
GET /api/tasks/current/details
GET /api/session
GET /api/tasks/stream    # WebSocket upgrade required
```

Implemented POST routes:

```text
POST /api/tasks
POST /api/tasks/bulk
POST /api/tasks/current/watch
POST /api/tasks/current/accept
POST /api/tasks/current/continue
POST /api/tasks/current/stop
POST /api/session/import
```

Unknown API routes return JSON 404:

```json
{"ok": false, "error": "not found"}
```

`/api/tasks/stream` is a WebSocket endpoint. A plain HTTP request without the correct upgrade headers returns a JSON error. That does not mean WebSocket support is missing.

## API Response Concepts

`GET /api/modes` returns human mode records like:

```json
{
  "modes": [
    {
      "key": "local",
      "number": 1,
      "label": "Use this computer",
      "best_for": "small, bounded work that should stay on this Linux machine",
      "caution": "best when the work is clear and only one task should move"
    }
  ]
}
```

Task payloads include:

```text
id
human_title
status
status_label
progress
summary
needs_human
changed_files
verification
artifacts
agent_loop
readiness_gate
metadata
advanced_details
```

The main UI should use the human fields. `advanced_details` may contain command args, raw JSON payloads, stdout/stderr, run dirs, planner/executor names, and should stay collapsed by default.

## Local Agent Loop Integration

A later pass incorporated the local agent-loop/readiness idea from:

```text
/mnt/raid0/home-ai-inference/agentic_ai_analysis.md
```

That source discusses local agentic AI loops and production-readiness issues. It should be treated as a useful analysis memo, not a perfect source of truth for external video content.

Implemented practical pieces:

- `GET /api/readiness` exposes current readiness separately.
- `GET /api/health` embeds the readiness object.
- Task payloads include `agent_loop` and `readiness_gate`.
- The frontend displays **Readiness gate** and **Local loop**.
- The Start API blocks new simple-UI starts when current local-goal state requires review.
- A parser bug was fixed so JSON containing `"accepted": false` is not misclassified as `done` merely because it contains the word `accepted`.

Loop stages shown to the user:

```text
Perceive -> Plan -> Act -> Check -> Review
```

Status-to-stage mapping:

```text
ready        -> Perceive
starting     -> Plan
working      -> Act
checking     -> Check
needs_review -> Review
done         -> Review
blocked      -> Review
stopped      -> Review
```

Readiness guardrails currently exposed:

```text
One visible task decision at a time.
Review gates must pass before done is trusted.
Raw commands and run paths stay in Advanced details.
```

## No-Babysitting Policy

`GET /api/health` includes:

```json
{
  "no_babysitting": {
    "enabled": true,
    "policy": "The worker should move safe work forward without repeated check-ins.",
    "human_review_statuses": ["needs_review", "blocked"]
  }
}
```

Interpretation:

- Routine safe work should move forward without repeated check-ins.
- Review gates remain real.
- `needs_review` and `blocked` are human-decision states.
- The GUI should never imply broad autonomous production authority beyond what is actually proven by local-goal state.

## Security / Local Safety Behavior

Implemented or present in current code:

- Local-only default host: `127.0.0.1`.
- Optional `AGENTIC_HARNESS_GUI_TOKEN` bearer token for API access.
- Query-token support for token-protected browser/WebSocket paths.
- Simple rate limiting: 240 requests per 60 seconds per client key.
- Unknown API routes return JSON 404.
- Static file serving prevents nested paths and dotfile traversal by accepting only top-level packaged static files.
- Advanced details are available but not main UI content.

Still worth future hardening before broader exposure:

- Keep the server local-only unless a separate auth/reverse-proxy decision is made.
- Add CSRF consideration if it is ever exposed beyond localhost.
- Add stronger auth/session UX if non-local access is desired.
- Do not put raw run evidence or logs on a public network.

## Frontend Features Present

Current static frontend includes:

- Four human mode cards.
- Plain-English objective textarea.
- Safe area/check metadata fields.
- Readiness card.
- Local loop display.
- Current work display.
- Progress/status rendering.
- Advanced details drawer.
- Task history list.
- History search.
- Export session to clipboard.
- Import session via pasted JSON.
- WebSocket live status updates.
- Dark/light theme switching.
- Keyboard shortcuts.
- Local form undo/redo behavior.

Known frontend caveat:

- I did not run a fresh Playwright screenshot pass in the final handoff step. The API/server tests are current, but visual QA should still be performed before calling the UI polished.

## Routing / Port Conflict Fix

Problem found during live validation:

- `http://127.0.0.1:8765/api/modes` was not Agentic Harness.
- Port `8765` belongs to Utility Hub on `ai-inference`.
- Utility Hub returned JSON 404 for Agentic Harness routes.
- Earlier reports that pointed users to `8765` for the harness were wrong for this machine.

Implemented fix:

- `serve_gui()` still defaults to port `8765`.
- `create_gui_server()` now supports deterministic fallback when fallback is allowed.
- It tries `port + 1` through `port + 50`, skipping occupied ports.
- It prints the actual chosen URL.
- Tests cover fallback behavior and explicit busy-port rejection.
- README now says that on `ai-inference`, Utility Hub owns `8765`, so use the printed fallback URL.

Current result:

```text
Agentic Harness GUI runs on http://127.0.0.1:8769/
```

## Validation Evidence

Focused tests passed after readiness integration:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m pytest tests/test_gui_api.py tests/test_local_goal_bridge.py -q'
```

Result:

```text
19 passed in 4.09s
```

Full test suite passed after the GUI/routing work:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m pytest -q'
```

Result:

```text
673 passed in 15.90s
```

Syntax checks passed after readiness integration:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m py_compile agentic_harness/gui/api.py agentic_harness/gui/server.py agentic_harness/core/local_goal_bridge.py agentic_harness/cli.py'
```

Result: passed.

Frontend JavaScript parse check passed:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && node --check agentic_harness/gui/static/app.js'
```

Result: passed.

Live API checks passed:

```bash
curl -s http://127.0.0.1:8769/api/health
curl -s http://127.0.0.1:8769/api/readiness
curl -s http://127.0.0.1:8769/api/modes
curl -s http://127.0.0.1:8769/api/tasks/current
```

Key observed live facts:

- `/api/health` returns `app: agentic-harness`.
- `/api/health` returns `local_goal_available: true`.
- `/api/health` returns readiness state `needs_review`.
- `/api/readiness` returns `can_start: false` and `requires_review: true`.
- `/api/modes` returns the four required human labels.
- `/api/tasks/current` returns the current local-goal state with Advanced details.

Live Start-block behavior was verified:

- `POST /api/tasks` while readiness is `needs_review` returns status `needs_review`.
- It does not enqueue a new task through the simple UI while existing work awaits review.

## Test Coverage Added / Present

Current collected focused tests:

```text
tests/test_gui_api.py::test_gui_modes_use_human_labels
tests/test_gui_api.py::test_task_from_command_result_maps_review_state
tests/test_gui_api.py::test_task_from_command_result_does_not_treat_accepted_false_as_done
tests/test_gui_api.py::test_task_from_command_result_maps_failed_command_to_blocked
tests/test_gui_api.py::test_start_task_uses_bridge_human_goal
tests/test_gui_api.py::test_start_task_blocks_when_current_work_needs_review
tests/test_gui_api.py::test_gui_server_get_api_routes_return_json
tests/test_gui_api.py::test_gui_server_unknown_api_route_returns_json_404
tests/test_gui_api.py::test_gui_server_falls_back_when_default_port_is_busy
tests/test_gui_api.py::test_gui_server_rejects_busy_explicit_port
tests/test_gui_api.py::test_gui_server_post_task_workflow_routes
tests/test_gui_api.py::test_gui_server_keeps_task_history_and_searches
tests/test_gui_api.py::test_gui_server_bulk_tasks_returns_created_tasks
tests/test_gui_api.py::test_gui_server_session_export_import_round_trips_history
tests/test_gui_api.py::test_gui_server_websocket_status_upgrade_sends_json_frame
tests/test_local_goal_bridge.py::test_build_mode3a_goal_hides_worker_details_behind_plain_objective
tests/test_local_goal_bridge.py::test_local_goal_bridge_enqueue_mode3a_calls_local_goal
tests/test_local_goal_bridge.py::test_friendly_queue_summary_prefers_ticket_id
tests/test_local_goal_bridge.py::test_friendly_queue_summary_handles_empty_output
```

## What I Personally Worked On In This Thread

### 1. Design Package

Produced/updated the design package in the Mac repo and ensured the same design artifacts are present in the live repo:

- `codex-app-gui-design-result.md`
- `docs/assets/gui-concepts/workbench-start.png`
- `docs/assets/gui-concepts/run-room-active.png`
- `docs/assets/gui-concepts/review-desk.png`

Key product decisions:

- Recommended **Calm Local Operations**.
- Kept **Friendly Assistant Control** copy for review and blocked states.
- Kept **Power User Cockpit** evidence/timeline ideas only in disciplined form.
- Required the four human mode labels listed above.
- Required technical details only in Advanced details.

### 2. Live ai-inference Research And Routing Fix

Investigated the live `ai-inference` environment and found the main runtime error:

- `8765` was not Agentic Harness.
- Utility Hub occupied `8765`.
- Agentic Harness needed a fallback-port strategy.

Changed behavior:

- GUI now falls back deterministically instead of failing or randomly binding.
- Tests cover fallback and explicit-port behavior.
- README documents the `ai-inference` port reality.

### 3. Readiness Gate / Agent Loop Integration

Incorporated the practical parts of local-agent-loop analysis into code:

- Readiness endpoint.
- Readiness embedded in health.
- Agent-loop stage exposed in task payloads.
- Start blocked when active work needs review.
- UI displays readiness and loop.
- Parser fixed for `accepted: false`.

This was not a cosmetic-only integration. It changes behavior so the GUI cannot casually enqueue new simple-UI tasks while current local-goal work needs review.

### 4. Verification And Correction Of Overclaims

Corrected earlier overclaims from an external/liaison-style report:

- The GUI was not on `8765`.
- A plain HTTP request to `/api/tasks/stream` does not test WebSocket support.
- The current lane was `needs_review`, so production-ready claims needed qualification.
- The GUI implementation is real and tested, but the active workflow is not clean for new work until review is resolved.

## Important Overclaims To Avoid

Do not say:

```text
Agentic Harness GUI is on 127.0.0.1:8765 on ai-inference.
```

Correct statement:

```text
Utility Hub owns 8765 on ai-inference. Agentic Harness GUI currently runs on 127.0.0.1:8769, or whatever fallback URL is printed at startup.
```

Do not say:

```text
WebSocket is not implemented because a normal HTTP GET did not upgrade.
```

Correct statement:

```text
/api/tasks/stream requires a WebSocket upgrade. Tests cover RFC 6455 upgrade and JSON frame delivery.
```

Do not say:

```text
The harness is fully production-ready and ready for new work right now.
```

Correct statement:

```text
The GUI/API implementation is tested and running, but the current live local-goal state is needs_review, so new simple-UI starts are blocked until that review is resolved.
```

Do not say:

```text
The GUI hides everything from the operator.
```

Correct statement:

```text
The main UI hides backend machinery by default, while Advanced details retain raw evidence for debugging and power-user review.
```

## Remaining Risks / Gaps

1. **Dirty worktree ownership**
   - Many files are untracked.
   - Commit staging must be explicit.
   - Do not accidentally stage `__pycache__` or backup files.

2. **Visual QA still needed**
   - API tests pass.
   - I did not perform final screenshot QA with Playwright after the readiness UI changes.
   - Check desktop and narrow viewport for text overflow and layout overlap.

3. **Current readiness is not clear**
   - Live lane is `needs_review`.
   - Review/accept/continue decisions mutate the local-goal state.
   - Do not accept or continue without the operator's explicit intent.

4. **Auth is minimal**
   - Token auth exists but is environment-variable based.
   - Good enough for localhost use.
   - Not enough for public exposure.

5. **Session history is in-memory**
   - Session export/import exists.
   - Server-side session history is not durable across restarts unless exported/imported.

6. **Bulk tasks exist but product policy should stay conservative**
   - The API supports `/api/tasks/bulk`.
   - Main UI should still emphasize one visible task decision at a time.
   - Bulk should not become a casual non-programmer default until queue semantics are fully designed.

7. **Agentic analysis memo needs careful treatment**
   - `/mnt/raid0/home-ai-inference/agentic_ai_analysis.md` is useful for concepts.
   - Do not treat every external claim in it as verified local truth.

## Recommended Next Steps For Another Codex

Recommended order:

1. **Do not mutate local-goal immediately.**
   - First inspect current state:
     ```bash
     ssh ai-inference 'cd /mnt/raid0/documentation && scripts/local-goal status --json | python3 -m json.tool'
     ```
   - Decide with the operator whether to review, continue, accept, or stop the active run.

2. **Confirm the GUI URL from tmux output or API.**
   ```bash
   ssh ai-inference 'tmux capture-pane -pt pop-os:harness-gui -S -80'
   curl -s http://127.0.0.1:8769/api/health | python3 -m json.tool
   ```

3. **Run focused tests.**
   ```bash
   ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m pytest tests/test_gui_api.py tests/test_local_goal_bridge.py -q'
   ```

4. **Run visual QA.**
   - Open `http://127.0.0.1:8769/` through the available browser lane or SSH tunnel.
   - Capture desktop and mobile/narrow screenshots.
   - Verify no overlap, overflow, or exposed backend terms in the main UI.

5. **Inspect untracked files before commit planning.**
   ```bash
   ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && find agentic_harness/gui -maxdepth 3 -type f -print | sort'
   ```

6. **Clean/stage carefully only after ownership is clear.**
   - Likely stage source, tests, docs, static files, and concept images.
   - Likely exclude `__pycache__` and backup files.
   - Do not use `git add .` by default.

7. **Decide whether to persist session history.**
   - If persistence matters, design a small local JSON session store.
   - If not, document that export/import is the persistence mechanism.

8. **Decide whether bulk operations are UI-visible.**
   - Keep bulk API if useful for power users/tests.
   - Avoid making bulk the first-run workflow.

## Suggested Verification Commands

Current repo status:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && git status --short && git branch --show-current && git log -1 --oneline'
```

Port state:

```bash
ssh ai-inference 'ss -ltnp | grep -E ":876[5-9]|:8770" || true'
```

GUI health:

```bash
ssh ai-inference 'python3 - <<"PY"
import json, urllib.request
for path in ["/api/health", "/api/readiness", "/api/modes", "/api/tasks/current"]:
    url = "http://127.0.0.1:8769" + path
    with urllib.request.urlopen(url, timeout=3) as r:
        print("---", path, "---")
        print(json.dumps(json.loads(r.read().decode()), indent=2)[:3000])
PY'
```

Focused tests:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m pytest tests/test_gui_api.py tests/test_local_goal_bridge.py -q'
```

Full tests:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m pytest -q'
```

Syntax checks:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m py_compile agentic_harness/gui/api.py agentic_harness/gui/server.py agentic_harness/core/local_goal_bridge.py agentic_harness/cli.py'
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && node --check agentic_harness/gui/static/app.js'
```

Launch command:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && python3 -m agentic_harness.cli gui --host 127.0.0.1 --no-open'
```

Optional token-protected launch:

```bash
ssh ai-inference 'cd /mnt/raid0/home-ai-inference/agentic-harness && AGENTIC_HARNESS_GUI_TOKEN=change-me python3 -m agentic_harness.cli gui --host 127.0.0.1 --no-open'
```

## Suggested Prompt For Another Codex

Use this if handing the work to another agent:

```text
Work in /mnt/raid0/home-ai-inference/agentic-harness on ai-inference. Read AGENTIC_HARNESS_GUI_CODEX_HANDOFF_2026-07-10.md first. Do not assume port 8765 is Agentic Harness; Utility Hub owns it. Confirm the printed GUI fallback URL and current /api/health. The current local-goal state may be needs_review; do not accept, continue, stop, or enqueue new work without checking status and confirming intent. Focus on visual QA, clean staging plan, and making the GUI implementation commit-ready without exposing planner/executor/Mode 3A details in the main UI. Run focused tests before and after changes.
```

## Decision Log

- **Selected visual direction:** Calm Local Operations.
- **Main UI language:** human modes only; hide backend route names.
- **Advanced details:** keep raw evidence available but collapsed.
- **Port behavior:** default to `8765`, deterministic fallback when occupied.
- **Current ai-inference URL:** `127.0.0.1:8769` at handoff time.
- **Readiness policy:** block simple starts during `needs_review` or blocked decision states.
- **No-babysitting policy:** safe work can move forward, but review gates remain real.
- **Experimental mode:** tiny canary only, not default broad work.
- **WebSocket:** implemented as status stream with RFC 6455 upgrade.
- **Session history:** in-memory with export/import.

## Do-Not-Do List

Do not:

- Use `http://127.0.0.1:8765/` as the harness URL on `ai-inference` without checking.
- Kill Utility Hub just to claim `8765` for the harness.
- Run `git reset --hard`, `git checkout --`, or broad cleanup commands.
- Stage everything with `git add .`.
- Commit `__pycache__` files or backup files accidentally.
- Expose planner/executor/worker/run-dir/log details in the main GUI.
- Put `Mode 3A` wording in the default UI.
- Start new simple-UI work while the readiness gate says `needs_review`.
- Accept/continue/stop the active local-goal run without operator intent.
- Claim production readiness while the live state is still `needs_review`.

## Files To Read First In This Repo

```text
README.md
codex-app-gui-design-result.md
gui-implementation-plan.md
agentic_harness/core/local_goal_bridge.py
agentic_harness/gui/api.py
agentic_harness/gui/server.py
agentic_harness/gui/static/index.html
agentic_harness/gui/static/app.js
agentic_harness/gui/static/styles.css
tests/test_gui_api.py
tests/test_local_goal_bridge.py
```

## Final Current-State Statement

The Agentic Harness GUI implementation is real, locally running, and covered by focused API/bridge tests. The product direction is clear: a calm non-programmer local workbench with four human modes and technical details hidden by default. The most important operational fact is that the live harness is not on `8765`; it is currently on `8769`, and the active local-goal lane is `needs_review`. Treat that review gate as a real state, not a cosmetic warning.
