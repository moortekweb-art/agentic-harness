from __future__ import annotations

from pathlib import Path


def test_ci_runs_package_build_and_compile_smoke() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m compileall agentic_harness" in workflow
    assert "python -m pip install build" in workflow
    assert "python -m build" in workflow
    assert "dist/*.whl" in workflow
