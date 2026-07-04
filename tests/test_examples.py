from __future__ import annotations

from pathlib import Path


def test_real_world_recipes_cover_supported_runtime_paths() -> None:
    recipes = Path("examples/real-world-recipes.md").read_text(encoding="utf-8")

    assert "worker:" in recipes
    assert "type: coding_agent" in recipes
    assert "worker: shell" in recipes
    assert "worker: tmux" in recipes
    assert "worker: local_llm" in recipes
    assert "worker: github_actions" in recipes
    assert "review_command:" in recipes
