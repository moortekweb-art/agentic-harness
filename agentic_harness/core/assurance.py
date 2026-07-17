"""Named assurance guarantees, separate from execution effort."""

from __future__ import annotations

from enum import StrEnum


class AssuranceMode(StrEnum):
    """The integrity claim the harness is configured to make."""

    CHECK_GATED = "check_gated"
    SPECIFICATION_FROZEN = "specification_frozen"
    HIGH_ASSURANCE = "high_assurance"
