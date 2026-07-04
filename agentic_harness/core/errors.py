"""Typed errors raised by the harness core."""


class HarnessError(Exception):
    """Base class for expected harness failures."""


class InvalidTransitionError(HarnessError):
    """Raised when a goal status transition is not allowed."""


class ConfigError(HarnessError):
    """Raised when project configuration is missing or invalid."""


class GoalConflictError(HarnessError):
    """Raised when an operation would clobber an active goal."""


class StateLockError(HarnessError):
    """Raised when project-local harness state is locked by another process."""


class LoopGuardTripped(HarnessError):
    """Raised when auto-continue behavior exceeds the configured circuit breaker."""


class AdapterError(HarnessError):
    """Raised by execution adapters for expected runtime failures."""
