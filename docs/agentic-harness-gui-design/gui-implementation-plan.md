# Agentic Harness GUI Implementation Plan

## Goal

Turn the Agentic Harness Linux GUI design into a working Pop-OS local app while preserving the bigger target: a human-friendly, upgradeable path toward Codex CLI `/goal`-style long-horizon work with minimal babysitting.

This plan assumes the Codex app design pass produced:

- `codex-app-gui-design-result.md`
- `docs/assets/gui-concepts/*`

Those artifacts currently exist on the Mac mini clone at:

```text
/Users/MikeMacMini/agentic-harness/codex-app-gui-design-result.md
/Users/MikeMacMini/agentic-harness/docs/assets/gui-concepts
```

They must be copied or synced into this Pop-OS repo before implementation choices are finalized:

```text
/mnt/raid0/home-ai-inference/agentic-harness/codex-app-gui-design-result.md
/mnt/raid0/home-ai-inference/agentic-harness/docs/assets/gui-concepts
```

## Tasks

- [ ] Import Mac design artifacts into the Pop-OS repo.
  Verify: `test -f codex-app-gui-design-result.md` and `find docs/assets/gui-concepts -type f`.

- [ ] Read the design result and pick the final visual direction.
  Verify: chosen direction, mode-card copy, status language, and screen list are recorded in this plan or a follow-up implementation spec.

- [ ] Define stable backend contracts before UI code.
  Verify: Python types or schemas exist for `HumanMode`, `TaskStatus`, `TaskSummary`, `ReviewState`, `BlockedReason`, and `AdvancedDetails`.

- [ ] Normalize the existing local-goal backend behind a clean adapter.
  Verify: tests cover the four modes mapping to local, GLM-guided local, cloud GLM, and experimental GLM canary routes.

- [ ] Add a local GUI server command.
  Verify: `agentic-harness gui --port 8765` starts a local-only server and `GET /api/health` returns OK.

- [ ] Add API endpoints for the GUI.
  Verify: tests or curl checks pass for modes, start task, current task, watch, accept, continue, stop, and details.

- [ ] Build the first GUI screen.
  Verify: browser shows four human mode cards, plain-English task box, Start button, and no programmer terms in the default view.

- [ ] Build active-work and review screens.
  Verify: simulated statuses render Starting, Working, Checking work, Needs review, Done, Blocked, and Stopped.

- [ ] Add Advanced details drawer.
  Verify: raw queue ID, worker, logs, JSON, run directory, and command details are hidden by default and visible only when opened.

- [ ] Add no-babysitting policy surface.
  Verify: routine success can proceed to Done/Needs review without asking; real risky cases show a clear human decision prompt.

- [ ] Run visual QA.
  Verify: Playwright screenshots exist for desktop and narrow windows, with no text overflow or incoherent overlap.

- [ ] Run one live smoke from the GUI.
  Verify: a harmless Mode 3 cloud GLM task starts from the GUI, progresses, and reaches Done/Needs review/Blocked accurately.

- [ ] Prepare GitHub publication.
  Verify: README updated, screenshots included, tests/lint/type checks pass, changes committed, and pushed to GitHub.

## Done When

- A Pop-OS user can run `agentic-harness gui` and use the app without knowing CLI syntax.
- The app presents all four modes in human language.
- The user can type a normal request and start work.
- The UI hides technical details by default.
- The backend remains upgradeable beyond the current script harness.
- The no-babysitting goal is represented in contracts and behavior, not only copy.
- A real smoke task has been verified through the GUI.
- The finished work is committed and pushed to GitHub.

## Current Blocker

The Codex app result file and generated concept images are on the Mac mini clone, not this Pop-OS workspace yet. Import those files before implementation begins.
