"""Tests for agentic_harness.core.errors — typed error hierarchy."""

import pytest

from agentic_harness.core.errors import (
    AdapterError,
    ConfigError,
    GoalConflictError,
    HarnessError,
    InvalidTransitionError,
    LoopGuardTripped,
    NoActiveGoalError,
    StateLockError,
)


class TestHarnessErrorHierarchy:
    """Verify the error class hierarchy is correct."""

    def test_harness_error_is_exception(self):
        assert issubclass(HarnessError, Exception)

    def test_invalid_transition_is_harness_error(self):
        assert issubclass(InvalidTransitionError, HarnessError)

    def test_config_error_is_harness_error(self):
        assert issubclass(ConfigError, HarnessError)

    def test_goal_conflict_error_is_harness_error(self):
        assert issubclass(GoalConflictError, HarnessError)

    def test_no_active_goal_error_is_harness_error(self):
        assert issubclass(NoActiveGoalError, HarnessError)

    def test_state_lock_error_is_harness_error(self):
        assert issubclass(StateLockError, HarnessError)

    def test_loop_guard_tripped_is_harness_error(self):
        assert issubclass(LoopGuardTripped, HarnessError)

    def test_adapter_error_is_harness_error(self):
        assert issubclass(AdapterError, HarnessError)

    def test_all_errors_are_catchable_as_harness_error(self):
        """Any of the specific errors can be caught with except HarnessError."""
        for err_cls in [
            InvalidTransitionError,
            ConfigError,
            GoalConflictError,
            NoActiveGoalError,
            StateLockError,
            LoopGuardTripped,
            AdapterError,
        ]:
            with pytest.raises(HarnessError):
                raise err_cls("test message")


class TestInvalidTransitionError:
    def test_default_message(self):
        err = InvalidTransitionError()
        assert str(err) == ""

    def test_custom_message(self):
        err = InvalidTransitionError("cannot go from A to B")
        assert str(err) == "cannot go from A to B"

    def test_isinstance_harness_error(self):
        err = InvalidTransitionError("test")
        assert isinstance(err, HarnessError)


class TestConfigError:
    def test_custom_message(self):
        err = ConfigError("missing config.yaml")
        assert str(err) == "missing config.yaml"

    def test_isinstance_harness_error(self):
        err = ConfigError("test")
        assert isinstance(err, HarnessError)


class TestGoalConflictError:
    def test_custom_message(self):
        err = GoalConflictError("goal already active")
        assert str(err) == "goal already active"

    def test_isinstance_harness_error(self):
        err = GoalConflictError("test")
        assert isinstance(err, HarnessError)


class TestNoActiveGoalError:
    def test_custom_message(self):
        err = NoActiveGoalError("no goal running")
        assert str(err) == "no goal running"

    def test_isinstance_harness_error(self):
        err = NoActiveGoalError("test")
        assert isinstance(err, HarnessError)


class TestStateLockError:
    def test_custom_message(self):
        err = StateLockError("locked by another process")
        assert str(err) == "locked by another process"

    def test_isinstance_harness_error(self):
        err = StateLockError("test")
        assert isinstance(err, HarnessError)


class TestLoopGuardTripped:
    def test_custom_message(self):
        err = LoopGuardTripped("circuit breaker tripped at 10 loops")
        assert str(err) == "circuit breaker tripped at 10 loops"

    def test_isinstance_harness_error(self):
        err = LoopGuardTripped("test")
        assert isinstance(err, HarnessError)


class TestAdapterError:
    def test_custom_message(self):
        err = AdapterError("adapter failed to execute")
        assert str(err) == "adapter failed to execute"

    def test_isinstance_harness_error(self):
        err = AdapterError("test")
        assert isinstance(err, HarnessError)
