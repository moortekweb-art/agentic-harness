from __future__ import annotations

import re
from pathlib import Path

from agentic_harness.core.config import load_config


def test_real_world_recipes_cover_supported_runtime_paths() -> None:
    recipes = Path("examples/real-world-recipes.md").read_text(encoding="utf-8")

    assert "worker:" in recipes
    assert "type: coding_agent" in recipes
    assert "worker: shell" in recipes
    assert "worker: tmux" in recipes
    assert "worker: local_llm" in recipes
    assert "worker: github_actions" in recipes
    assert "review_command:" in recipes


def test_documented_harness_yaml_snippets_parse_as_config(tmp_path) -> None:
    docs = [
        Path("README.md"),
        Path("examples/real-world-recipes.md"),
        Path("examples/coding-agent/README.md"),
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
    root = Path("examples/fix-failing-tests-demo")

    assert (root / "README.md").exists()
    assert (root / ".agentic-harness" / "config.yml").exists()
    assert (root / "mock_coding_agent.py").exists()
    assert (root / "tests" / "test_calculator.py").exists()

    readme = (root / "README.md").read_text(encoding="utf-8")
    config = (root / ".agentic-harness" / "config.yml").read_text(encoding="utf-8")

    assert 'agentic-harness run "fix failing tests"' in readme
    assert "type: coding_agent" in config
    assert "mock_coding_agent.py" in config
    assert "pytest" in config


def test_repo_artwork_assets_exist() -> None:
    social = Path("docs/assets/agentic-harness-social-preview.png")
    icon = Path("docs/assets/agentic-harness-icon.png")

    assert social.exists()
    assert icon.exists()
    assert social.stat().st_size > 100_000
    assert icon.stat().st_size > 100_000


def test_license_and_authors_credit_michael_moortekweb() -> None:
    license_text = Path("LICENSE").read_text(encoding="utf-8")
    authors = Path("AUTHORS.md").read_text(encoding="utf-8")

    assert "Copyright (c) 2026 Michael / Moortekweb" in license_text
    assert "Agentic Harness was created by Michael / Moortekweb." in authors
