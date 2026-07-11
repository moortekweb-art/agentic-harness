from __future__ import annotations

from pathlib import Path
import re
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

    assert '"--version"' in workflow
    assert '"version"' in workflow
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
    parsed = yaml.safe_load(workflow)
    assert parsed["permissions"] == {"contents": "read"}
    checkout = parsed["jobs"]["test"]["steps"][0]
    assert checkout["with"]["persist-credentials"] is False


def test_ci_runs_on_linux_windows_and_macos() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "runs-on: ${{ matrix.os }}" in workflow


def test_publish_workflow_template_uses_pypi_trusted_publishing() -> None:
    workflow_path = REPO_ROOT / "docs/templates/publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["on"]["push"]["tags"] == ["v*"]
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["environment"]["url"] == "https://pypi.org/project/local-agentic-harness/"
    assert publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    upload = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("pypa/gh-action-pypi-publish@")
    )
    assert upload["with"]["packages-dir"] == "pypi-dist/"
    assert not any("PYPI_TOKEN" in str(step) or "password" in str(step) for step in steps)
    all_steps = workflow["jobs"]["validate"]["steps"] + steps
    run_steps = "\n".join(str(step.get("run", "")) for step in all_steps)
    assert 'python -m pip install -e ".[test]"' in run_steps
    assert "python -m agentic_harness.cli release-smoke --dist-dir dist" in run_steps
    assert "mkdir -p pypi-dist release-bundle" in run_steps
    assert "cp dist/*.whl dist/*.tar.gz pypi-dist/" in run_steps
    assert "cp dist/*.whl dist/*.tar.gz dist/SHA256SUMS release-bundle/" in run_steps
    assert "SHA256SUMS pypi-dist" not in run_steps


def test_active_publish_workflow_uses_pypi_trusted_publishing() -> None:
    workflow_path = REPO_ROOT / ".github/workflows/publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["on"]["push"]["tags"] == ["v*"]
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["environment"]["url"] == "https://pypi.org/project/local-agentic-harness/"
    assert publish["permissions"]["id-token"] == "write"
    steps = publish["steps"]
    upload = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("pypa/gh-action-pypi-publish@")
    )
    assert upload["with"]["packages-dir"] == "pypi-dist/"
    assert not any("PYPI_TOKEN" in str(step) or "password" in str(step) for step in steps)
    all_steps = workflow["jobs"]["validate"]["steps"] + steps
    run_steps = "\n".join(str(step.get("run", "")) for step in all_steps)
    assert 'python -m pip install -e ".[test]"' in run_steps
    assert "python -m agentic_harness.cli release-smoke --dist-dir dist" in run_steps
    assert "mkdir -p pypi-dist release-bundle" in run_steps
    assert "cp dist/*.whl dist/*.tar.gz pypi-dist/" in run_steps
    assert "cp dist/*.whl dist/*.tar.gz dist/SHA256SUMS release-bundle/" in run_steps
    assert "SHA256SUMS pypi-dist" not in run_steps


def test_active_publish_workflow_gates_oidc_on_exact_verified_release_commit() -> None:
    text = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    recovery_input = workflow["on"]["workflow_dispatch"]["inputs"]["release_tag"]
    assert recovery_input["required"] is True
    assert recovery_input["type"] == "string"
    assert "default" not in recovery_input
    recovery_sha_input = workflow["on"]["workflow_dispatch"]["inputs"]["release_sha"]
    assert recovery_sha_input["required"] is True
    assert recovery_sha_input["type"] == "string"
    assert "default" not in recovery_sha_input
    assert workflow["env"]["RELEASE_TAG"] == "${{ inputs.release_tag || github.ref_name }}"
    assert workflow["env"]["RELEASE_SHA"] == "${{ inputs.release_sha || github.sha }}"
    assert "validate" in workflow["jobs"]
    source_gate = next(
        step
        for step in workflow["jobs"]["validate"]["steps"]
        if step.get("name") == "Require protected default-branch recovery source"
    )
    assert source_gate["if"] == "github.event_name == 'workflow_dispatch'"
    assert source_gate["env"]["DEFAULT_BRANCH"] == (
        "${{ github.event.repository.default_branch }}"
    )
    assert '"$GITHUB_REF" != "refs/heads/$DEFAULT_BRANCH"' in source_gate["run"]
    publish = workflow["jobs"]["publish"]
    assert set(publish["needs"]) == {"validate", "stage_release"}
    assert publish["permissions"]["id-token"] == "write"
    assert workflow["jobs"]["validate"]["permissions"].get("id-token") is None
    assert "Verify release tag and package version" in text
    assert "Verify exact release commit passed CI" in text
    assert "Verify release commit is on the default branch" in text
    validation_script = "python agentic_harness/core/release_validation.py"
    assert f"{validation_script} identity" in text
    assert f"{validation_script} ancestry" in text
    assert f"{validation_script} ci" in text
    assert "python -m agentic_harness.core.release_validation" not in text
    validate_steps = workflow["jobs"]["validate"]["steps"]
    first_install = next(
        index
        for index, step in enumerate(validate_steps)
        if "python -m pip install" in str(step.get("run", ""))
    )
    for gate in ("identity", "ancestry", "ci"):
        gate_index = next(
            index
            for index, step in enumerate(validate_steps)
            if f"{validation_script} {gate}" in str(step.get("run", ""))
        )
        assert gate_index < first_install
    assert "actions/upload-artifact@" in text
    assert "actions/download-artifact@" in text
    assert "gh release upload" in text
    stage = workflow["jobs"]["stage_release"]
    assert stage["needs"] == "validate"
    assert stage["permissions"] == {"contents": "write", "actions": "read"}
    assert "environment" not in stage
    assert "github.ref_name" not in str(stage)
    stage_release_step = next(
        step
        for step in stage["steps"]
        if step.get("name") == "Create or update draft release"
    )
    assert stage_release_step["env"]["GH_REPO"] == "${{ github.repository }}"
    final = workflow["jobs"]["publish_release"]
    assert set(final["needs"]) == {"stage_release", "publish"}
    assert final["environment"]["name"] == "github-release"
    assert "github.ref_name" not in str(final)
    final_release_step = next(
        step
        for step in final["steps"]
        if step.get("name") == "Make the verified release public"
    )
    assert final_release_step["env"]["GH_REPO"] == "${{ github.repository }}"
    assert "--draft=false" in text
    assert "gh release create" in text
    assert "--draft" in text
    assert workflow["concurrency"] == {
        "group": "publish-${{ inputs.release_tag || github.ref_name }}",
        "cancel-in-progress": False,
    }
    checkout = next(
        step
        for step in workflow["jobs"]["validate"]["steps"]
        if step.get("name") == "Check out exact release commit"
    )
    assert checkout["with"]["ref"] == "${{ inputs.release_sha || github.sha }}"
    bind = next(
        step
        for step in workflow["jobs"]["validate"]["steps"]
        if step.get("name") == "Bind checked-out release commit"
    )
    assert "id" not in bind
    assert 'checked_out_sha="$(git rev-parse HEAD)"' in bind["run"]
    assert '"$checked_out_sha" != "$RELEASE_SHA"' in bind["run"]
    assert "steps.release.outputs.sha" not in text
    assert (
        workflow["jobs"]["publish_release"]["environment"]["url"]
        == "https://github.com/${{ github.repository }}/releases/tag/${{ env.RELEASE_TAG }}"
    )
    assert "--clobber" not in text
    assert "skip-existing" not in text
    for job_name, job in workflow["jobs"].items():
        if job_name == "publish":
            assert job["permissions"]["id-token"] == "write"
        else:
            assert job["permissions"].get("id-token") is None


def test_publish_template_never_interpolates_release_tag_inside_python_source() -> None:
    text = (REPO_ROOT / "docs/templates/publish.yml").read_text(encoding="utf-8")

    assert 'RELEASE_TAG: ${{ inputs.release_tag || github.ref_name }}' in text
    assert "github.event.release" not in text


def test_active_publish_workflow_matches_documented_template() -> None:
    active = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    template = (REPO_ROOT / "docs/templates/publish.yml").read_text(encoding="utf-8")

    assert active == template


def test_workflows_pin_third_party_actions_to_full_commit_shas() -> None:
    for relative in (
        ".github/workflows/ci.yml",
        ".github/workflows/publish.yml",
        "docs/templates/publish.yml",
    ):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("uses:"):
                continue
            reference = stripped.rsplit("@", 1)[-1].split()[0]
            assert len(reference) == 40
            assert all(char in "0123456789abcdef" for char in reference)


def test_workflows_pin_current_action_releases() -> None:
    expected = {
        "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    }
    for relative in (
        ".github/workflows/ci.yml",
        ".github/workflows/publish.yml",
        "docs/templates/publish.yml",
    ):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for action, sha in expected.items():
            if action in text:
                assert f"{action}@{sha}" in text


def test_distribution_name_avoids_occupied_pypi_project() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "local-agentic-harness"
    assert metadata["project"]["version"] == "0.7.0"
    assert metadata["project"]["requires-python"] == ">=3.11,<3.15"
    assert metadata["project"]["scripts"]["agentic-harness"] == "agentic_harness.cli:main"
    assert metadata["project"]["scripts"]["agentic-harness-gui"] == (
        "agentic_harness.gui.cli:main"
    )
    assert metadata["project"]["urls"]["Repository"].startswith("https://github.com/")
    assert "Development Status :: 4 - Beta" in metadata["project"]["classifiers"]


def test_release_docs_match_current_package_version() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    checklist = (REPO_ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    release_notes = REPO_ROOT / f"docs/RELEASE_NOTES_{version}.md"

    assert f"v{version}" in checklist
    assert f"docs/RELEASE_NOTES_{version}.md" in checklist
    assert release_notes.exists()
    assert release_notes.read_text(encoding="utf-8").startswith(
        f"# Agentic Harness v{version}\n"
    )


def test_release_smoke_has_twine_dependency() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "twine>=5.1" in metadata["project"]["optional-dependencies"]["test"]


def test_readme_documents_pypi_install_command() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pipx install local-agentic-harness" in readme


def test_pypi_readme_uses_resolvable_absolute_links() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    destinations = re.findall(r"\]\(([^)]+)\)", readme)

    assert destinations
    assert all(
        target.startswith(("https://", "http://", "mailto:", "#"))
        for target in destinations
    )


def test_release_checklist_uses_release_smoke_command() -> None:
    checklist = (REPO_ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "python -m agentic_harness.cli release-smoke" in checklist
    assert "agentic-harness-wheel-smoke" not in checklist
