from __future__ import annotations

import re
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from agentic_harness.core.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_real_world_recipes_cover_supported_runtime_paths() -> None:
    recipes = (REPO_ROOT / "examples/real-world-recipes.md").read_text(encoding="utf-8")

    assert "worker:" in recipes
    assert "type: coding_agent" in recipes
    assert "worker: shell" in recipes
    assert "worker: tmux" in recipes
    assert "worker: model_agent" in recipes
    assert "local_llm" in recipes and "deprecated" in recipes
    assert "worker: github_actions" in recipes
    assert "review_command:" in recipes


def test_documented_harness_yaml_snippets_parse_as_config(tmp_path) -> None:
    docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "examples/real-world-recipes.md",
        REPO_ROOT / "examples/coding-agent/README.md",
    ]
    snippets = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for match in re.finditer(r"```yaml\n(.*?)\n```", text, re.DOTALL):
            snippet = match.group(1)
            if re.search(r"^version:\s*1$", snippet, re.MULTILINE):
                snippets.append((doc, snippet))

    assert snippets
    for index, (doc, snippet) in enumerate(snippets):
        project = tmp_path / f"snippet-{index}"
        config_dir = project / ".agentic-harness"
        config_dir.mkdir(parents=True)
        config_path = config_dir / "config.yml"
        config_path.write_text(snippet + "\n", encoding="utf-8")

        config = load_config(project)

        assert config.worker, f"{doc} snippet {index} did not set worker"


def test_killer_demo_contains_runnable_fix_failing_tests_loop() -> None:
    root = REPO_ROOT / "examples/fix-failing-tests-demo"

    assert (root / "README.md").exists()
    assert (root / "mock_coding_agent.py").exists()
    assert (root / "requirements-dev.txt").read_text(encoding="utf-8") == (
        "pytest>=8\nPyYAML>=6.0\n"
    )
    assert (root / "tests" / "test_calculator.py").exists()

    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "agentic-harness init shell" not in readme
    assert "agentic-harness fix-tests" in readme
    assert "auto-creates demo config" in readme
    assert "agentic-harness report" in readme
    assert not (root / ".agentic-harness" / "config.yml").exists()


def test_readme_public_intro_leads_with_a_short_product_and_install_path() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    public_intro = readme.split("# Agentic Harness", 1)[1].split(
        "## What you install", 1
    )[0]
    normalized_intro = " ".join(public_intro.split())

    assert len(public_intro.split()) <= 700
    assert "Let your coding agent work. Make it prove the result." in public_intro
    assert "pipx install local-agentic-harness" in public_intro
    assert "latest published build is 0.7.2" not in public_intro
    assert "git+https://github.com" not in public_intro
    assert "agentic-harness gui" in public_intro
    assert (
        'agentic-harness do "fix the failing tests" '
        '--check "python -m pytest tests/ -q"'
    ) in public_intro
    assert ".agentic-harness/runs/{goal-id}/report.md" in public_intro
    assert "agentic-harness-gui.png" in public_intro
    assert 'width="420"' in public_intro
    assert "See the mobile first-run experience" in public_intro
    assert 'width="220"' in public_intro
    assert "docs/EXTERNAL_BETA.md" in readme
    assert "issues/new?template=external-beta.yml" in readme
    assert "agentic-harness run-demo fix-tests" in public_intro
    assert "controlled mechanics demo" in normalized_intro
    assert "agentic-harness create-demo" not in public_intro
    assert "python -m pip install -r requirements-dev.txt" not in public_intro
    assert public_intro.index("agentic-harness gui") < public_intro.index(
        "agentic-harness run-demo"
    )
    assert "docs/demo-script.md" in public_intro
    assert "agentic-harness selftest" not in public_intro
    assert "agentic-harness goal" not in public_intro
    assert "agentic-harness quickstart" not in public_intro
    assert "agentic-harness run-recipe" not in public_intro
    assert "agentic-harness lint-fix" in readme
    assert "agentic-harness typecheck-fix" in readme
    assert "agentic-harness update-docs" in readme
    assert "agentic-harness changelog" in readme
    assert "agentic-harness verify-tests" in readme
    assert "python -m agentic_harness.cli release-smoke" in readme
    assert ".agentic-harness/runs/{goal-id}/report.md" in readme
    assert ".agentic-harness/runs/<goal-id>/report.md" not in readme
    assert "cat > .agentic-harness/config.yml" not in public_intro
    assert 'width="720"' in readme


def test_readme_documents_released_distribution_install() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    installation = readme.split("## Installation", 1)[1].split("For development:", 1)[0]

    assert "pipx install local-agentic-harness" in installation
    assert "The installed CLI command remains `agentic-harness`." in installation
    assert "After the first PyPI publish" not in installation
    assert "Install the latest published release from PyPI" in installation
    assert "currently 0.7.3" not in installation


def test_external_beta_uses_the_immutable_release_under_evaluation() -> None:
    beta = (REPO_ROOT / "docs/EXTERNAL_BETA.md").read_text(encoding="utf-8")
    normalized = " ".join(beta.split())

    assert beta.count("pipx install local-agentic-harness==0.12.0") == 2
    assert "git+https://github.com" not in beta
    assert "immutable v0.12.0 release" in normalized
    assert "install the latest published release" in normalized


def test_public_getting_started_docs_use_one_verified_task_story() -> None:
    use_cases = (REPO_ROOT / "USE_CASES.md").read_text(encoding="utf-8")
    examples = (REPO_ROOT / "examples/README.md").read_text(encoding="utf-8")

    assert (
        "pipx install --force git+https://github.com/moortekweb-art/agentic-harness.git"
        in use_cases
    )
    assert "latest PyPI release remains available" in use_cases
    assert "agentic-harness gui" in use_cases
    assert (
        'agentic-harness do "draft release notes for the last three commits" '
        '--check "python -m pytest tests/ -q"'
    ) in use_cases
    assert "cat > .agentic-harness/config.yml" not in use_cases
    assert "github_token: token-from-your-secret-store" not in use_cases

    demo = examples.index("fix-failing-tests-demo")
    advanced = examples.index("## Advanced examples")
    shell = examples.index("shell-worker")
    assert demo < advanced < shell


def test_package_description_leads_with_the_completion_gate() -> None:
    payload = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["description"] == (
        "Run coding agents and only mark work done when independent checks pass."
    )


def test_gui_docs_describe_one_install_with_two_interfaces() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs/GUI_ARCHITECTURE.md").read_text(encoding="utf-8")

    for text in (readme, architecture):
        assert "local-agentic-harness" in text
        assert "agentic-harness-gui" in text
        assert ".agentic-harness/" in text
        assert "packaged static" in text
        assert "assets" in text
    assert "same install" in readme.lower()
    assert "shared Python engine" in architecture
    assert "project state model" in architecture


def test_evidence_contract_documents_common_cross_adapter_acceptance_boundary() -> None:
    contract = (REPO_ROOT / "docs/EVIDENCE_CONTRACT.md").read_text(encoding="utf-8")

    for marker in (
        "agentic_harness.evidence.v1",
        "goal_id",
        "run_id",
        "requirement_ids",
        "harness_verified",
        "Coding-agent and custom workers",
        "local-goal",
    ):
        assert marker in contract


def test_readme_links_reproducible_gate_evaluation_without_model_quality_claims() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.casefold().split())

    assert "## Controlled Evaluation" in readme
    assert "24 task-behavior cases across six maintenance payloads" in readme
    assert "24 maintenance tasks" not in readme
    assert "12 false accepts" in readme
    assert "0 false accepts" in readme
    assert "not a real-model benchmark" in readme
    assert "immutable v0.7.2 release snapshot" in normalized
    assert "validate it against the v0.7.2 tag, not current main" in normalized
    assert "evaluation/results/representative/README.md" in readme
    assert "evaluation/results/representative/raw.jsonl" in readme
    assert "both arms passed 9/10 verifiers" in normalized
    assert "direct execution falsely accepted the miss" in normalized
    assert "harness refused it but did not repair it" in normalized
    assert "hard-real-agent-v5-20260712/README.md" in readme


def test_active_product_docs_are_provider_neutral() -> None:
    active_docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs/GUI_ARCHITECTURE.md",
        REPO_ROOT / "docs/GUI_DESIGN.md",
        REPO_ROOT / "docs/GUI_DEPLOYMENT.md",
        REPO_ROOT / "docs/CODEX_GOAL_PARITY.md",
    ]

    for path in active_docs:
        text = path.read_text(encoding="utf-8")
        assert "/mnt/raid0" not in text, path
        assert "Let GLM" not in text, path
        assert "Node1" not in text, path
        assert "Hermes" not in text, path
        assert "Mode 3A" not in text, path


def test_public_product_docs_do_not_expose_private_infrastructure() -> None:
    public_docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "USE_CASES.md",
        REPO_ROOT / "examples/README.md",
        REPO_ROOT / "docs/TURNSTONE_INTEGRATION.md",
    ]
    private_markers = (
        "/mnt/raid0",
        "/Users/MikeMacMini",
        "Hermes",
        "Node1",
        "Mode 3A",
        "Utility Hub",
    )

    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        normalized = text.casefold()
        for marker in private_markers:
            assert marker.casefold() not in normalized, (path, marker)

    assert not (REPO_ROOT / "docs/agentic-harness-gui-design").exists()


def test_turnstone_is_documented_as_an_optional_external_backend() -> None:
    guide = (REPO_ROOT / "docs/TURNSTONE_INTEGRATION.md").read_text(encoding="utf-8")

    assert "turnstonelabs/turnstone" in guide
    assert "optional" in guide.lower()
    assert "not bundled" in guide.lower()
    assert "Private Deployment Note" not in guide
    assert "maintainer's external deployment manifest" not in guide
    assert "v1.7.2" not in guide
    assert "v1.7.3" not in guide


def test_gui_deployment_guide_is_portable_and_uses_placeholders() -> None:
    guide = (REPO_ROOT / "docs/GUI_DEPLOYMENT.md").read_text(encoding="utf-8")
    template = (REPO_ROOT / "docs/agentic-harness-gui.service.template").read_text(
        encoding="utf-8"
    )

    assert "agentic-harness-gui" in guide
    assert "Tailscale Serve" in guide
    assert "loopback" in guide.lower()
    assert "<USER>" in template
    assert "<WORKDIR>" in template
    assert "<EXECUTABLE>" in template
    assert "<PORT>" in template
    assert "--project-dir <WORKDIR>" in template
    assert "--doc-root" not in template
    assert "--no-open" in template
    assert "EnvironmentFile=-<TOKEN_ENV_FILE>" in template


def test_recovery_docs_preserve_failed_goal_evidence() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`.agentic-harness/config.yml`" in readme
    assert "`agentic-harness report`" in readme
    assert "`agentic-harness restart`" in readme
    assert "preserving its evidence" in readme


def test_terminal_demo_script_uses_packaged_run_demo_path() -> None:
    script = (REPO_ROOT / "docs" / "demo-script.md").read_text(encoding="utf-8")
    normalized = " ".join(script.split())

    assert "Target: complete the recording in under two minutes." in script
    assert "Record the actual elapsed time" in script
    assert "seconds on Linux" not in script
    assert "not a published-release performance guarantee" in script
    assert "This is a target, not a measured result." not in normalized
    assert "agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force" in script
    assert "agentic-harness report" in script
    assert "## Optional variants" in script
    assert "agentic-harness create-demo" not in script
    assert "agentic-harness quickstart" not in script
    assert "agentic-harness status" not in script
    assert "cat > .agentic-harness/config.yml" not in script


def test_public_demo_leads_with_one_canonical_path_and_trusted_receipts() -> None:
    script = (REPO_ROOT / "docs/demo-script.md").read_text(encoding="utf-8")
    normalized = " ".join(script.split())

    canonical = script.split("## Optional variants", 1)[0]
    assert "agentic-harness run-demo fix-tests" in canonical
    assert "agentic-harness create-demo" not in canonical
    assert "agentic-harness quickstart" not in canonical
    assert "agentic-harness init-agent" not in canonical
    assert "agentic-harness run-recipe" not in canonical
    assert "Result: Verified done" in script
    assert "Blocked with reason" in script
    assert "Failed with evidence" in script
    assert "Status: done" not in script
    assert "Target: complete the recording in under two minutes." in script
    assert "This is a target, not a measured result." not in normalized


def test_public_gui_docs_use_the_exact_trusted_result_categories() -> None:
    docs = [
        REPO_ROOT / "docs/GUI_DESIGN.md",
        REPO_ROOT / "docs/GUI_ARCHITECTURE.md",
        REPO_ROOT / "docs/CODEX_GOAL_PARITY.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        for category in ("Verified done", "Blocked with reason", "Failed with evidence"):
            assert category in text, (path, category)
        assert "### Done" not in text, path
        assert "Status: done" not in text, path


def test_public_cli_goal_examples_include_an_explicit_independent_check() -> None:
    docs = [
        REPO_ROOT / "docs/demo-script.md",
        REPO_ROOT / "docs/CODEX_GOAL_PARITY.md",
    ]

    examples = []
    for path in docs:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("agentic-harness do "):
                examples.append((path, line))

    assert examples
    for path, command in examples:
        assert " --check " in command, (path, command)


def test_release_guides_are_generic_and_old_receipts_are_historical() -> None:
    checklist = (REPO_ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    publishing = (REPO_ROOT / "docs/PYPI_TRUSTED_PUBLISHING.md").read_text(
        encoding="utf-8"
    )

    assert "The current source candidate is v0.7.2" not in checklist
    assert "final accepted result" not in checklist
    assert 'gh release view "$TAG"' in checklist
    assert "was most recently verified by" not in publishing
    assert "The steady state verified after v0.7.0" not in publishing
    assert "gh release view v0.7.0" not in publishing
    assert "gh run view 29159578285" not in publishing
    assert "## Historical publication receipt: v0.7.0" in publishing


def test_unresolved_launch_draft_is_not_shipped() -> None:
    assert not (REPO_ROOT / "docs/launch-posts.md").exists()


def test_killer_demo_readme_says_first_pytest_is_expected_to_fail() -> None:
    readme = (REPO_ROOT / "examples/fix-failing-tests-demo/README.md").read_text(encoding="utf-8")

    assert "expected to fail" in readme


def test_killer_demo_runs_failure_fix_review_cycle(tmp_path) -> None:
    source = REPO_ROOT / "examples/fix-failing-tests-demo"
    demo = tmp_path / "fix-failing-tests-demo"
    shutil.copytree(source, demo)
    shutil.rmtree(demo / ".agentic-harness", ignore_errors=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    before = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert before.returncode != 0
    assert "assert 6 == 5" in before.stdout

    run = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "fix-tests", "--until-done"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    assert "Result: Verified done" in run.stdout
    assert "Review: passed" in run.stdout
    config = (demo / ".agentic-harness" / "config.yml").read_text(encoding="utf-8")
    assert "mock_coding_agent.py" in config
    assert sys.executable in config
    assert "{objective}" not in config

    status = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "status"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert status.returncode == 0, status.stderr
    assert "Status: verified done" in status.stdout

    report = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "report"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert report.returncode == 0, report.stderr
    assert "Result: Verified done" in report.stdout
    assert "Review: passed" in report.stdout
    assert "Report: .agentic-harness/runs/" in report.stdout
    report_paths = list((demo / ".agentic-harness" / "runs").glob("*/report.md"))
    assert report_paths
    report_text = report_paths[0].read_text(encoding="utf-8")
    assert "Report: .agentic-harness/runs/" in report_text
    assert "Worker claim (untrusted): fixed calculator for objective: Fix the failing tests" in (
        report_text
    )
    assert "{objective}" not in report_text
    assert list((demo / ".agentic-harness" / "runs").glob("*/shell-worker.log"))

    after = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert after.returncode == 0


def test_packaged_demo_generator_runs_failure_fix_review_cycle(tmp_path) -> None:
    demo = tmp_path / "generated-demo"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    create = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "create-demo", "fix-tests", str(demo)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert create.returncode == 0, create.stderr
    assert "Created demo:" in create.stdout
    assert (demo / "requirements-dev.txt").read_text(encoding="utf-8") == (
        "pytest>=8\nPyYAML>=6.0\n"
    )

    before = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert before.returncode != 0
    assert "assert 6 == 5" in before.stdout

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_harness.cli",
            "fix-tests",
            "--until-done",
        ],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    assert "Result: Verified done" in run.stdout
    assert "Review: passed" in run.stdout
    assert "Attempts: 2" in run.stdout
    assert "Retries: 1" in run.stdout
    config = (demo / ".agentic-harness" / "config.yml").read_text(encoding="utf-8")
    assert "mock_coding_agent.py" in config
    assert sys.executable in config

    report = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "report"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert report.returncode == 0, report.stderr
    assert "Report: .agentic-harness/runs/" in report.stdout
    assert list((demo / ".agentic-harness" / "runs").glob("*/shell-worker.log"))
    assert list((demo / ".agentic-harness" / "runs").glob("*/report.md"))

    after = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert after.returncode == 0


def test_repo_artwork_assets_exist() -> None:
    social = REPO_ROOT / "docs/assets/agentic-harness-social-preview.png"
    icon = REPO_ROOT / "docs/assets/agentic-harness-icon.png"

    assert social.exists()
    assert icon.exists()
    assert social.stat().st_size > 100_000
    assert icon.stat().st_size > 100_000


def test_license_and_authors_credit_michael_moortekweb() -> None:
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    authors = (REPO_ROOT / "AUTHORS.md").read_text(encoding="utf-8")

    assert "Copyright (c) 2026 Michael / Moortekweb" in license_text
    assert "Agentic Harness was created by Michael / Moortekweb." in authors
