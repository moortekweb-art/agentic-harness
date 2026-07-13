"""Public execution strategies independent of model provider and runtime location."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_harness.core.autonomy import AutonomyPolicy


DEFAULT_PUBLIC_STRATEGY = "plan"


@dataclass(frozen=True)
class ExecutionStrategy:
    """One user-facing way to bound and direct a verified goal."""

    key: str
    number: int
    label: str
    best_for: str
    caution: str
    instruction: str
    budget_profile: str
    max_cycles: int | None = None
    max_elapsed_seconds: int | None = None
    max_total_tokens: int | None = None
    max_provider_calls: int | None = None
    max_tool_calls: int | None = None
    repeated_blocker_limit: int = 3
    requires_enforced_scope: bool = False

    def to_public_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "number": self.number,
            "label": self.label,
            "best_for": self.best_for,
            "caution": self.caution,
            "budget_profile": self.budget_profile,
            "requires_enforced_scope": self.requires_enforced_scope,
        }

    def to_metadata(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "instruction": self.instruction,
            "budget_profile": self.budget_profile,
            "requires_enforced_scope": self.requires_enforced_scope,
        }


PUBLIC_STRATEGIES: tuple[ExecutionStrategy, ...] = (
    ExecutionStrategy(
        key="quick",
        number=1,
        label="Quick task",
        best_for="a small, clear job that should finish in one focused pass",
        caution="Uses a deliberately small retry and spending budget.",
        instruction=(
            "Use the Quick task strategy: make the smallest complete change, avoid unrelated "
            "expansion, and move directly to independent verification."
        ),
        budget_profile="small",
        max_cycles=3,
        max_elapsed_seconds=900,
        max_total_tokens=50_000,
        max_provider_calls=20,
        max_tool_calls=80,
        repeated_blocker_limit=2,
    ),
    ExecutionStrategy(
        key="plan",
        number=2,
        label="Plan first",
        best_for="important or unfamiliar work that should be planned before files change",
        caution="Recommended for most first runs.",
        instruction=(
            "Use the Plan first strategy: derive and record a concrete plan and requirements "
            "before changing files, then execute that plan and verify every requirement."
        ),
        budget_profile="balanced",
        max_cycles=20,
        max_elapsed_seconds=3_600,
        max_total_tokens=250_000,
        max_provider_calls=100,
        max_tool_calls=500,
    ),
    ExecutionStrategy(
        key="persistent",
        number=3,
        label="Keep working",
        best_for="a larger job that may need several repair attempts and resumable progress",
        caution="Uses the full limits configured for this workspace.",
        instruction=(
            "Use the Keep working strategy: preserve checkpoints, treat recoverable failures as "
            "repair input, and continue while meaningful progress remains within the configured limits."
        ),
        budget_profile="full",
    ),
    ExecutionStrategy(
        key="experiment",
        number=4,
        label="Bounded experiment",
        best_for="a tiny reversible trial inside explicitly selected files or folders",
        caution="Requires the built-in bounded model worker and an explicit scope.",
        instruction=(
            "Use the Bounded experiment strategy: stay strictly inside the explicit allowed paths, "
            "make the smallest reversible change, and stop rather than widening scope."
        ),
        budget_profile="tiny",
        max_cycles=2,
        max_elapsed_seconds=600,
        max_total_tokens=20_000,
        max_provider_calls=10,
        max_tool_calls=40,
        repeated_blocker_limit=1,
        requires_enforced_scope=True,
    ),
)


def strategy_by_key(value: str) -> ExecutionStrategy:
    normalized = value.strip().lower()
    for strategy in PUBLIC_STRATEGIES:
        if normalized in {strategy.key, str(strategy.number)}:
            return strategy
    valid = ", ".join(strategy.key for strategy in PUBLIC_STRATEGIES)
    raise ValueError(f"Unknown work strategy {value!r}; choose one of {valid}.")


def strategy_from_metadata(value: object) -> ExecutionStrategy:
    if isinstance(value, dict):
        key = str(value.get("key") or "")
        if key:
            try:
                return strategy_by_key(key)
            except ValueError:
                pass
    return strategy_by_key(DEFAULT_PUBLIC_STRATEGY)


def policy_for_strategy(
    base: AutonomyPolicy,
    strategy: ExecutionStrategy,
) -> AutonomyPolicy:
    """Apply a strategy cap without allowing it to exceed workspace limits."""

    return AutonomyPolicy(
        repeated_blocker_limit=min(
            base.repeated_blocker_limit,
            strategy.repeated_blocker_limit,
        ),
        require_completion_claim=True,
        max_cycles=_bounded_limit(base.max_cycles, strategy.max_cycles),
        max_elapsed_seconds=_bounded_limit(
            base.max_elapsed_seconds,
            strategy.max_elapsed_seconds,
        ),
        max_total_tokens=_bounded_limit(
            base.max_total_tokens,
            strategy.max_total_tokens,
        ),
        max_provider_calls=_bounded_limit(
            base.max_provider_calls,
            strategy.max_provider_calls,
        ),
        max_tool_calls=_bounded_limit(base.max_tool_calls, strategy.max_tool_calls),
    )


def _bounded_limit(configured: int, cap: int | None) -> int:
    if cap is None:
        return configured
    if configured <= 0:
        return cap
    return min(configured, cap)
