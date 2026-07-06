from __future__ import annotations

import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agentic_harness.core.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_real_world_recipes_cover_supported_runtime_paths() -> None:
    recipes = (REPO_ROOT / "examples/real-world-recipes.md").read_text(encoding="utf-8")

    assert "worker:" in recipes
    assert "type: coding_agent" in recipes
    assert "worker: shell" in recipes
    assert "worker: tmux" in recipes
    assert "worker: local_llm" in recipes
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
    assert (root / "requirements-dev.txt").read_text(encoding="utf-8") == "pytest>=8\n"
    assert (root / "tests" / "test_calculator.py").exists()

    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "agentic-harness init shell" in readme
    assert "agentic-harness fix-tests" in readme
    assert "agentic-harness report" in readme
    assert not (root / ".agentic-harness" / "config.yml").exists()


def test_readme_quick_start_uses_easy_path_not_manual_yaml() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick Start", 1)[1].split("### Recipes", 1)[0]

    assert "agentic-harness selftest" in quick_start
    assert "agentic-harness quickstart" in quick_start
    assert "agentic-harness run-demo fix-tests" in quick_start
    assert "agentic-harness create-demo fix-tests" in quick_start
    assert "python -m pip install -r requirements-dev.txt" in quick_start
    assert "agentic-harness init shell" in quick_start
    assert "agentic-harness fix-tests" in quick_start
    assert "agentic-harness lint-fix" in readme
    assert "agentic-harness typecheck-fix" in readme
    assert "agentic-harness update-docs" in readme
    assert "agentic-harness changelog" in readme
    assert "agentic-harness verify-tests" in readme
    assert "python -m agentic_harness.cli release-smoke" in readme
    assert ".agentic-harness/runs/<goal-id>/report.md" in readme
    assert "cat > .agentic-harness/config.yml" not in quick_start


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

    init = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "init", "shell"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert init.returncode == 0, init.stderr
    config = (demo / ".agentic-harness" / "config.yml").read_text(encoding="utf-8")
    assert "mock_coding_agent.py" in config
    assert sys.executable in config

    run = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "fix-tests"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    assert "Result: done" in run.stdout
    assert "Review: passed" in run.stdout

    status = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "status", "--format", "text"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert status.returncode == 0, status.stderr
    assert "Status: done" in status.stdout

    report = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "report"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert report.returncode == 0, report.stderr
    assert "Result: done" in report.stdout
    assert "Review: passed" in report.stdout
    assert "Report: .agentic-harness/runs/" in report.stdout
    report_paths = list((demo / ".agentic-harness" / "runs").glob("*/report.md"))
    assert report_paths
    assert "Report: .agentic-harness/runs/" in report_paths[0].read_text(encoding="utf-8")
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
    assert (demo / "requirements-dev.txt").read_text(encoding="utf-8") == "pytest>=8\n"

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

    init = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "init", "shell"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert init.returncode == 0, init.stderr
    config = (demo / ".agentic-harness" / "config.yml").read_text(encoding="utf-8")
    assert "mock_coding_agent.py" in config
    assert sys.executable in config

    run = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "fix-tests"],
        cwd=demo,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    assert "Result: done" in run.stdout
    assert "Review: passed" in run.stdout

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
