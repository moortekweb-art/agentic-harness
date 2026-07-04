from __future__ import annotations

from pathlib import Path

import yaml


def test_ci_runs_package_build_and_compile_smoke() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m compileall agentic_harness" in workflow
    assert "python -m pip install build" in workflow
    assert "python -m build" in workflow
    assert "dist/*.whl" in workflow


def test_ci_runs_lint_and_typecheck() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m ruff check" in workflow
    assert "python -m mypy agentic_harness" in workflow


def test_publish_workflow_template_uses_pypi_trusted_publishing() -> None:
    workflow_path = Path("docs/templates/publish.yml")
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["on"]["release"]["types"] == ["published"]
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    assert any(step.get("uses") == "pypa/gh-action-pypi-publish@release/v1" for step in steps)
    assert not any("PYPI_TOKEN" in str(step) or "password" in str(step) for step in steps)
