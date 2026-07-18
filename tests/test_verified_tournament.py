from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

import agentic_harness.core.tournament as tournament_module
from agentic_harness.cli import main
from agentic_harness.core.errors import ConfigError
from agentic_harness.core.tournament import (
    CandidateResult,
    run_verified_tournament,
    select_verified_candidate,
)
from agentic_harness.gui.backend import EmbeddedExecutionBackend


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _project(
    tmp_path: Path,
    *,
    final_only_failure: bool = False,
    all_fail: bool = False,
    tamper_verifier: bool = False,
) -> tuple[Path, list[list[str]]]:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitignore").write_text(".agentic-harness/\n", encoding="utf-8")
    (root / "value.txt").write_text("original\n", encoding="utf-8")
    check_source = """
from pathlib import Path
import sys

value = Path("value.txt").read_text(encoding="utf-8").strip()
worktree_ok = Path(".git").is_file() if FINAL_ONLY_FAILURE else True
raise SystemExit(0 if value == "good" and worktree_ok else 1)
""".replace("FINAL_ONLY_FAILURE", "True" if final_only_failure else "False")
    (root / "check.py").write_text(check_source.strip() + "\n", encoding="utf-8")
    worker_source = r'''
from __future__ import annotations

import json
import os
from pathlib import Path
import re

instruction = os.environ.get("AGENTIC_HARNESS_INSTRUCTION", "")
match = re.search(r"implementation candidate (\d+)", instruction)
candidate = int(match.group(1)) if match else 1
bad_candidate = ALL_FAIL or TAMPER_VERIFIER or candidate == 1
Path("value.txt").write_text("bad\n" if bad_candidate else "good\n", encoding="utf-8")
if TAMPER_VERIFIER and candidate == 2:
    Path("check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
outcome = {
    "status": "complete",
    "plan": [{"status": "complete", "step": "Implement the candidate"}],
    "current_subgoal": "Verify the candidate",
    "checkpoint": f"candidate_{candidate}_implemented",
    "requirement_status": [
        {"id": "R1", "status": "satisfied", "evidence": ["review:1"]}
    ],
    "blockers": [],
    "summary": f"candidate {candidate} implemented",
}
print("HARNESS_RESULT_JSON=" + json.dumps(outcome, separators=(",", ":")))
'''.replace("ALL_FAIL", "True" if all_fail else "False").replace(
        "TAMPER_VERIFIER", "True" if tamper_verifier else "False"
    )
    (root / "worker.py").write_text(worker_source.strip() + "\n", encoding="utf-8")
    review_command = [sys.executable, "check.py"]
    config = {
        "version": 1,
        "worker": {
            "type": "coding_agent",
            "coding_agent_command": [sys.executable, "worker.py"],
            "coding_agent_timeout": 30,
        },
        "review_command": review_command,
        "review_covers": ["*"],
        "review_command_timeout": 30,
        "autonomy": {
            "max_cycles": 2,
            "max_elapsed_seconds": 120,
            "max_total_tokens": 10_000,
            "max_provider_calls": 10,
            "max_tool_calls": 100,
        },
    }
    config_path = root / ".agentic-harness" / "config.yml"
    config_path.parent.mkdir()
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Agentic Harness Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root, [review_command]


def test_selector_never_chooses_the_least_bad_candidate() -> None:
    candidates = [
        CandidateResult(number=1, verified=False, patch_bytes=10, patch_file="one.patch"),
        CandidateResult(number=2, verified=False, patch_bytes=1, patch_file="two.patch"),
    ]

    assert select_verified_candidate(candidates) is None


def test_gui_runs_verified_tournament_and_exposes_only_reverified_winner(
    tmp_path: Path,
) -> None:
    root, _ = _project(tmp_path)
    backend = EmbeddedExecutionBackend(root)

    started = backend.start(
        {
            "objective": "Set value.txt to good and prove it",
            "candidate_count": 2,
        }
    )
    deadline = time.monotonic() + 15
    finished = started
    while time.monotonic() < deadline:
        finished = backend.status()
        if finished["status"] in {"done", "blocked", "failed"}:
            break
        time.sleep(0.02)

    assert finished["status"] == "done"
    assert finished["final_result"]["accepted"] is True
    assert (root / "value.txt").read_text(encoding="utf-8") == "good\n"
    goal = backend.store.read_current_goal()
    assert goal is not None
    tournament = goal.metadata["verified_tournament"]
    assert tournament["candidate_count"] == 2
    assert tournament["winner"] == 2
    assert tournament["applied"] is True
    assert tournament["status"] == "verified_done"
    assert len(tournament["candidates"]) == 2
    assert goal.review is not None and goal.review["passed"] is True


def test_gui_tournament_reports_blocked_when_every_candidate_fails(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, all_fail=True)
    backend = EmbeddedExecutionBackend(root)

    backend.start({"objective": "Set value.txt to good and prove it", "candidate_count": 2})
    deadline = time.monotonic() + 15
    finished: dict[str, object] = {}
    while time.monotonic() < deadline:
        finished = backend.status()
        if finished["status"] in {"done", "blocked", "failed"}:
            break
        time.sleep(0.02)

    assert finished["status"] == "blocked"
    assert finished["final_result"]["accepted"] is False
    assert (root / "value.txt").read_text(encoding="utf-8") == "original\n"
    goal = backend.store.read_current_goal()
    assert goal is not None
    assert goal.metadata["verified_tournament"]["winner"] is None
    assert goal.metadata["verified_tournament"]["applied"] is False


def test_selector_chooses_smallest_verified_patch() -> None:
    candidates = [
        CandidateResult(
            number=1,
            verified=True,
            receipt_category="verified_done",
            changed_files=["a.py", "b.py"],
            patch_bytes=20,
            patch_sha256="a",
            patch_file="one.patch",
        ),
        CandidateResult(
            number=2,
            verified=True,
            receipt_category="verified_done",
            changed_files=["a.py"],
            patch_bytes=30,
            patch_sha256="b",
            patch_file="two.patch",
        ),
    ]

    winner = select_verified_candidate(candidates)

    assert winner is not None
    assert winner.number == 2


def test_verified_tournament_disqualifies_failure_and_reverifies_winner(
    tmp_path: Path,
) -> None:
    root, commands = _project(tmp_path)

    result = run_verified_tournament(
        root,
        "Make value.txt contain good.",
        candidate_count=2,
        review_commands=commands,
        max_attempts=1,
    )

    assert result.status == "verified_done"
    assert result.winner == 2
    assert result.applied is True
    assert result.final_verification["passed"] is True
    assert root.joinpath("value.txt").read_text(encoding="utf-8") == "good\n"
    assert result.candidates[0].verified is False
    assert result.candidates[1].verified is True
    receipt = json.loads((root / result.receipt_path).read_text(encoding="utf-8"))
    assert receipt["contract"] == "agentic_harness.verified_tournament.v1"
    assert receipt["goal_spec_sha256"] == result.goal_spec_sha256
    assert receipt["goal_spec"]["sha256"] == result.goal_spec_sha256
    assert receipt["verification_commands"] == commands


def test_post_apply_failure_blocks_and_rolls_back_winner(tmp_path: Path) -> None:
    root, commands = _project(tmp_path, final_only_failure=True)

    result = run_verified_tournament(
        root,
        "Make value.txt contain good.",
        candidate_count=2,
        review_commands=commands,
        max_attempts=1,
    )

    assert result.status == "blocked"
    assert result.winner == 2
    assert result.applied is False
    assert result.final_verification["passed"] is False
    assert root.joinpath("value.txt").read_text(encoding="utf-8") == "original\n"
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all").strip() == ""


def test_all_failed_candidates_produce_no_winner_and_no_applied_change(
    tmp_path: Path,
) -> None:
    root, commands = _project(tmp_path, all_fail=True)

    result = run_verified_tournament(
        root,
        "Make value.txt contain good.",
        candidate_count=2,
        review_commands=commands,
        max_attempts=1,
    )

    assert result.status == "blocked"
    assert result.winner is None
    assert result.applied is False
    assert all(candidate.verified is False for candidate in result.candidates)
    assert root.joinpath("value.txt").read_text(encoding="utf-8") == "original\n"


def test_final_verification_exception_rolls_back_applied_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commands = _project(tmp_path)

    def fail_final_verification(*args: object, **kwargs: object) -> object:
        raise OSError("simulated final verifier outage")

    monkeypatch.setattr(
        tournament_module,
        "_run_final_verification",
        fail_final_verification,
    )

    result = run_verified_tournament(
        root,
        "Make value.txt contain good.",
        candidate_count=2,
        review_commands=commands,
        max_attempts=1,
    )

    assert result.status == "blocked"
    assert result.winner == 2
    assert result.applied is False
    assert "simulated final verifier outage" in result.reason
    assert root.joinpath("value.txt").read_text(encoding="utf-8") == "original\n"
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all").strip() == ""


def test_tampered_candidate_patch_is_blocked_before_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commands = _project(tmp_path)
    original_select = tournament_module.select_verified_candidate

    def tamper_after_selection(
        candidates: list[CandidateResult],
    ) -> CandidateResult | None:
        winner = original_select(candidates)
        assert winner is not None
        (root / winner.patch_file).write_text("tampered\n", encoding="utf-8")
        return winner

    monkeypatch.setattr(
        tournament_module,
        "select_verified_candidate",
        tamper_after_selection,
    )

    result = run_verified_tournament(
        root,
        "Make value.txt contain good.",
        candidate_count=2,
        review_commands=commands,
        max_attempts=1,
    )

    assert result.status == "blocked"
    assert result.applied is False
    assert "checksum" in result.reason
    assert root.joinpath("value.txt").read_text(encoding="utf-8") == "original\n"


def test_candidate_cannot_weaken_a_frozen_verifier_asset(tmp_path: Path) -> None:
    root, commands = _project(tmp_path, tamper_verifier=True)

    result = run_verified_tournament(
        root,
        "Make value.txt contain good.",
        candidate_count=2,
        review_commands=commands,
        max_attempts=1,
    )

    assert result.status == "blocked"
    assert result.winner is None
    assert result.applied is False
    assert result.candidates[1].receipt_category == "verified_done"
    assert result.candidates[1].verified is False
    assert result.candidates[1].verifier_asset_drift == ["check.py"]
    assert root.joinpath("check.py").read_text(encoding="utf-8").startswith("from pathlib")
    assert root.joinpath("value.txt").read_text(encoding="utf-8") == "original\n"


def test_tournament_refuses_existing_workspace_changes(tmp_path: Path) -> None:
    root, commands = _project(tmp_path)
    (root / "value.txt").write_text("user work\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="requires a clean workspace"):
        run_verified_tournament(
            root,
            "Make value.txt contain good.",
            candidate_count=2,
            review_commands=commands,
        )


def test_cli_best_of_n_returns_the_durable_verified_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _project(tmp_path)

    returncode = main(
        [
            "--project-dir",
            str(root),
            "best-of-n",
            "-n",
            "2",
            "--max-attempts",
            "1",
            "--json",
            "Make value.txt contain good.",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert returncode == 0
    assert payload["status"] == "verified_done"
    assert payload["winner"] == 2
    assert payload["applied"] is True
