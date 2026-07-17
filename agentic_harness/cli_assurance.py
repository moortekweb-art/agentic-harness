"""CLI wiring for operator-owned GoalSpec approval."""

from __future__ import annotations

import argparse
from typing import Any

from agentic_harness.core.autonomy import AutonomousRunner
from agentic_harness.core.config import HarnessConfig
from agentic_harness.core.factory import autonomy_policy_from_config
from agentic_harness.core.state import Goal
from agentic_harness.core.supervisor import Supervisor


def add_approval_parser(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Register the plain-language initial and amendment approval command."""

    approve_spec = subcommands.add_parser(
        "approve-spec",
        help="Approve pending high-assurance completion conditions",
    )
    approve_spec.add_argument(
        "--requirement",
        action="append",
        default=None,
        help="Replace the proposed conditions with this plain-language condition; repeatable.",
    )


def approve_pending_specification(
    supervisor: Supervisor,
    config: HarnessConfig,
    requirements: list[str] | None,
) -> Goal:
    """Approve either the initial proposal or a pending versioned amendment."""

    return AutonomousRunner(
        supervisor,
        policy=autonomy_policy_from_config(config),
    ).approve_specification(requirements)
