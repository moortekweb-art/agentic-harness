from __future__ import annotations

import textwrap

import pytest

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.recipes import (
    Recipe,
    _parse_recipe,
    explain_recipe,
    list_recipes,
    load_recipe,
    recipe_names,
)
from agentic_harness.core.safety import format_command, resolve_command_executable


def test_list_recipes_returns_five_recipes() -> None:
    recipes = list_recipes()

    assert len(recipes) == 6
    names = [r.name for r in recipes]
    assert names == [
        "changelog",
        "fix-tests",
        "lint-fix",
        "typecheck-fix",
        "update-docs",
        "verify-tests",
    ]


def test_list_recipes_sorted_by_filename() -> None:
    recipes = list_recipes()

    names = [r.name for r in recipes]
    assert names == sorted(names)


def test_load_recipe_returns_expected_fix_tests_recipe() -> None:
    recipe = load_recipe("fix-tests")

    assert recipe.name == "fix-tests"
    assert "fix failing tests" in recipe.description.lower()
    assert recipe.review_command == ["python", "-m", "pytest", "tests/", "-q"]
    assert recipe.review_command_timeout == 120


def test_load_recipe_accepts_snake_case_for_hyphenated_name() -> None:
    kebab = load_recipe("typecheck-fix")
    snake = load_recipe("typecheck_fix")

    assert kebab == snake
    assert kebab.name == "typecheck-fix"


def test_recipe_names_returns_expected_names() -> None:
    assert recipe_names() == [
        "changelog",
        "fix-tests",
        "lint-fix",
        "typecheck-fix",
        "update-docs",
        "verify-tests",
    ]


def test_load_recipe_accepts_hyphenated_name() -> None:
    recipe = load_recipe("fix-tests")

    assert recipe.name == "fix-tests"
    assert recipe.review_command[0] == "python"


def test_load_recipe_raises_for_unknown_recipe() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_recipe("does-not-exist")

    error = str(exc_info.value)
    assert "unknown recipe" in error
    assert "fix-tests" in error


def test_parse_recipe_rejects_yaml_with_missing_name() -> None:
    text = textwrap.dedent("""\
        description: orphan recipe
        objective_template: do something
        review:
          command:
            - echo
            - ok
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "orphan")

    assert "name" in str(exc_info.value)


def test_parse_recipe_rejects_yaml_with_missing_description() -> None:
    text = textwrap.dedent("""\
        name: orphan
        objective_template: do something
        review:
          command:
            - echo
            - ok
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "orphan")

    assert "description" in str(exc_info.value)


def test_parse_recipe_rejects_yaml_with_missing_objective() -> None:
    text = textwrap.dedent("""\
        name: orphan
        description: orphan recipe
        review:
          command:
            - echo
            - ok
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "orphan")

    assert "objective_template" in str(exc_info.value)


def test_parse_recipe_rejects_yaml_with_empty_name() -> None:
    text = textwrap.dedent("""\
        name: ""
        description: orphan
        objective_template: do something
        review:
          command:
            - echo
            - ok
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "orphan")

    assert "name" in str(exc_info.value)


def test_parse_recipe_rejects_non_dict_yaml() -> None:
    text = "- item one\n- item two\n"

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "list-recipe")

    assert "mapping" in str(exc_info.value)


def test_parse_recipe_rejects_invalid_yaml() -> None:
    text = ":\n  - [\n    broken\n"

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "broken")

    assert "invalid recipe YAML" in str(exc_info.value)


def test_parse_recipe_rejects_missing_review_command() -> None:
    text = textwrap.dedent("""\
        name: orphan
        description: orphan
        objective_template: do something
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "orphan")

    assert "review_command" in str(exc_info.value) or "review" in str(exc_info.value)


def test_parse_recipe_accepts_flat_review_command_key() -> None:
    text = textwrap.dedent("""\
        name: flat-review
        description: uses flat key
        objective_template: do something
        review_command:
          - echo
          - ok
    """)

    recipe = _parse_recipe(text, "flat-review")

    assert recipe.review_command == ["echo", "ok"]
    assert recipe.review_command_timeout == 120


def test_parse_recipe_accepts_review_command_timeout() -> None:
    text = textwrap.dedent("""\
        name: fast-review
        description: fast review
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: 30
    """)

    recipe = _parse_recipe(text, "fast-review")

    assert recipe.review_command_timeout == 30


def test_parse_recipe_accepts_nested_review_command_timeout() -> None:
    text = textwrap.dedent("""\
        name: fast-review
        description: fast review
        objective_template: do something
        review:
          command:
            - echo
            - ok
          command_timeout: 30
    """)

    recipe = _parse_recipe(text, "fast-review")

    assert recipe.review_command_timeout == 30


def test_parse_recipe_default_timeout_is_120() -> None:
    text = textwrap.dedent("""\
        name: default-timeout
        description: default timeout
        objective_template: do something
        review_command:
          - echo
          - ok
    """)

    recipe = _parse_recipe(text, "default-timeout")

    assert recipe.review_command_timeout == 120


def test_explain_recipe_includes_all_sections() -> None:
    recipe = Recipe(
        name="fix-tests",
        description="Fix failing tests",
        objective="Fix the failing tests.",
        review_command=["python", "-m", "pytest", "tests/", "-q"],
    )

    output = explain_recipe(recipe)

    assert "Recipe: fix-tests" in output
    assert "Purpose: Fix failing tests" in output
    assert "What it asks the worker to do:" in output
    assert "Fix the failing tests." in output
    assert "Review command:" in output
    assert "pytest tests/ -q" in output


def test_explain_recipe_includes_timeout_for_long_reviews() -> None:
    recipe = Recipe(
        name="slow-review",
        description="Slow review",
        objective="Run slow review.",
        review_command=["python", "-m", "pytest", "tests/", "-q", "--slow"],
        review_command_timeout=300,
    )

    output = explain_recipe(recipe)

    assert "Recipe: slow-review" in output
    assert "pytest tests/ -q --slow" in output
    assert "Review timeout: 300s" in output


def test_explain_recipe_omits_timeout_for_default() -> None:
    recipe = Recipe(
        name="default-review",
        description="Default review",
        objective="Run review.",
        review_command=["python", "-m", "pytest", "tests/", "-q"],
        review_command_timeout=120,
    )

    output = explain_recipe(recipe)

    assert "Recipe: default-review" in output
    assert "Review timeout:" not in output


def test_recipe_dataclass_is_frozen() -> None:
    recipe = Recipe(
        name="test",
        description="test",
        objective="test",
        review_command=["echo", "ok"],
    )

    with pytest.raises(AttributeError):
        recipe.name = "changed"  # type: ignore[misc]


def test_recipe_dataclass_equality() -> None:
    a = Recipe(
        name="test",
        description="test",
        objective="test",
        review_command=["echo", "ok"],
    )
    b = Recipe(
        name="test",
        description="test",
        objective="test",
        review_command=["echo", "ok"],
    )

    assert a == b


def test_recipe_dataclass_inequality() -> None:
    a = Recipe(
        name="test",
        description="test",
        objective="test",
        review_command=["echo", "ok"],
    )
    b = Recipe(
        name="other",
        description="test",
        objective="test",
        review_command=["echo", "ok"],
    )

    assert a != b


def test_changelog_review_uses_git_diff() -> None:
    recipe = load_recipe("changelog")

    assert "git" in recipe.review_command
    assert "diff" in recipe.review_command


def test_lint_fix_review_uses_ruff() -> None:
    recipe = load_recipe("lint-fix")

    assert "ruff" in recipe.review_command


def test_typecheck_fix_review_uses_mypy() -> None:
    recipe = load_recipe("typecheck-fix")

    assert "mypy" in recipe.review_command


def test_update_docs_review_uses_pytest() -> None:
    recipe = load_recipe("update-docs")

    assert "pytest" in recipe.review_command


def test_public_api_exports_recipes() -> None:
    import agentic_harness

    assert hasattr(agentic_harness, "Recipe")
    assert hasattr(agentic_harness, "list_recipes")
    assert hasattr(agentic_harness, "load_recipe")
    assert hasattr(agentic_harness, "recipe_names")
    assert hasattr(agentic_harness, "explain_recipe")
    assert "Recipe" in agentic_harness.__all__
    assert "list_recipes" in agentic_harness.__all__
    assert "load_recipe" in agentic_harness.__all__
    assert "recipe_names" in agentic_harness.__all__
    assert "explain_recipe" in agentic_harness.__all__


def test_run_recipe_cli_with_noop_worker_succeeds(tmp_path, capsys) -> None:
    from agentic_harness.cli import main

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )

    rc = main(["--project-dir", str(tmp_path), "run-recipe", "fix-tests"])

    output = capsys.readouterr().out
    assert rc == 0
    assert "Recipe: fix-tests" in output
    assert "Result: Verified done" in output
    assert "Attempts: 1" in output
    assert "Retries: 0" in output
    assert "Verification commands:" in output
    expected_command = format_command(
        resolve_command_executable(["python", "-m", "pytest", "tests/", "-q"])
    )
    assert expected_command in output
    assert "independent command passed" in output
    report = next((config_dir / "runs").glob("*/report.md")).read_text(
        encoding="utf-8"
    )
    assert "Verification commands:" in report
    assert expected_command in report


def test_run_recipe_cli_can_print_final_goal_json(tmp_path, capsys) -> None:
    from agentic_harness.cli import main

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )

    rc = main(["--project-dir", str(tmp_path), "run-recipe", "fix-tests", "--json"])

    payload = __import__("json").loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "done"
    assert payload["review"]["passed"] is True


def test_run_recipe_cli_unknown_recipe_returns_error(tmp_path, capsys) -> None:
    from agentic_harness.cli import main

    rc = main(["--project-dir", str(tmp_path), "run-recipe", "nonexistent"])

    payload = __import__("json").loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["ok"] is False
    assert "unknown recipe" in payload["error"]


def test_run_recipe_explain_does_not_require_config(tmp_path, capsys) -> None:
    from agentic_harness.cli import main

    rc = main(["--project-dir", str(tmp_path), "run-recipe", "changelog", "--explain"])

    output = capsys.readouterr().out
    assert rc == 0
    assert "Recipe: changelog" in output
    assert "git" in output
    assert "diff" in output


def test_parse_recipe_rejects_unknown_top_level_key() -> None:
    text = textwrap.dedent("""\
        name: sneaky
        description: has unknown key
        objective_template: do something
        review_command:
          - echo
          - ok
        bonus_points: 100
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "sneaky")

    assert "unknown key" in str(exc_info.value)
    assert "bonus_points" in str(exc_info.value)


def test_parse_recipe_rejects_unknown_review_key() -> None:
    text = textwrap.dedent("""\
        name: sneaky-review
        description: has unknown review key
        objective_template: do something
        review:
          command:
            - echo
            - ok
          bonus_points: 100
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "sneaky-review")

    assert "unknown key" in str(exc_info.value)
    assert "bonus_points" in str(exc_info.value)


def test_parse_recipe_rejects_empty_command_item() -> None:
    text = textwrap.dedent("""\
        name: empty-item
        description: has empty command item
        objective_template: do something
        review_command:
          - echo
          - ""
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "empty-item")

    assert "empty items" in str(exc_info.value)


def test_parse_recipe_rejects_whitespace_only_command_item() -> None:
    text = textwrap.dedent("""\
        name: whitespace-item
        description: has whitespace command item
        objective_template: do something
        review_command:
          - echo
          - "   "
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "whitespace-item")

    assert "empty items" in str(exc_info.value)


def test_parse_recipe_rejects_timeout_below_minimum() -> None:
    text = textwrap.dedent("""\
        name: too-fast
        description: timeout too low
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: 0
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "too-fast")

    assert "timeout" in str(exc_info.value)


def test_parse_recipe_rejects_timeout_above_maximum() -> None:
    text = textwrap.dedent("""\
        name: too-slow
        description: timeout too high
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: 100000
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "too-slow")

    assert "timeout" in str(exc_info.value)


def test_parse_recipe_accepts_timeout_at_minimum() -> None:
    text = textwrap.dedent("""\
        name: min-timeout
        description: minimum timeout
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: 1
    """)

    recipe = _parse_recipe(text, "min-timeout")
    assert recipe.review_command_timeout == 1


def test_parse_recipe_accepts_timeout_at_maximum() -> None:
    text = textwrap.dedent("""\
        name: max-timeout
        description: maximum timeout
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: 86400
    """)

    recipe = _parse_recipe(text, "max-timeout")
    assert recipe.review_command_timeout == 86400


def test_parse_recipe_rejects_negative_timeout() -> None:
    text = textwrap.dedent("""\
        name: negative-timeout
        description: negative timeout
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: -5
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "negative-timeout")

    assert "timeout" in str(exc_info.value)


def test_parse_recipe_nested_review_rejects_empty_command() -> None:
    text = textwrap.dedent("""\
        name: empty-nested
        description: nested empty command
        objective_template: do something
        review:
          command: []
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "empty-nested")

    assert "review_command" in str(exc_info.value) or "command" in str(exc_info.value)


def test_parse_recipe_nested_review_accepts_valid_command() -> None:
    text = textwrap.dedent("""\
        name: valid-nested
        description: nested valid command
        objective_template: do something
        review:
          command:
            - python
            - -m
            - pytest
          command_timeout: 60
    """)

    recipe = _parse_recipe(text, "valid-nested")
    assert recipe.review_command == ["python", "-m", "pytest"]
    assert recipe.review_command_timeout == 60


def test_parse_recipe_unknown_review_key_in_nested() -> None:
    text = textwrap.dedent("""\
        name: sneaky-nested
        description: unknown key in nested review
        objective_template: do something
        review:
          command:
            - echo
            - ok
          magic_word: open-sesame
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "sneaky-nested")

    assert "unknown key" in str(exc_info.value)
    assert "magic_word" in str(exc_info.value)


def test_parse_recipe_rejects_non_integer_timeout_in_flat_key() -> None:
    text = textwrap.dedent("""\
        name: str-timeout
        description: has non-integer timeout
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: "not-a-number"
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "str-timeout")

    assert "timeout" in str(exc_info.value)
    assert "integer" in str(exc_info.value)


def test_parse_recipe_rejects_non_integer_timeout_in_nested_key() -> None:
    text = textwrap.dedent("""\
        name: str-timeout-nested
        description: has non-integer timeout in nested review
        objective_template: do something
        review:
          command:
            - echo
            - ok
          command_timeout: "slow"
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "str-timeout-nested")

    assert "timeout" in str(exc_info.value)
    assert "integer" in str(exc_info.value)


def test_parse_recipe_rejects_boolean_timeout_in_nested_key() -> None:
    # Booleans in nested review.command_timeout must also be rejected.
    text = textwrap.dedent("""\
        name: bool-timeout-nested
        description: has boolean timeout in nested review
        objective_template: do something
        review:
          command:
            - echo
            - ok
          command_timeout: false
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "bool-timeout-nested")

    assert "timeout" in str(exc_info.value)
    assert "boolean" in str(exc_info.value)


def test_parse_recipe_rejects_boolean_timeout_in_flat_key() -> None:
    # Booleans must not be silently coerced to integers (int(True)==1 would give
    # a 1-second review timeout, which is dangerously low).
    text = textwrap.dedent("""\
        name: bool-timeout
        description: has boolean timeout
        objective_template: do something
        review_command:
          - echo
          - ok
        review_command_timeout: true
    """)

    with pytest.raises(ConfigError) as exc_info:
        _parse_recipe(text, "bool-timeout")

    assert "timeout" in str(exc_info.value)
    assert "boolean" in str(exc_info.value)


def test_load_recipe_does_not_cache_results(tmp_path, monkeypatch) -> None:
    """load_recipe must not cache results so recipe files can be reloaded from disk."""
    import agentic_harness.core.recipes as recipes_module

    class FakeRecipeFile:
        def __init__(self, name: str, content: str):
            self.name = name
            self._content = content

        def read_text(self, encoding: str = "utf-8") -> str:
            return self._content

    monkeypatch.setattr(
        recipes_module,
        "_recipe_files",
        lambda: [
            FakeRecipeFile(
                "hot_reload_test.yml",
                textwrap.dedent("""\
            name: hot-reload-test
            description: first version
            objective_template: do version 1
            review_command:
              - echo
              - v1
        """),
            )
        ],
    )

    recipe1 = load_recipe("hot-reload-test")
    assert "first version" in recipe1.description

    monkeypatch.setattr(
        recipes_module,
        "_recipe_files",
        lambda: [
            FakeRecipeFile(
                "hot_reload_test.yml",
                textwrap.dedent("""\
            name: hot-reload-test
            description: second version
            objective_template: do version 2
            review_command:
              - echo
              - v2
        """),
            )
        ],
    )

    recipe2 = load_recipe("hot-reload-test")
    assert "second version" in recipe2.description
