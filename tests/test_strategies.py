from __future__ import annotations

import pytest

from agentic_harness.core.autonomy import AutonomyPolicy
from agentic_harness.core.strategies import (
    DEFAULT_PUBLIC_STRATEGY,
    PUBLIC_STRATEGIES,
    policy_for_strategy,
    strategy_by_key,
    strategy_from_metadata,
)


def test_public_strategies_are_provider_independent_and_plan_first_by_default() -> None:
    assert DEFAULT_PUBLIC_STRATEGY == "plan"
    assert [strategy.key for strategy in PUBLIC_STRATEGIES] == [
        "quick",
        "plan",
        "persistent",
        "experiment",
    ]
    assert [strategy.label for strategy in PUBLIC_STRATEGIES] == [
        "Quick task",
        "Plan first",
        "Keep working",
        "Bounded experiment",
    ]
    assert all("glm" not in strategy.instruction.lower() for strategy in PUBLIC_STRATEGIES)
    assert all("provider" not in strategy.key for strategy in PUBLIC_STRATEGIES)


def test_strategy_policy_caps_small_modes_but_preserves_persistent_limits() -> None:
    base = AutonomyPolicy(
        repeated_blocker_limit=3,
        max_cycles=100,
        max_elapsed_seconds=7_200,
        max_total_tokens=500_000,
        max_provider_calls=200,
        max_tool_calls=1_000,
    )

    quick = policy_for_strategy(base, strategy_by_key("quick"))
    persistent = policy_for_strategy(base, strategy_by_key("persistent"))
    experiment = policy_for_strategy(base, strategy_by_key("experiment"))

    assert quick.max_cycles == 3
    assert quick.max_elapsed_seconds == 900
    assert quick.max_total_tokens == 50_000
    assert persistent == base
    assert experiment.max_cycles == 2
    assert experiment.max_provider_calls == 10
    assert experiment.repeated_blocker_limit == 1


def test_strategy_policy_never_expands_tighter_workspace_limits() -> None:
    base = AutonomyPolicy(
        max_cycles=1,
        max_elapsed_seconds=30,
        max_total_tokens=100,
        max_provider_calls=2,
        max_tool_calls=3,
    )

    quick = policy_for_strategy(base, strategy_by_key("quick"))

    assert quick.max_cycles == 1
    assert quick.max_elapsed_seconds == 30
    assert quick.max_total_tokens == 100
    assert quick.max_provider_calls == 2
    assert quick.max_tool_calls == 3


def test_missing_or_stale_strategy_metadata_fails_to_safe_default() -> None:
    assert strategy_from_metadata(None).key == "plan"
    assert strategy_from_metadata({"key": "retired-route"}).key == "plan"


def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown work strategy"):
        strategy_by_key("glm-mode")
