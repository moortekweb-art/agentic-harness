# Codex App GUI Design Result

Date: 2026-07-09
Repo: `/Users/MikeMacMini/agentic-harness`
Source goal: `codex-app-gui-design-goal.md`
Source workflow: `codex-app-design-workflow-plan.md`
Source plan: `linux-gui-agentic-harness-plan.md`

## Executive Summary

Agentic Harness should become a calm local workbench for starting, watching, and accepting AI-assisted work without requiring the user to understand the current script-shaped backend. The main UI should never feel like a terminal dashboard. It should answer five questions quickly:

- What can I ask it to do?
- Which kind of help should I use?
- Is it working?
- Does it need me?
- What finished, passed, or remains blocked?

Recommended direction: **Calm Local Operations**. It should feel like a practical local control panel: quiet, readable, and trustworthy. Keep the warm guidance from **Friendly Assistant Control** and the compact evidence structure from **Power User Cockpit**, but do not let either direction dominate the main UI.

The future GUI should be a local web app first, opened by `agentic-harness gui` on Pop-OS/Linux. It should use a stable JSON API that normalizes the current local-goal/Mode 3A mechanics into human states, preserving technical evidence in an Advanced details drawer.

## Concept References

These are concept references, not implementation assets. The files remain named after the screen they emphasize, but they map to the three required visual directions below. Generated UI text may be imperfect; the exact product copy in this document should be treated as authoritative.

- [Calm Local Operations reference](docs/assets/gui-concepts/workbench-start.png): mode picker, task box, and local status preview.
- [Friendly Assistant Control reference](docs/assets/gui-concepts/review-desk.png): guided review, reassuring labels, and plain-language decision support.
- [Power User Cockpit reference](docs/assets/gui-concepts/run-room-active.png): denser timeline, live status, and advanced evidence placement.

## Visual Directions

### 1. Calm Local Operations

Feels like a local control surface: quiet, direct, and task-first. The user sees the four human modes, a large request field, optional boundaries, and a compact preview of what will happen after Start.

Why it fits: Agentic Harness is local-first and review-oriented. A workbench metaphor supports practical action without making the app look like a cloud product or a programmer console.

Strengths:

- Best first-run experience for non-programmers.
- Keeps mode selection, task text, and safety scope visible together.
- Easy to implement with simple responsive layout.
- Makes the default recommendation feel helpful rather than pushy.
- Works for all required screens without inventing a heavy navigation model.

Risks:

- If the cards get too wordy, the screen can become instruction-heavy.
- Needs careful spacing so four cards do not feel cramped on narrower windows.

Recommendation: **Use this as the selected visual direction.** Start Work should use it directly. Active Work, Needs Review, and Done / Blocked should keep the same calm visual system rather than becoming separate product modes.

### 2. Friendly Assistant Control

Feels warmer and more guided. It emphasizes helpful explanations, clear next choices, and reassuring review language for people who do not know what the harness is doing underneath.

Why it fits: The target user should be able to type a plain request and make a decision without knowing backend vocabulary. This direction reduces anxiety in Needs Review, Blocked, and quiet-worker states.

Strengths:

- Best for review, blocked, and error recovery states.
- Makes human decisions feel concrete: Accept, Ask it to continue, Stop.
- Helps explain experimental mode without scaring the user.
- Encourages plain-language summaries instead of file-path-first output.

Risks:

- Can become too soft if it hides real uncertainty.
- Can feel vague if every panel is written like coaching text.

What to keep: Keep its reassuring microcopy, especially in Needs Review, Blocked, and empty states. Do not use a chat-style interface as the main frame.

### 3. Power User Cockpit

Feels denser and more operational. It emphasizes current state, timeline, elapsed time, evidence, and compact controls for people who already trust the harness and want fast scanning.

Why it fits: Agentic Harness has real operational machinery underneath. Active Work needs enough structure to reassure the user during long or quiet runs without exposing the raw machinery by default.

Strengths:

- Best for Active Work and power-user scanning.
- Gives the timeline, status band, and Advanced drawer a natural home.
- Makes long-running work feel observable without becoming a terminal view.
- Supports future History and Settings areas if the GUI grows.

Risks:

- Can drift into a developer dashboard.
- Sidebar navigation and dense metadata are unnecessary for MVP unless there is real content behind them.

What to keep: Keep the compact timeline, evidence grouping, and bottom Advanced details placement. Avoid raw logs, shell commands, run directories, queue mechanics, and Mode 3A wording in the default surface.

## Recommended Visual Direction

Use **Calm Local Operations** as the one selected direction.

Keep these pieces from the other directions:

- From **Friendly Assistant Control:** concise human reassurance in quiet, blocked, and review states.
- From **Power User Cockpit:** the three-step progress trail, compact timeline, and disciplined Advanced details drawer.

Visual rules:

- Canvas: warm off-white or very light neutral gray.
- Text: charcoal, high contrast, no negative letter spacing.
- Accent: muted teal for progress and primary action.
- Success: deep green, used sparingly.
- Attention: amber for "Needs you" and experimental mode.
- Stop/destructive action: restrained red outline or secondary destructive button.
- Cards: 8px radius or less, subtle border, no nested cards.
- Typography: compact and readable, not hero-scale.
- Layout: desktop-first at 1280px wide, usable down to narrow browser windows.

Do not use a giant hero, animated marketing background, terminal black panels, decorative gradient blobs, or raw log surfaces in the main view.

## Information Architecture

Primary areas for MVP:

- **Start Work:** choose mode, write task, add boundaries, start.
- **Current Work:** status, timeline, plain summary, check/move button.
- **Review:** human decision when needed.
- **History:** later. Not required for MVP, but the layout should leave room.
- **Settings:** later. Not required for MVP.

MVP can be a single-page app with state-based views instead of full navigation. If navigation appears in MVP, keep it minimal: Start, Current, History, Settings.

## Start Work Screen

Purpose: let the user start useful work without learning harness vocabulary.

Layout:

- Top bar: app name, local health indicator, optional current task indicator.
- Main heading: "What do you want done?"
- Four mode cards in a 2x2 or 4-column layout depending on width.
- Large text area with placeholder: "Describe the outcome you want..."
- Collapsed optional section: "Add boundaries".
- Start button disabled until task text exists.
- Small preview panel: selected mode, safe areas, checks, next step.

Default behavior:

- The task box should appear before or beside mode choice, not after a wizard step.
- The app may recommend a mode after the user types, but mode selection remains visible.
- If the task mentions long-running work, recommend "Let GLM carry a long task".
- Otherwise default recommendation is "Let GLM guide the plan".

Main copy:

- Heading: "What do you want done?"
- Text box label: "Task"
- Text box helper: "Use normal language. The app will turn it into a safe work ticket."
- Boundaries toggle: "Add boundaries"
- Boundaries helper: "Limit where it can work or name checks you expect."
- Start button: "Start"

## Mode-Card Text

These four titles are the final user-facing labels for the main UI. Backend route names belong only in Advanced details.

| Mode | Description | Helper text | Badge | Warning / boundary text |
| --- | --- | --- | --- | --- |
| Use this computer | Good for small and medium tasks on this machine. | Best when the work is local, bounded, and easy to check. | None | You stay in control. |
| Let GLM guide the plan | Good when the task is fuzzy, important, or needs judgment. | Best when you want stronger planning while this machine does the work. | Recommended for local work | The app may take a little longer to shape the work before starting. |
| Let GLM carry a long task | Good for longer work you want to hand off. | Best when the task may need several passes before it is ready. | Recommended for long tasks | Use this when you are comfortable letting the task run longer. |
| Try experimental GLM | For tiny safe experiments only. | Best when you are testing the newest path, not doing important broad work. | Experimental | Use only for small tests. |

Recommended card treatment:

- Selected card: teal border, light teal background, visible check mark.
- Recommended card: small badge, never a popover or modal.
- Experimental card: amber badge, plain warning line, visually secondary.
- Disabled state: only if the backend reports the route unavailable; show "Unavailable right now" with a short reason.

## Active Work Screen

Purpose: reassure the user, show progress, and provide one safe manual nudge.

Layout:

- Top status band with current state: Starting, Working, or Checking work.
- Three-step progress trail:
  - Starting
  - Working
  - Checking work
- Current summary panel:
  - "What it is doing"
  - "Latest outcome"
  - "Time elapsed"
  - "Next"
- Timeline:
  - "Task accepted"
  - "Work started"
  - "Changes found"
  - "Checking work"
  - "Ready for review"
- Actions:
  - "Check now" when a manual status refresh is useful.
  - "Move forward" when the backend supports one safe watch/monitor cycle.
  - "Stop" as a secondary destructive action.
- Advanced details drawer collapsed by default.

Quiet-backend handling:

- Show "Still working" rather than implying a stall.
- Include last observed time: "Last update: 4 minutes ago."
- If no new event appears after a threshold, show: "No new update yet. You can check now or keep waiting."
- Do not surface raw logs as the primary experience.

## Needs Review Screen

Purpose: turn harness evidence into a clear human decision.

Layout:

- State label: "Needs review"
- Short summary: one or two sentences.
- "What changed" section.
- "What passed" section.
- "Files changed" section with plain descriptions.
- Decision bar fixed near the bottom on desktop:
  - Accept
  - Ask it to continue
  - Stop
- Advanced details drawer below evidence.

Button behavior:

- **Accept:** available only when verification passed or when the backend explicitly allows human acceptance.
- **Ask it to continue:** creates a continuation request using plain instructions, with optional text field: "What should it improve?"
- **Stop:** asks for confirmation if stopping may leave partial work.

Approval language:

- "This is ready for your review."
- "Accept this result?"
- "Ask it to continue with a note."
- "Stop this work."

Avoid words like approve/reject if they sound too formal for the main flow. "Accept" and "Ask it to continue" are clearer.

## Done / Blocked Screen

Purpose: give a final answer, not a pile of artifacts.

Done layout:

- State label: "Done"
- Final summary: what changed and why it matters.
- "What passed" verification list.
- "What changed" with plain file or area descriptions.
- "What remains" if any.
- Primary next action:
  - "Start another task"
  - "View details"
  - "Open changed files" later, if supported.

Blocked layout:

- State label: "Blocked"
- Plain explanation:
  - "What happened"
  - "What you can do"
  - "What was already checked"
- Action choices:
  - "Try again"
  - "Change boundaries"
  - "Stop"
  - "Advanced details"

Blocked state should not look like a crash. Use amber or neutral attention styling unless data loss or destructive risk is involved.

## Status Language

Use these states in the main UI:

| State | Main label | Plain explanation | Primary action |
| --- | --- | --- | --- |
| idle | Ready | "Choose how you want help, then describe the task." | Start |
| starting | Starting | "The app is preparing the work ticket." | Check now |
| working | Working | "The task is underway." | Check now |
| checking | Checking work | "The app is verifying the result." | Check now |
| needs_review | Needs review | "The work is ready for your decision." | Accept |
| done | Done | "The task finished and checks passed." | Start another task |
| blocked | Blocked | "The task needs a change or human decision." | Review options |
| stopped | Stopped | "The work was stopped before completion." | Start another task |

Secondary labels:

- "Needs you"
- "What changed"
- "What passed"
- "What remains"
- "Last update"
- "Advanced details"

## Empty, Loading, Quiet, Blocked, Error, And Success States

### Empty

Use on first launch and when no current task exists.

- Main label: "Ready"
- Body: "Choose how you want help, then describe the task."
- Primary action: Start, disabled until task text exists.
- Secondary content: show the four mode cards and optional boundaries. Do not show history as an empty table in MVP.

### Loading

Use while the app is reading health, modes, or current work.

- Main label: "Checking local status"
- Body: "Looking for current work on this computer."
- Treatment: small inline spinner or skeleton rows. Avoid full-page loading if cached mode labels are available.
- Timeout copy: "This is taking longer than expected. You can try again or open Advanced details."

### Quiet Worker

Use when the backend is still running but has not produced a new event.

- Main label: "Still working"
- Body: "No new update yet. Last update: {relative time}."
- Primary action: "Check now"
- Secondary action: "Stop"
- Do not imply failure until the backend reports a blocker or the health check fails.

### Blocked

Use when progress requires a changed instruction, permission, unavailable dependency, or a human decision.

- Main label: "Blocked"
- Body structure: "What happened", "What was already checked", "What you can do".
- Primary action: "Review options"
- Other actions: "Try again", "Change boundaries", "Stop"
- Treatment: amber or neutral attention styling. Do not use a crash screen unless the app itself failed.

### Error

Use when the GUI or backend bridge fails unexpectedly.

- Main label: "Something went wrong"
- Body: "The app could not update this work item."
- Primary action: "Try again"
- Secondary action: "Open Advanced details"
- Technical output: keep stack traces, raw logs, and command output inside Advanced details only.

### Success

Use after acceptance or when a task completes without needing review.

- Main label: "Done"
- Body: short final answer with what changed and why it matters.
- Evidence: "What passed", "What changed", "What remains".
- Primary action: "Start another task"
- Secondary action: "View details"

## Review And Acceptance Flow

1. Work reaches a reviewable state.
2. The app shows the Needs Review screen.
3. The user reads summary, verification, and changed areas.
4. The user chooses:
   - Accept: mark as accepted/done.
   - Ask it to continue: send a continuation note or retry instruction.
   - Stop: halt work and preserve evidence.
5. If Accept succeeds, show Done with final evidence.
6. If continuation starts, return to Active Work with the new instruction visible in plain language.
7. If stop succeeds, show Stopped with what was preserved.

Routine success should require one decision at most. If the backend can safely continue after a failed check, the GUI should show "Checking again" rather than asking the user to babysit every retry.

## Advanced Details Drawer

Hidden by default. The drawer may include technical words that the main UI avoids.

Contents:

- Task ID or queue ID.
- Selected route.
- Planner.
- Executor or worker.
- Run directory.
- Raw status JSON.
- Raw logs.
- Exact command used.
- Verification command output.
- Changed file paths.
- Report and evidence paths.
- Timing and retry counters.
- Backend version and API contract version.

Rules:

- Drawer label: "Advanced details".
- Show a short warning when raw output may contain sensitive local paths.
- Keep copy/select buttons available for diagnostics.
- Never put raw JSON or logs above the human summary.

## Accessibility Notes

- Use semantic headings and buttons.
- Ensure all controls are keyboard reachable.
- Keep focus outlines visible.
- Do not rely on color alone for status; pair color with text and icons.
- Minimum contrast should meet WCAG AA.
- Buttons should have stable width and height so labels do not shift layout.
- The four mode cards should be selectable by keyboard and screen reader.
- The selected card should expose `aria-checked` or equivalent state.
- Progress trail should also be readable as text.
- Error and blocked states should announce changes politely, not aggressively.
- Long task text and long file paths must wrap without overflowing.
- Respect `prefers-reduced-motion`.

## Implementation Notes For Future Local Web GUI

Recommended command:

```bash
agentic-harness gui --port 8765
```

Recommended frontend:

- React + Vite for the real GUI because active status updates, review actions, drawers, and state transitions benefit from component structure.
- A static HTML/CSS/JS version is acceptable only for an early smoke prototype.

Recommended backend modules:

- `agentic_harness/gui/server.py`
- `agentic_harness/gui/api.py`
- `agentic_harness/core/local_goal_bridge.py`

Minimum API:

- `GET /api/health`
- `GET /api/modes`
- `POST /api/tasks`
- `GET /api/tasks/current`
- `POST /api/tasks/current/watch`
- `POST /api/tasks/current/accept`
- `POST /api/tasks/current/continue`
- `POST /api/tasks/current/stop`
- `GET /api/tasks/current/details`

Stable task shape:

```json
{
  "id": "human-readable-ticket",
  "human_title": "Short task title",
  "mode": "guided",
  "status": "working",
  "summary": "The task is underway.",
  "needs_human": false,
  "changed_files": [],
  "verification": [],
  "created_at": "2026-07-09T00:00:00Z",
  "updated_at": "2026-07-09T00:00:00Z",
  "advanced_details": {}
}
```

Mode mapping should be versioned and tested. The GUI should consume labels and descriptions from the backend, but the backend must preserve the human labels from this document.

For the existing bridge:

- `local` maps to "Use this computer".
- `guided` maps to "Let GLM guide the plan".
- `cloud` maps to "Let GLM carry a long task".
- `experimental` maps to "Try experimental GLM".

Testing expectations:

- Unit tests for mode contract and route mapping.
- API tests for modes, start, current task, watch, accept, continue, stop, and details.
- Text scan that forbidden technical terms do not appear in the default rendered UI.
- Playwright screenshots for desktop and narrow windows.
- Long text overflow checks.
- Live smoke after implementation: start one harmless task, run one watch cycle, confirm Done, Blocked, or Needs review displays accurately.

## UX Decisions Resolved

1. **Should the first screen recommend a mode automatically?**
   Yes. Recommend after task text is entered, while still letting the user choose. Default recommendation is guided local work; long-horizon language should recommend the long-task mode.

2. **Should mode selection happen before or after the user types the task?**
   Show both on the same screen. Let users type first or choose first. Do not use a step-by-step wizard for MVP.

3. **How should Mode 4 explain experimental status?**
   Use an amber "Experimental" badge and the line "For small tests only." Keep it selectable but visually secondary.

4. **What should the app show while the backend is working but quiet?**
   Show "Still working", last update time, and the last known plain-English summary. Offer "Check now" without implying failure.

5. **How should the app ask for human approval?**
   Show a Needs Review screen with summary, checks, changed areas, and three actions: Accept, Ask it to continue, Stop.

6. **What should a blocked state look like?**
   Calm attention state, not an error dump. It should explain what happened, what was checked, and what choices the user has.

7. **What details belong in Advanced?**
   Raw IDs, route details, technical actors, exact commands, paths, logs, JSON, verification output, and report locations.

8. **What should the final Done answer look like?**
   A short final summary, what passed, what changed, what remains, and a clear next action.

## Open Questions And Risks

- The existing CLI human-mode titles are still more technical than the desired card labels. The GUI should use the human labels in this document and keep backend names in Advanced.
- The current local-goal backend may not expose enough normalized status for a rich timeline. MVP should tolerate sparse events and avoid inventing progress.
- Concept references show side navigation in some places, but MVP may not need it. Avoid adding navigation complexity until history/settings exist.
- The future API must redact sensitive output before showing it in the browser.
- The GUI should not auto-accept broad or risky changes just because a check passes.
- Mode recommendation needs simple heuristics first; model-based recommendation can wait.
- If the backend can run only one long job safely, the GUI should enforce that with a clear "One task is already running" state.

## Done Criteria Coverage

- This result includes three visual directions and recommends Calm Local Operations.
- It describes Start Work, Active Work, Needs Review, and Done / Blocked.
- It provides exact mode-card text, status language, review flow, and Advanced details contents.
- It keeps the main UI non-programmer and places technical details behind Advanced details.
- It includes implementation notes sufficient for a future local web GUI.
- It includes generated mockup references stored in the repo.
