"""Project-local artifact storage."""

from __future__ import annotations

from contextlib import contextmanager
import importlib
import importlib.util
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from typing import Any, Iterator, cast

from agentic_harness.core.errors import StateLockError
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.state import Goal

fcntl = importlib.import_module("fcntl") if importlib.util.find_spec("fcntl") else None
msvcrt = importlib.import_module("msvcrt") if importlib.util.find_spec("msvcrt") else None


class ArtifactStore:
    """Write and read goal artifacts below a project-local state directory."""

    def __init__(self, root: str | Path = ".agentic-harness") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.current_path = self.root / "current.json"
        self.lock_path = self.root / "state.lock"
        self.autonomy_lock_path = self.root / "autonomy.lock"
        self._autonomy_lease: object | None = None

    def init(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Acquire a non-blocking project-local state lock."""
        with self._locked_path(
            self.lock_path,
            f"harness state is locked by another process: {self.lock_path}",
        ):
            yield

    @contextmanager
    def autonomy_locked(self) -> Iterator[object]:
        """Lease autonomous goal decisions to one driver process."""
        with self._locked_path(
            self.autonomy_lock_path,
            "autonomous driver is already active for this project: "
            f"{self.autonomy_lock_path}",
        ):
            lease = object()
            self._autonomy_lease = lease
            try:
                yield lease
            finally:
                if self._autonomy_lease is lease:
                    self._autonomy_lease = None

    def owns_autonomy_lease(self, lease: object | None) -> bool:
        return lease is not None and lease is self._autonomy_lease

    @contextmanager
    def _locked_path(self, path: Path, conflict_message: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            try:
                self._lock_handle(handle)
            except (BlockingIOError, OSError) as exc:
                raise StateLockError(conflict_message) from exc
            try:
                yield
            finally:
                self._unlock_handle(handle)

    def goal_dir(self, goal: Goal | str) -> Path:
        goal_id = goal.id if isinstance(goal, Goal) else goal
        return self.runs_dir / goal_id

    def write_goal(self, goal: Goal, *, make_current: bool = True) -> Path:
        run_dir = self.goal_dir(goal)
        run_dir.mkdir(parents=True, exist_ok=True)
        state_path = run_dir / "state.json"
        self._write_json(state_path, goal.to_dict())
        if make_current:
            self._write_json(self.current_path, {"goal_id": goal.id})
        return state_path

    def read_goal(self, goal_id: str) -> Goal:
        try:
            return Goal.from_dict(self._read_json(self.goal_dir(goal_id) / "state.json"))
        except (json.JSONDecodeError, OSError, ValueError):
            raise StateLockError(f"corrupted or missing goal state for {goal_id}")

    def read_current_goal(self) -> Goal | None:
        if not self.current_path.exists():
            return None
        try:
            payload = self._read_json(self.current_path)
        except (json.JSONDecodeError, OSError):
            return None
        goal_id = payload.get("goal_id")
        if not isinstance(goal_id, str):
            return None
        return self.read_goal(goal_id)

    def list_goals(self) -> list[Goal]:
        """Return readable durable goals, newest first."""

        goals: list[Goal] = []
        if not self.runs_dir.exists():
            return goals
        for state_path in self.runs_dir.glob("*/state.json"):
            try:
                goal = Goal.from_dict(self._read_json(state_path))
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            goals.append(goal)
        goals.sort(key=lambda goal: (goal.updated_at, goal.created_at, goal.id), reverse=True)
        return goals

    def write_report(self, goal: Goal, content: str, name: str = "report.md") -> Path:
        run_dir = self.goal_dir(goal)
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = (run_dir / name).resolve()
        run_root = run_dir.resolve()
        try:
            report_path.relative_to(run_root)
        except ValueError as exc:
            raise ValueError("report path is outside goal artifact directory") from exc
        self._write_text(report_path, content)
        project_root = self.root.resolve().parent
        rel = report_path.relative_to(project_root).as_posix()
        if rel not in goal.artifacts:
            goal.artifacts.append(rel)
        return report_path

    def repair_current_marker(self) -> Goal | None:
        """Restore current.json from the most recently updated run when only marker state is missing.

        Uses the goal's ``updated_at`` timestamp from the state file to determine
        recency, not ``st_mtime``. Filesystem modification time can be wrong after
        copies, restores, or VCS operations, so ``updated_at`` is the authoritative
        source for which goal was worked on most recently.
        """
        if self.current_path.exists():
            return self.read_current_goal()
        candidates: list[tuple[str, Path]] = []
        for state_path in self.runs_dir.glob("*/state.json"):
            try:
                payload = self._read_json(state_path)
            except (json.JSONDecodeError, OSError):
                continue
            updated_at = payload.get("updated_at")
            goal_id = payload.get("id")
            if not isinstance(updated_at, str) or not isinstance(goal_id, str):
                continue
            candidates.append((updated_at, state_path))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        try:
            goal = Goal.from_dict(self._read_json(candidates[0][1]))
        except (json.JSONDecodeError, OSError, ValueError):
            return None
        self._write_json(self.current_path, {"goal_id": goal.id})
        return goal

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _read_json(self, path: Path) -> dict[str, Any]:
        for attempt in range(3):
            try:
                content = path.read_text(encoding="utf-8")
                return cast(dict[str, Any], json.loads(content))
            except (FileNotFoundError, PermissionError):
                if attempt == 2:
                    raise
                time.sleep(0.01)
        raise AssertionError("unreachable")

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=str(path.parent),
                delete=False,
            ) as handle:
                handle.write(redact_secrets(content))
                tmp = Path(handle.name)
            tmp.replace(path)
        except Exception:
            if tmp is not None and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

    def _lock_handle(self, handle: Any) -> None:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        raise StateLockError("state locking is unsupported on this platform")

    def _unlock_handle(self, handle: Any) -> None:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        raise StateLockError("state unlocking is unsupported on this platform")
