# Agentic Harness v0.6.27

This patch release removes the final backend-control wording from the GUI's
ready state and records the now-working PyPI trusted-publishing path.

## Changes

- Ready-state summaries containing local-goal or controller-specific language are
  translated to "The assistant is ready for a new task." on the main surface.
- The original backend response remains available in Advanced details for
  diagnosis and audit evidence.
- Regression coverage now includes the real idle response that previously
  exposed backend, controller, operator, and provider-specific terminology.
- PyPI documentation now records the successful v0.6.26 trusted-publishing run
  and the wheel/sdist-only staging fix used by the publish workflow.
- Desktop and mobile captures are refreshed from the corrected release
  candidate.

## Verification

- Full Python, Ruff, mypy, compile, package, and release-smoke gates pass.
- Browser checks cover desktop and mobile layouts, all four modes, horizontal
  overflow, console/page errors, and backend-language leakage.
