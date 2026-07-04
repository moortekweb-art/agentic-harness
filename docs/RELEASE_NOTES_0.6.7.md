# Agentic Harness v0.6.7

This release hardens the demo and artifact-storage proof points.

## Highlights

- Added an end-to-end demo test that copies `examples/fix-failing-tests-demo`
  to a temp directory and verifies the full failing-test -> agent fix -> pytest
  pass cycle.
- Clarified that the first pytest command in the fix-failing-tests demo is
  expected to fail.
- Added coverage for `ArtifactStore(".agentic-harness").write_report(...)` with
  a relative root and fixed relative artifact recording for that path.
- Test coverage increased to 80 current-package tests.

## Deferred

- Cross-platform CI for `windows-latest` and `macos-latest` still requires a
  GitHub credential with `workflow` scope because updating
  `.github/workflows/ci.yml` is rejected by the current token.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
