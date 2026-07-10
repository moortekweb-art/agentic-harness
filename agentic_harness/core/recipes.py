"""Built-in beginner recipes for common harness goals."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

import yaml

from agentic_harness.core.errors import ConfigError


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    objective: str
    review_command: list[str]
    review_command_timeout: int = 120


def list_recipes() -> list[Recipe]:
    return [load_recipe(name) for name in recipe_names()]


def recipe_names() -> list[str]:
    return [path.name.removesuffix(".yml").replace("_", "-") for path in _recipe_files()]


def load_recipe(name: str) -> Recipe:
    normalized = name.replace("_", "-")
    return _load_recipe(normalized)


def _load_recipe(normalized: str) -> Recipe:
    filename = f"{normalized.replace('-', '_')}.yml"
    for path in _recipe_files():
        if path.name == filename:
            return _parse_recipe(path.read_text(encoding="utf-8"), normalized)
    available = ", ".join(recipe.name for recipe in list_recipes())
    raise ConfigError(f"unknown recipe: {normalized}; available recipes: {available}")


def explain_recipe(recipe: Recipe) -> str:
    lines = [
        f"Recipe: {recipe.name}",
        f"Purpose: {recipe.description}",
        "What it asks the worker to do:",
        recipe.objective,
        "Review command:",
        " ".join(recipe.review_command),
    ]
    if recipe.review_command_timeout != 120:
        lines.append(f"Review timeout: {recipe.review_command_timeout}s")
    return "\n".join(lines)


def _recipe_files() -> list[Traversable]:
    root = resources.files("agentic_harness.recipes")
    return sorted(
        (path for path in root.iterdir() if path.name.endswith(".yml")),
        key=lambda path: path.name,
    )


ALLOWED_RECIPE_KEYS = {
    "name",
    "description",
    "objective_template",
    "review",
    "review_command",
    "review_command_timeout",
}
ALLOWED_REVIEW_KEYS = {
    "command",
    "command_timeout",
}
MIN_TIMEOUT = 1
MAX_TIMEOUT = 86400


def _parse_recipe(text: str, fallback_name: str) -> Recipe:
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid recipe YAML for {fallback_name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"recipe {fallback_name} must be a mapping")
    _check_unknown_keys(payload, ALLOWED_RECIPE_KEYS, fallback_name)
    name = _required_str(payload, "name", fallback_name)
    description = _required_str(payload, "description", fallback_name)
    objective = _required_str(payload, "objective_template", fallback_name)
    review = payload.get("review")
    if isinstance(review, dict):
        _check_unknown_keys(review, ALLOWED_REVIEW_KEYS, f"{fallback_name} review")
        review_command = _required_list(review, "command", fallback_name)
        raw_timeout = review.get("command_timeout", 120)
        if isinstance(raw_timeout, bool):
            raise ConfigError(
                f"recipe {fallback_name} review_command_timeout must be an integer, got boolean"
            )
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"recipe {fallback_name} review_command_timeout must be an integer, "
                f"got {raw_timeout!r}"
            ) from exc
    else:
        review_command = _required_list(payload, "review_command", fallback_name)
        raw_timeout = payload.get("review_command_timeout", 120)
        if isinstance(raw_timeout, bool):
            raise ConfigError(
                f"recipe {fallback_name} review_command_timeout must be an integer, got boolean"
            )
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"recipe {fallback_name} review_command_timeout must be an integer, "
                f"got {raw_timeout!r}"
            ) from exc
    if not all(item.strip() for item in review_command):
        raise ConfigError(f"recipe {fallback_name} review_command contains empty items")
    if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
        raise ConfigError(
            f"recipe {fallback_name} review_command_timeout must be between "
            f"{MIN_TIMEOUT} and {MAX_TIMEOUT}, got {timeout}"
        )
    return Recipe(
        name=name,
        description=description,
        objective=objective,
        review_command=review_command,
        review_command_timeout=timeout,
    )


def _required_str(payload: dict[str, Any], key: str, recipe: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"recipe {recipe} requires string key: {key}")
    return value.strip()


def _required_list(payload: dict[str, Any], key: str, recipe: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"recipe {recipe} requires non-empty list key: {key}")
    return [str(item) for item in value]


def _check_unknown_keys(payload: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{context} has unknown key(s): {', '.join(unknown)}")
