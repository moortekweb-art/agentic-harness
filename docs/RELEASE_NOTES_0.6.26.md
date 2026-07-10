# Agentic Harness v0.6.26

This release prepares the packaged local browser GUI for public GitHub use.

## Changes

- `agentic-harness gui` now binds to `127.0.0.1` by default and asks the OS for
  a free local port when `--port` is omitted.
- Explicit `--port N` remains available for scripts and operators that need a
  stable URL.
- The selected GUI URL is printed only after the server has successfully bound.
- Browser launch failures no longer stop the server; the CLI prints the URL and
  `--no-open` guidance instead.
- Non-loopback GUI binding prints a warning with token guidance and notes that
  bearer tokens are not a complete browser-origin or CSRF model.
- The optional local-goal/Mode 3A backend now uses portable lookup defaults:
  explicit `--doc-root`, then non-empty `AGENTIC_HARNESS_DOC_ROOT`, then the
  current directory. `AGENTIC_HARNESS_LOCAL_GOAL` remains the executable
  override, and `~` is expanded in configured paths.
- Missing local-goal backends are reported as optional configuration gaps; the
  Python package does not install that backend.
- README and release checklist now document Linux/Ubuntu GUI usage, the
  Python-backend plus packaged HTML/CSS/JS architecture, package-data shipping,
  and the GUI security model.
- GUI static files remain included in the wheel/sdist through package data.

## Verification

- `python -m pytest tests/test_gui_api.py tests/test_local_goal_bridge.py -q`
  passes.
- Run the full release gate from `docs/RELEASE_CHECKLIST.md` before tagging or
  publishing.
