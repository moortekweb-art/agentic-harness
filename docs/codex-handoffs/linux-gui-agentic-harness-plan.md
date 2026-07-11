# Linux GUI Agentic Harness Plan

## Goal

Build a Pop-OS friendly GUI for Agentic Harness that lets a non-programmer choose what kind of AI help they want, type the task in normal language, and monitor the work without seeing planner names, worker IDs, JSON, shell commands, or goal-packet syntax unless they open an advanced details view.

The product should feel like a local assistant control panel, not a developer dashboard.

## Product Thesis

The current harness is real but script-shaped. Its safety comes from queues, run directories, review gates, completion markers, ownership tracking, logs, and deterministic acceptance. Those are valuable, but they are not the human interface.

The GUI must become the human layer:

- The human chooses an understandable work mode.
- The human writes what they want in plain English.
- The app translates that into the correct harness route.
- The app shows simple progress and only asks for decisions when needed.
- Technical detail is available, but hidden by default.

The backend must also become upgradeable. This should not freeze the current
script-shaped harness forever. The program should make it easier to improve the
harness toward Codex CLI `/goal` behavior: less babysitting, better autonomous
problem solving, clearer continuation, stronger recovery, and more natural final
prompting.

Long-term target:

- The human gives one normal-language goal.
- The app chooses or recommends the right mode.
- The harness decomposes the goal, works through obstacles, verifies itself,
  continues when appropriate, and asks only when a real human decision is needed.
- The final user-facing result reads like Codex `/goal`: what changed, what was
  verified, what remains, and whether human action is required.

## The Four Human Modes

Mode 1: Local Steady Worker

- Human label: "Use this computer"
- Best for: normal bounded work where the local Pop-OS machine should do the job.
- Backend route: local-goal quick start / local OpenCode lane.
- Human promise: "Good for small and medium tasks on this machine."
- Hidden technical detail: executor, vLLM, local Node1 lane, review markers.

Mode 2: GLM-Guided Local Worker

- Human label: "Let GLM guide the plan"
- Best for: harder local tasks where GLM should help structure the approach while local tools do the work.
- Backend route: premium planner local builder with `glm-5.2`.
- Human promise: "Good when the task is fuzzy or needs judgment."
- Hidden technical detail: planner-assisted start, local builder, deterministic acceptance.

Mode 3: Cloud GLM Long Task

- Human label: "Let GLM carry a long task"
- Best for: Codex `/goal`-like work that should run through the cloud GLM worker lane.
- Backend route: Mode 3A / `opencode-glm-build`.
- Human promise: "Good for longer tasks you want to hand off."
- Hidden technical detail: Mode 3A, worker registry, queue ID, cloud-loop, result JSON.

Mode 4: Experimental GLM Canary

- Human label: "Try experimental GLM"
- Best for: tiny, safe experiments that test direct GLM implementation.
- Backend route: direct GLM canary worker only.
- Human promise: "For small tests only, not important broad work."
- Hidden technical detail: `glm52-direct-implementation-canary`, canary boundary, adapter status.

Default recommendation:

- For most users: Mode 2 if the work is local and important.
- For `/goal`-style long work: Mode 3.
- Mode 4 should be visibly marked experimental.

## Main UI Flow

First screen:

- App title: Agentic Harness
- Short prompt: "What do you want help with?"
- Four mode cards with plain labels and one-sentence descriptions.
- Recommended badge on Mode 2 or Mode 3 depending on the task size selector.
- Large text area for the task.
- Optional "Safe areas" field hidden under "Add boundaries".
- Start button.

After Start:

- App shows a work ticket in human language, not raw ID first.
- Status states:
  - Starting
  - Working
  - Checking work
  - Needs review
  - Done
  - Blocked
  - Stopped
- Timeline shows plain events:
  - "Task accepted by harness"
  - "Worker started"
  - "File changes detected"
  - "Verification running"
  - "Review passed"
  - "Ready for you"

Review screen:

- Summary first.
- Files changed.
- Verification passed/failed.
- Clear actions:
  - Accept
  - Ask it to continue
  - Stop
  - Open details

Advanced details drawer:

- Queue ID
- Worker
- Planner
- Run directory
- Raw logs
- Raw JSON
- Exact command used

## Design Direction

The app should look like a calm local operations tool, not a SaaS landing page and not a terminal wrapper.

Visual rules:

- Dense but readable.
- Four clear mode cards.
- No decorative hero page.
- No fake "AI magic" styling.
- Status should be obvious at a glance.
- Logs are secondary.
- Avoid exposing implementation words in the default UI:
  - Hide: planner, executor, worker, JSON, tmux, queue, vLLM, Mode 3A.
  - Show: local work, guided work, long task, experiment, working, done, needs review.

Design loop:

1. Generate 2-3 UI mockups or visual directions.
2. Pick one direction.
3. Build local web GUI.
4. Take Playwright screenshots.
5. Fix layout, text, spacing, state clarity, and mobile/narrow windows.
6. Repeat until it feels easy.

Codex/image tooling role:

- Use image generation for mood boards, layout inspiration, icons, empty-state art, or first-pass screen concepts.
- Do not treat generated images as implementation.
- Build the final UI in code and verify with screenshots.

## Architecture

Recommended first implementation: local web app.

Why:

- Best speed-to-quality on Pop-OS.
- Easy to run at `http://localhost:8765`.
- Browser screenshots make visual QA practical.
- Can later be wrapped into a desktop launcher or Tauri app.
- Backend can reuse the existing Python package and local-goal bridge.

Components:

- `agentic_harness/gui/server.py`
  - Small local HTTP server.
  - Serves frontend assets.
  - Exposes simple JSON API.

- `agentic_harness/gui/api.py`
  - `GET /api/health`
  - `GET /api/modes`
  - `POST /api/tasks`
  - `GET /api/tasks/current`
  - `POST /api/tasks/current/watch`
  - `POST /api/tasks/current/accept`
  - `POST /api/tasks/current/continue`
  - `POST /api/tasks/current/stop`
  - `GET /api/tasks/current/details`

- `agentic_harness/core/local_goal_bridge.py`
  - Wraps the existing local-goal backend.
  - Translates human mode selection into backend route.
  - Normalizes output into simple states.

- Frontend
  - Local static app, likely React/Vite for the real version.
  - Simpler fallback is server-rendered HTML plus small JavaScript, but React is better for live status and state panels.

State model:

- `Task`
  - id
  - human_title
  - mode
  - status
  - summary
  - needs_human
  - changed_files
  - verification
  - created_at
  - updated_at
  - advanced_details

The GUI should not depend on raw harness JSON shape directly. Backend normalizes it.

Upgrade architecture:

- Keep a stable app API between the GUI and harness backend.
- Treat the existing `local-goal` scripts as backend adapter v1, not the final
  internal architecture.
- Add versioned contracts for:
  - mode definitions,
  - task status,
  - review state,
  - continuation decisions,
  - blocked reasons,
  - final result summaries.
- Store run evidence in a stable schema so future harness versions can read old
  runs.
- Add migration hooks for config/state changes.
- Keep implementation details replaceable: today scripts/local-goal, tomorrow a
  proper daemon/service/state machine.

Codex `/goal` parity capabilities to grow toward:

- Goal decomposition into subgoals.
- Self-directed continuation when verification fails.
- Honest blocker detection instead of silent stalls.
- Recovery after process exits, machine restart, or worker failure.
- Context summarization across long work windows.
- Worker selection based on task type.
- Clear final answer generation from evidence.
- Review/acceptance that does not require operator babysitting for routine
  success cases.

## Safety Requirements

Default guardrails:

- No secrets.
- No provider dashboards.
- No destructive cleanup.
- No DNS, billing, routing, firewall, or service changes unless explicitly approved.
- No broad refactors unless the task clearly asks.
- Preserve unrelated dirty work.
- Ask before public/external side effects.

Human confirmations:

- Required before service restarts.
- Required before destructive filesystem or git cleanup.
- Required before external/public changes.
- Required before switching model routes.
- Required before touching credentials or account settings.

UI safety language:

- Use plain prompts:
  - "This may restart a service. Allow that?"
  - "This wants to edit files outside the safe area. Continue?"
  - "This task needs a credential. Stop or provide it manually?"

Never show scary raw stack traces as the primary error. Show:

- What happened.
- What the user can do.
- "Open details" for technical output.

## MVP Scope

MVP must include:

- Local web GUI command: `agentic-harness gui`
- Mode picker with four modes.
- Plain task text box.
- Start task.
- Current task status.
- Watch/move-forward button.
- Done/blocked/needs-review display.
- Advanced details drawer.
- Basic API tests.
- Playwright screenshot verification.
- README instructions for Pop-OS.

MVP should not include:

- Multi-user accounts.
- Remote access.
- Cloud dashboard.
- Mobile app.
- Full theming system.
- Notifications beyond local page state.
- Editing every backend harness behavior.

## Implementation Plan

1. Freeze the mode contract
   - Define the four human modes, labels, descriptions, default route, and safety copy.
   - Verify with unit tests that each mode maps to the correct backend command.

2. Define upgradeable backend contracts
   - Add versioned Python types or JSON schemas for mode, task, status, review,
     continuation, blocker, and final-result records.
   - Verify old local-goal output can be normalized into these contracts.

3. Add normalized task status
   - Create a backend adapter that converts local-goal status/queue/review output into `TaskStatus`.
   - Verify using saved sample outputs and live smoke calls.

4. Build local API server
   - Add `agentic-harness gui --port 8765`.
   - Serve `GET /api/modes`, `POST /api/tasks`, `GET /api/tasks/current`, and `POST /api/tasks/current/watch`.
   - Verify with `curl` and pytest.

5. Build first UI
   - Implement mode cards, task box, start button, current task status, and details drawer.
   - Verify manually and with Playwright screenshot.

6. Add review actions
   - Add Accept, Continue, Stop buttons when the backend reports those actions are safe.
   - Verify with a harmless smoke task.

7. Add no-babysitting behavior
   - Add a continuation policy that can decide: continue, review, accept, stop,
     or ask human.
   - Show only human-needed decisions in the UI.
   - Verify with cases for success, failed verification, blocked task, and stale
     worker.

8. Improve user language
   - Replace all programmer terms in default UI.
   - Keep raw terms only in Advanced.
   - Verify by screenshot review and text scan.

9. Package for Pop-OS
   - `pipx install` path works.
   - Optional `.desktop` launcher opens the GUI.
   - Verify on Pop-OS with browser launch.

10. Prepare GitHub publication
   - Keep changes in the `agentic-harness` repo.
   - Update README screenshots/instructions.
   - Add a release note describing the GUI and four human modes.
   - Run tests, lint, type checks, and package smoke checks.
   - Commit with a clear message.
   - Push to GitHub after final verification.

11. Visual iteration
   - Generate or sketch 2-3 visual directions.
   - Pick one.
   - Adjust spacing, color, typography, responsive layout.
   - Verify with Playwright screenshots.

12. End-to-end proof
   - Start a tiny Mode 3 task from GUI.
   - Watch it complete.
   - Confirm the GUI shows Done without exposing raw JSON.
   - Confirm Advanced contains the raw evidence.

## Verification Plan

Backend tests:

- Mode mapping unit tests.
- Goal packaging tests.
- Status normalization tests.
- API contract tests.
- Upgrade-contract compatibility tests.
- Continuation-policy tests for no-babysitting behavior.

Frontend tests:

- Page loads.
- Mode cards render.
- Start button disabled until task text exists.
- Start request sends selected mode and objective.
- Status updates render correctly.
- Advanced drawer hides/shows technical detail.

Visual checks:

- Desktop screenshot.
- Narrow window screenshot.
- Long task text does not overflow.
- Status labels do not overlap controls.

Live smoke:

- Run `agentic-harness gui`.
- Start a harmless documentation smoke task.
- Run one watch cycle.
- Confirm Done/Blocked/Needs Review is shown accurately.

## Open Decisions

- Frontend stack:
  - Recommended: React + Vite.
  - Simpler fallback: static HTML/CSS/JS served by Python.

- Desktop wrapper:
  - Recommended later: Tauri or `.desktop` launcher opening local browser.
  - Not required for MVP.

- Image generation:
  - Recommended before final UI implementation.
  - Generate visual concepts for mode picker and task status screen.

- Default mode:
  - Recommended: Mode 3 for `/goal`-like long tasks.
  - Recommended: Mode 2 for local important tasks.
  - UI can ask "Where should the work happen?" to guide choice.

## Done When

- A Pop-OS user can start the GUI without reading harness docs.
- The user can choose a mode without understanding planner/executor terms.
- The user can type a normal request and start work.
- The app can show useful progress and final state.
- Technical details remain available but are hidden by default.
- At least one live smoke task completes through the GUI.
- Routine success and routine verification-retry paths do not require operator
  babysitting.
- The backend contracts make future harness upgrades possible without rewriting
  the GUI.
- Screenshots show the UI is clear, readable, and not a programmer dashboard.
- The finished work is committed and pushed to GitHub after verification.
