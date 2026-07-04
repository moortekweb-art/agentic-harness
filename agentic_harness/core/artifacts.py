"""Project-local artifact storage."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterator, cast

from agentic_harness.core.errors import StateLockError
from agentic_harness.core.state import Goal


class ArtifactStore:
    """Write and read goal artifacts below a project-local state directory."""

    def __init__(self, root: str | Path = ".agentic-harness") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.current_path = self.root / "current.json"
        self.lock_path = self.root / "state.lock"

    def init(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Acquire a non-blocking project-local state lock."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateLockError(
                    f"harness state is locked by another process: {self.lock_path}"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def goal_dir(self, goal: Goal | str) -> Path:
        goal_id = goal.id if isinstance(goal, Goal) else goal
        return self.runs_dir / goal_id

    def write_goal(self, goal: Goal) -> Path:
        run_dir = self.goal_dir(goal)
        run_dir.mkdir(parents=True, exist_ok=True)
        state_path = run_dir / "state.json"
        self._write_json(state_path, goal.to_dict())
        self._write_json(self.current_path, {"goal_id": goal.id})
        return state_path

    def read_goal(self, goal_id: str) -> Goal:
        return Goal.from_dict(self._read_json(self.goal_dir(goal_id) / "state.json"))

    def read_current_goal(self) -> Goal | None:
        if not self.current_path.exists():
            return None
        payload = self._read_json(self.current_path)
        goal_id = payload.get("goal_id")
        if not isinstance(goal_id, str):
            return None
        return self.read_goal(goal_id)

    def write_report(self, goal: Goal, content: str, name: str = "report.md") -> Path:
        run_dir = self.goal_dir(goal)
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / name
        self._write_text(report_path, content)
        rel = str(report_path.relative_to(self.root.parent if self.root.parent != Path("") else Path(".")))
        if rel not in goal.artifacts:
            goal.artifacts.append(rel)
        return report_path

    def repair_current_marker(self) -> Goal | None:
        """Restore current.json from the most recently updated run when only marker state is missing."""
        if self.current_path.exists():
            return self.read_current_goal()
        candidates = sorted(
            self.runs_dir.glob("*/state.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return None
        goal = Goal.from_dict(self._read_json(candidates[0]))
        self._write_json(self.current_path, {"goal_id": goal.id})
        return goal

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _read_json(self, path: Path) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), delete=False
        ) as handle:
            handle.write(content)
            tmp = Path(handle.name)
        tmp.replace(path)
