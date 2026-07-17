"""Validated operator-controlled changes to frozen GoalSpecs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentic_harness.core.errors import GoalConflictError
from agentic_harness.core.goal_spec import GoalRequirement, GoalSpec, SAFE_REQUIREMENT_ID


def amended_requirements(
    current: GoalSpec,
    proposed_changes: object,
    *,
    replacement_texts: list[str] | None = None,
) -> tuple[GoalRequirement, ...]:
    """Return a validated revision while preserving stable IDs where possible."""

    if replacement_texts is not None:
        texts = _clean_texts(replacement_texts)
        return tuple(
            GoalRequirement(id=f"R{index}", text=text)
            for index, text in enumerate(texts, 1)
        )
    if not isinstance(proposed_changes, list) or not proposed_changes:
        raise GoalConflictError("specification amendment contains no proposed changes")
    requirements = list(current.requirements)
    next_number = _next_numeric_id(item.id for item in requirements)
    for raw_change in proposed_changes:
        if not isinstance(raw_change, dict):
            raise GoalConflictError("specification amendment change must be an object")
        operation = str(raw_change.get("operation") or "").strip().lower()
        requirement_id = str(raw_change.get("requirement_id") or "").strip()
        index = next(
            (position for position, item in enumerate(requirements) if item.id == requirement_id),
            None,
        )
        if operation == "replace":
            if index is None:
                raise GoalConflictError(
                    f"cannot replace unknown requirement {requirement_id or '(missing)'}"
                )
            text = _clean_text(raw_change.get("new_text"))
            requirements[index] = GoalRequirement(id=requirement_id, text=text)
        elif operation == "remove":
            if index is None:
                raise GoalConflictError(
                    f"cannot remove unknown requirement {requirement_id or '(missing)'}"
                )
            requirements.pop(index)
        elif operation == "add":
            text = _clean_text(raw_change.get("new_text"))
            new_id = requirement_id or f"R{next_number}"
            if SAFE_REQUIREMENT_ID.fullmatch(new_id) is None:
                raise GoalConflictError("added requirement id is invalid")
            if any(item.id == new_id for item in requirements):
                raise GoalConflictError(f"requirement {new_id} already exists")
            requirements.append(GoalRequirement(id=new_id, text=text))
            next_number += 1
        else:
            raise GoalConflictError(
                "specification amendment operation must be add, replace, or remove"
            )
    if not requirements:
        raise GoalConflictError("specification amendment cannot remove every requirement")
    return tuple(requirements)


def _clean_texts(values: list[str]) -> list[str]:
    texts = [_clean_text(value) for value in values]
    if not texts:
        raise GoalConflictError("approved specification requires completion conditions")
    return texts


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise GoalConflictError("specification amendment text must not be empty")
    return text


def _next_numeric_id(requirement_ids: Iterable[str]) -> int:
    numbers = [
        int(requirement_id[1:])
        for requirement_id in requirement_ids
        if requirement_id.startswith("R") and requirement_id[1:].isdigit()
    ]
    return max(numbers, default=0) + 1
