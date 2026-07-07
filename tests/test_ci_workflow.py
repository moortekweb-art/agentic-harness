from __future__ import annotations

from pathlib import Path
import tomllib

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_ci_runs_package_build_and_compile_smoke() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m compileall agentic_harness" in workflow
    assert "python -m pip install build" in workflow
    assert "python -m build" in workflow
    assert "python -m twine check dist/*" in workflow
    assert "dist/*.whl" in workflow


def test_ci_runs_packaged_demo_from_installed_wheel() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert '"run-demo"' in workflow
    assert '"fix-tests"' in workflow
    assert "requirements-dev.txt" in workflow
    assert "shell-worker.log" in workflow
    assert "report.md" in workflow


def test_ci_smokes_direct_recipe_commands_from_installed_wheel() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert '"lint-fix"' in workflow
    assert '"typecheck-fix"' in workflow
    assert '"update-docs"' in workflow
    assert '"changelog"' in workflow
    assert '"verify-tests"' in workflow
    assert '"--explain"' in workflow


def test_ci_runs_lint_and_typecheck() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m ruff check" in workflow
    assert "python -m mypy agentic_harness" in workflow


def test_ci_runs_on_linux_windows_and_macos() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "runs-on: ${{ matrix.os }}" in workflow


def test_publish_workflow_template_uses_pypi_trusted_publishing() -> None:
    workflow_path = REPO_ROOT / "docs/templates/publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["on"]["release"]["types"] == ["published"]
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["environment"]["url"] == "https://pypi.org/project/local-agentic-harness/"
    assert publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    assert any(step.get("uses") == "pypa/gh-action-pypi-publish@release/v1" for step in steps)
    assert not any("PYPI_TOKEN" in str(step) or "password" in str(step) for step in steps)


def test_active_publish_workflow_uses_pypi_trusted_publishing() -> None:
    workflow_path = REPO_ROOT / ".github/workflows/publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["on"]["release"]["types"] == ["published"]
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["environment"]["url"] == "https://pypi.org/project/local-agentic-harness/"
    assert publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    assert any(step.get("uses") == "pypa/gh-action-pypi-publish@release/v1" for step in steps)
    assert not any("PYPI_TOKEN" in str(step) or "password" in str(step) for step in steps)


def test_distribution_name_avoids_occupied_pypi_project() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "local-agentic-harness"
    assert metadata["project"]["scripts"]["agentic-harness"] == "agentic_harness.cli:main"


def test_release_smoke_has_twine_dependency() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "twine>=5.1" in metadata["project"]["optional-dependencies"]["test"]


def test_readme_documents_pypi_install_command() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pipx install local-agentic-harness" in readme


def test_release_checklist_uses_release_smoke_command() -> None:
    checklist = (REPO_ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "python -m agentic_harness.cli release-smoke" in checklist
    assert "agentic-harness-wheel-smoke" not in checklist
