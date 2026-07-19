"""Fail-closed best-of-N execution across isolated Git worktrees."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable
from uuid import uuid4

from agentic_harness.core.autonomy import AutonomousRunner
from agentic_harness.core.config import CONFIG_DIR, CONFIG_NAME, HarnessConfig, load_config
from agentic_harness.core.errors import ConfigError, HarnessError
from agentic_harness.core.factory import autonomy_policy_from_config, build_supervisor
from agentic_harness.core.goal_spec import GoalSpec, derived_objective_spec
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.reporting import build_run_receipt
from agentic_harness.core.review import ReviewResult
from agentic_harness.core.safety import goal_safety_metadata, subprocess_environment
from agentic_harness.core.state import Goal
from agentic_harness.core.verifier_manifest import (
    freeze_verifier_assets,
    harden_python_module_commands,
    require_lexical_regular_path,
    verifier_asset_drift,
)
from agentic_harness.core.workspace_transaction import (
    recover_interrupted_tournament as _recover_interrupted_tournament,
    rollback_patch as _rollback_patch,
    workspace_fingerprint as _workspace_fingerprint,
    write_private_bytes as _write_private_bytes,
)


TOURNAMENT_CONTRACT = "agentic_harness.verified_tournament.v1"
MIN_CANDIDATES = 2
MAX_CANDIDATES = 10


def recover_interrupted_tournament(
    project_dir: str | Path,
    receipt_path: str | Path,
) -> tuple[bool, str]:
    return _recover_interrupted_tournament(
        project_dir,
        receipt_path,
        contract=TOURNAMENT_CONTRACT,
    )


@dataclass
class CandidateResult:
    """Durable result for one isolated implementation candidate."""

    number: int
    verified: bool = False
    receipt_category: str = "failed"
    goal_id: str = ""
    goal_spec_sha256: str = ""
    changed_files: list[str] = field(default_factory=list)
    patch_bytes: int = 0
    patch_sha256: str = ""
    patch_file: str = ""
    verifier_asset_drift: list[str] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TournamentResult:
    """Public, serializable tournament receipt."""

    tournament_id: str
    objective: str
    base_commit: str
    goal_spec_sha256: str
    candidate_count: int
    goal_spec: dict[str, Any] = field(default_factory=dict)
    verification_commands: list[list[str]] = field(default_factory=list)
    verifier_assets: list[dict[str, str]] = field(default_factory=list)
    status: str = "running"
    reason: str = ""
    winner: int | None = None
    selection_policy: str = "smallest_verified_patch"
    applied: bool = False
    transaction_phase: str = "collecting_candidates"
    base_workspace_sha256: str = ""
    expected_workspace_sha256: str = ""
    final_verification: dict[str, Any] = field(default_factory=dict)
    candidates: list[CandidateResult] = field(default_factory=list)
    receipt_path: str = ""
    created_at: str = field(default_factory=lambda: _now_iso())
    completed_at: str = ""
    contract: str = TOURNAMENT_CONTRACT

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> TournamentResult:
        if not isinstance(payload, dict):
            raise ValueError("tournament receipt must be an object")
        raw_candidates = payload.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise ValueError("tournament candidates must be a list")
        candidates = [
            CandidateResult(
                **{
                    key: value
                    for key, value in candidate.items()
                    if key in CandidateResult.__dataclass_fields__
                }
            )
            for candidate in raw_candidates
            if isinstance(candidate, dict)
        ]
        values = {
            key: value
            for key, value in payload.items()
            if key in cls.__dataclass_fields__ and key != "candidates"
        }
        return cls(**values, candidates=candidates)


def load_verified_tournament_result(
    project_dir: str | Path,
    receipt_path: str | Path,
) -> TournamentResult:
    """Load a durable verified result only when the applied workspace still matches."""

    root = Path(project_dir).resolve()
    receipt = Path(receipt_path)
    if not receipt.is_absolute():
        receipt = root / receipt
    require_lexical_regular_path(root, receipt, label=str(receipt_path))
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"verified tournament receipt could not be read: {exc}") from exc
    try:
        result = TournamentResult.from_dict(payload)
        frozen_spec = GoalSpec.from_dict(result.goal_spec)
    except (TypeError, ValueError) as exc:
        raise HarnessError(f"verified tournament receipt is invalid: {exc}") from exc
    if result.contract != TOURNAMENT_CONTRACT:
        raise HarnessError("verified tournament receipt contract is not supported")
    if not (
        result.status == "verified_done"
        and result.transaction_phase == "verified"
        and result.applied
    ):
        raise HarnessError("tournament receipt does not contain a durable verified result")
    if frozen_spec.sha256 != result.goal_spec_sha256:
        raise HarnessError("verified tournament GoalSpec checksum does not match")
    expected_receipt = receipt.relative_to(root).as_posix()
    if result.receipt_path != expected_receipt:
        raise HarnessError("verified tournament receipt identity does not match its path")
    if len(result.candidates) != result.candidate_count:
        raise HarnessError("verified tournament candidate set is incomplete")
    winner = next(
        (candidate for candidate in result.candidates if candidate.number == result.winner),
        None,
    )
    if winner is None or not (
        winner.verified
        and winner.receipt_category == "verified_done"
        and winner.patch_sha256
        and winner.patch_file
    ):
        raise HarnessError("verified tournament receipt has no eligible winner")
    patch_path = root / winner.patch_file
    require_lexical_regular_path(root, patch_path, label=winner.patch_file)
    if patch_path.parent != receipt.parent:
        raise HarnessError("verified winner patch is outside its tournament receipt directory")
    try:
        patch = patch_path.read_bytes()
    except OSError as exc:
        raise HarnessError(f"verified winner patch could not be read: {exc}") from exc
    if hashlib.sha256(patch).hexdigest() != winner.patch_sha256:
        raise HarnessError("verified winner patch checksum does not match")
    if _git(root, "rev-parse", "HEAD").strip() != result.base_commit:
        raise HarnessError("workspace commit changed after verified tournament application")
    if _workspace_fingerprint(root) != result.expected_workspace_sha256:
        raise HarnessError("workspace no longer matches the durably verified result")
    final = result.final_verification
    if not isinstance(final, dict):
        raise HarnessError("verified tournament final review is missing")
    raw_criteria = final.get("criteria")
    review = ReviewResult(
        passed=final.get("passed") is True,
        criteria=(
            [dict(item) for item in raw_criteria if isinstance(item, dict)]
            if isinstance(raw_criteria, list)
            else []
        ),
    )
    if not _review_proves_spec(review, frozen_spec):
        raise HarnessError("verified tournament final review does not prove the GoalSpec")
    return result


def run_verified_tournament(
    project_dir: str | Path,
    objective: str,
    *,
    candidate_count: int = 3,
    review_commands: list[list[str]],
    max_attempts: int = 3,
    allowed_paths: list[str] | None = None,
    api_key: str | None = None,
    frozen_spec: GoalSpec | None = None,
    review_assets: list[str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    progress_callback: Callable[[TournamentResult], None] | None = None,
) -> TournamentResult:
    """Run candidates concurrently and apply only an independently verified winner.

    Every candidate starts from the same Git commit, receives the exact same immutable
    GoalSpec, and is reviewed using the same configured deterministic checks.  A winner
    is selected only from verified candidates, and its patch is checked again in the
    original workspace before the tournament can report success.
    """

    root = Path(project_dir).resolve()
    normalized_objective = objective.strip()
    if not normalized_objective:
        raise ConfigError("best-of-N objective must not be empty")
    if not MIN_CANDIDATES <= candidate_count <= MAX_CANDIDATES:
        raise ConfigError(
            f"candidate count must be between {MIN_CANDIDATES} and {MAX_CANDIDATES}"
        )
    if max_attempts < 1:
        raise ConfigError("--max-attempts must be at least 1")
    if not review_commands:
        raise ConfigError("verified best-of-N requires at least one independent check")

    _require_clean_git_root(root)
    base_commit = _git(root, "rev-parse", "HEAD").strip()
    config = load_config(root)
    if config.assurance_mode == "high_assurance":
        raise ConfigError(
            "verified best-of-N does not silently approve high-assurance specifications; "
            "use specification_frozen or approve a future tournament specification workflow"
        )
    goal_spec = frozen_spec or derived_objective_spec(normalized_objective)
    if goal_spec.objective != normalized_objective:
        raise ConfigError("the frozen tournament specification does not match the objective")
    effective_review_assets = (
        config.review_assets
        if review_assets is None
        and bool(config.review_command)
        and review_commands == [config.review_command]
        else (review_assets or [])
    )
    review_commands = harden_python_module_commands(review_commands)
    verifier_assets = _freeze_verifier_assets(
        root,
        review_commands,
        review_assets=effective_review_assets,
    )
    tournament_id = f"tournament-{uuid4().hex}"
    receipt_dir = root / CONFIG_DIR / "tournaments" / tournament_id
    receipt_dir.mkdir(parents=True, exist_ok=False)
    try:
        receipt_dir.chmod(0o700)
    except OSError:
        pass
    result = TournamentResult(
        tournament_id=tournament_id,
        objective=normalized_objective,
        base_commit=base_commit,
        goal_spec_sha256=goal_spec.sha256,
        candidate_count=candidate_count,
        goal_spec=goal_spec.to_dict(),
        verification_commands=[
            [redact_secrets(argument) for argument in command]
            for command in review_commands
        ],
        verifier_assets=verifier_assets,
        base_workspace_sha256=_workspace_fingerprint(root),
        receipt_path=(receipt_dir / "receipt.json").relative_to(root).as_posix(),
    )
    _write_receipt(receipt_dir, result)
    _notify_progress(progress_callback, result)

    if cancel_requested is not None and cancel_requested():
        result.status = "stopped"
        result.reason = "tournament stopped before candidates were started"
        result.completed_at = _now_iso()
        _write_receipt(receipt_dir, result)
        _notify_progress(progress_callback, result)
        return result

    worktree_parent = Path(tempfile.mkdtemp(prefix=f"{tournament_id}-"))
    worktrees: dict[int, Path] = {}
    verification_worktree: Path | None = None
    try:
        for number in range(1, candidate_count + 1):
            worktree = worktree_parent / f"candidate-{number}"
            _git(root, "worktree", "add", "--detach", str(worktree), base_commit)
            worktrees[number] = worktree
            _copy_runtime_config(root, worktree)

        candidate_results: dict[int, CandidateResult] = {}
        with ThreadPoolExecutor(max_workers=candidate_count) as executor:
            futures = {
                executor.submit(
                    _run_candidate,
                    root,
                    worktree,
                    number,
                    candidate_count,
                    base_commit,
                    goal_spec,
                    config,
                    review_commands,
                    verifier_assets,
                    max_attempts,
                    allowed_paths or [],
                    receipt_dir,
                    api_key,
                    cancel_requested,
                ): number
                for number, worktree in worktrees.items()
            }
            for future in as_completed(futures):
                number = futures[future]
                try:
                    candidate_results[number] = future.result()
                except Exception as exc:  # fail one candidate without losing the receipt
                    candidate_results[number] = CandidateResult(
                        number=number,
                        goal_spec_sha256=goal_spec.sha256,
                        error=redact_secrets(
                            f"candidate execution failed: {type(exc).__name__}: {exc}"
                        ),
                    )
                result.candidates = [
                    candidate_results[index]
                    for index in sorted(candidate_results)
                ]
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)

        result.candidates = [candidate_results[index] for index in sorted(candidate_results)]
        if cancel_requested is not None and cancel_requested():
            result.status = "stopped"
            result.reason = "tournament stopped before a winner was applied"
            result.completed_at = _now_iso()
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            return result
        winner = select_verified_candidate(result.candidates)
        if winner is None:
            result.status = "blocked"
            result.reason = "no candidate passed independent verification with a non-empty patch"
            result.completed_at = _now_iso()
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            return result

        result.winner = winner.number
        patch_path = root / winner.patch_file
        patch = patch_path.read_bytes()
        if hashlib.sha256(patch).hexdigest() != winner.patch_sha256:
            result.status = "blocked"
            result.reason = "selected candidate patch no longer matches its recorded checksum"
            result.completed_at = _now_iso()
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            return result
        _require_unchanged_workspace(root, base_commit)
        if cancel_requested is not None and cancel_requested():
            result.status = "stopped"
            result.reason = "tournament stopped before the winning patch was applied"
            result.completed_at = _now_iso()
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            return result
        try:
            verification_worktree = worktree_parent / "final-verification"
            _git(root, "worktree", "add", "--detach", str(verification_worktree), base_commit)
            _copy_runtime_config(root, verification_worktree)
            _git_bytes(
                verification_worktree,
                ["apply", "--check", "--binary", "-"],
                input_bytes=patch,
            )
            _git_bytes(
                verification_worktree,
                ["apply", "--binary", "-"],
                input_bytes=patch,
            )
            final_asset_drift = _verifier_asset_drift(
                verification_worktree, verifier_assets
            )
            if final_asset_drift:
                result.status = "blocked"
                result.reason = (
                    "selected candidate changed frozen verifier assets: "
                    + ", ".join(final_asset_drift[:10])
                )
                result.completed_at = _now_iso()
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)
                return result
            result.expected_workspace_sha256 = _workspace_fingerprint(
                verification_worktree
            )
            _write_receipt(receipt_dir, result)
            final_review = _run_final_verification(
                verification_worktree,
                review_commands,
                goal_spec,
                api_key=api_key,
            )
            result.final_verification = final_review.to_dict()
            if (
                _workspace_fingerprint(verification_worktree)
                != result.expected_workspace_sha256
            ):
                result.status = "blocked"
                result.reason = (
                    "final verification modified the verified workspace; "
                    "no winner was applied"
                )
                result.completed_at = _now_iso()
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)
                return result
            if cancel_requested is not None and cancel_requested():
                result.status = "stopped"
                result.reason = "tournament stopped before the winner could be accepted"
                result.completed_at = _now_iso()
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)
                return result
            if not _review_proves_spec(final_review, goal_spec):
                result.status = "blocked"
                result.reason = "winner failed final verification; no patch was applied"
                result.completed_at = _now_iso()
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)
                return result

            result.transaction_phase = "verified_staged"
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            _require_unchanged_workspace(root, base_commit)
            if cancel_requested is not None and cancel_requested():
                result.status = "stopped"
                result.reason = "tournament stopped before the verified winner was applied"
                result.completed_at = _now_iso()
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)
                return result
            _git_bytes(root, ["apply", "--check", "--binary", "-"], input_bytes=patch)
            result.transaction_phase = "applying_verified"
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            _git_bytes(root, ["apply", "--binary", "-"], input_bytes=patch)
            if _workspace_fingerprint(root) != result.expected_workspace_sha256:
                rollback_error = _rollback_patch(root, patch)
                result.transaction_phase = "rollback_failed" if rollback_error else "rolled_back"
                result.status = "blocked"
                result.reason = "applied workspace does not match the independently verified state"
                if rollback_error:
                    result.reason += f"; automatic rollback failed: {rollback_error}"
                result.completed_at = _now_iso()
                _write_receipt(receipt_dir, result)
                _notify_progress(progress_callback, result)
                return result
            result.applied = True

            result.status = "verified_done"
            result.transaction_phase = "verified"
            result.reason = (
                f"candidate {winner.number} of {candidate_count} passed the frozen checks; "
                "its exact, side-effect-free verified state was applied to the original workspace"
            )
            result.completed_at = _now_iso()
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            return result
        except Exception as exc:
            rollback_error = ""
            if result.transaction_phase == "applying_verified":
                try:
                    current_fingerprint = _workspace_fingerprint(root)
                except (HarnessError, OSError) as fingerprint_error:
                    current_fingerprint = ""
                    rollback_error = (
                        "workspace could not be fingerprinted before rollback: "
                        f"{fingerprint_error}"
                    )
                if current_fingerprint == result.expected_workspace_sha256:
                    rollback_error = _rollback_patch(root, patch)
                elif current_fingerprint and current_fingerprint != result.base_workspace_sha256:
                    rollback_error = "workspace state diverged before automatic rollback"
            result.applied = False
            result.transaction_phase = "rollback_failed" if rollback_error else "rolled_back"
            result.status = "blocked"
            result.reason = redact_secrets(
                "winner could not be durably verified after application: "
                f"{type(exc).__name__}: {exc}"
            )
            if rollback_error:
                result.reason += f"; automatic rollback failed: {rollback_error}"
            result.completed_at = _now_iso()
            _write_receipt(receipt_dir, result)
            _notify_progress(progress_callback, result)
            return result
    except Exception as exc:
        result.status = "blocked"
        result.reason = redact_secrets(
            f"tournament orchestration failed: {type(exc).__name__}: {exc}"
        )
        result.completed_at = _now_iso()
        _write_receipt(receipt_dir, result)
        _notify_progress(progress_callback, result)
        return result
    finally:
        if verification_worktree is not None:
            _remove_worktree(root, verification_worktree)
        for worktree in worktrees.values():
            _remove_worktree(root, worktree)
        shutil.rmtree(worktree_parent, ignore_errors=True)


def select_verified_candidate(
    candidates: list[CandidateResult],
) -> CandidateResult | None:
    """Choose deterministically from verified candidates; never choose a failing result."""

    eligible = [
        candidate
        for candidate in candidates
        if candidate.verified
        and candidate.receipt_category == "verified_done"
        and candidate.patch_bytes > 0
        and candidate.patch_sha256
        and candidate.patch_file
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda candidate: (
            len(candidate.changed_files),
            candidate.patch_bytes,
            candidate.number,
        ),
    )


def _notify_progress(
    callback: Callable[[TournamentResult], None] | None,
    result: TournamentResult,
) -> None:
    """Publish a detached snapshot without letting presentation break execution."""

    if callback is None:
        return
    try:
        callback(deepcopy(result))
    except Exception:
        return


def _run_candidate(
    root: Path,
    worktree: Path,
    number: int,
    candidate_count: int,
    base_commit: str,
    frozen_spec: GoalSpec,
    config: HarnessConfig,
    review_commands: list[list[str]],
    verifier_assets: list[dict[str, str]],
    max_attempts: int,
    allowed_paths: list[str],
    receipt_dir: Path,
    api_key: str | None,
    cancel_requested: Callable[[], bool] | None,
) -> CandidateResult:
    supervisor = build_supervisor(
        worktree,
        review_commands=review_commands,
        api_key=api_key,
        cancel_requested=cancel_requested,
    )
    metadata = goal_safety_metadata(
        worktree,
        allowed_paths=allowed_paths,
        review_commands=review_commands,
        path_enforcement=config.worker == "model_agent",
        secret_env_names=[config.llm_api_key_env],
        interface="verified_best_of_n",
    )
    metadata["tournament"] = {
        "contract": TOURNAMENT_CONTRACT,
        "candidate": number,
        "candidate_count": candidate_count,
        "base_commit": base_commit,
        "goal_spec_sha256": frozen_spec.sha256,
    }
    metadata["execution_strategy"] = {
        "key": "verified_candidate",
        "instruction": (
            f"You are implementation candidate {number}. Explore a distinct, minimal approach. "
            "Do not weaken or replace the frozen checks."
        ),
    }
    goal = supervisor.start(frozen_spec.objective, metadata=metadata)
    with supervisor.store.locked():
        supervisor.store.write_goal_spec(goal, frozen_spec)
    policy = autonomy_policy_from_config(
        config,
        repeated_blocker_limit=max_attempts,
        require_completion_claim=True,
    )
    goal = AutonomousRunner(
        supervisor,
        policy=policy,
        cancel_requested=cancel_requested,
    ).run()
    receipt = build_run_receipt(goal)
    patch, changed_files = _candidate_patch(worktree, base_commit)
    verifier_asset_drift = _verifier_asset_drift(worktree, verifier_assets)
    patch_file = ""
    patch_sha256 = ""
    if patch:
        patch_name = f"candidate-{number}.patch"
        patch_path = receipt_dir / patch_name
        _write_private_bytes(patch_path, patch)
        patch_file = patch_path.relative_to(root).as_posix()
        patch_sha256 = hashlib.sha256(patch).hexdigest()
    verified = (
        receipt.category == "verified_done"
        and bool(patch)
        and not verifier_asset_drift
    )
    error = ""
    if receipt.category != "verified_done":
        error = receipt.trusted_reason
    elif verifier_asset_drift:
        error = "candidate changed frozen verifier assets: " + ", ".join(
            verifier_asset_drift
        )
    elif not patch:
        error = "independent checks passed but the candidate produced no project patch"
    return CandidateResult(
        number=number,
        verified=verified,
        receipt_category=receipt.category,
        goal_id=goal.id,
        goal_spec_sha256=frozen_spec.sha256,
        changed_files=changed_files,
        patch_bytes=len(patch),
        patch_sha256=patch_sha256,
        patch_file=patch_file,
        verifier_asset_drift=verifier_asset_drift,
        review=dict(goal.review) if isinstance(goal.review, dict) else {},
        error=error,
    )


def _run_final_verification(
    root: Path,
    review_commands: list[list[str]],
    frozen_spec: GoalSpec,
    *,
    api_key: str | None,
) -> ReviewResult:
    supervisor = build_supervisor(root, review_commands=review_commands, api_key=api_key)
    goal = Goal(
        objective=frozen_spec.objective,
        metadata={
            "worker_success": True,
            "worker_run_id": "tournament-final-verification",
            "autonomy": {
                "goal_spec_sha256": frozen_spec.sha256,
                "goal_spec_requirement_ids": [item.id for item in frozen_spec.requirements],
            },
        },
    )
    return supervisor.reviewer.review(goal)


def _review_proves_spec(review: ReviewResult, frozen_spec: GoalSpec) -> bool:
    if not review.passed or not review.criteria:
        return False
    required = {item.id for item in frozen_spec.requirements}
    covered: set[str] = set()
    has_independent = False
    for criterion in review.criteria:
        if criterion.get("passed") is not True:
            return False
        if criterion.get("goal_spec_sha256") != frozen_spec.sha256:
            return False
        if criterion.get("independent") is True:
            has_independent = True
            covers = criterion.get("covers")
            if isinstance(covers, list):
                covered.update(str(item) for item in covers)
    return has_independent and required <= covered


def _candidate_patch(worktree: Path, base_commit: str) -> tuple[bytes, list[str]]:
    # Runtime evidence is ignored by the project; stage all non-ignored project changes so
    # new files are included without ever forcing .agentic-harness into a candidate patch.
    _git(worktree, "add", "-A", "--", ".")
    names = _git(
        worktree,
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        base_commit,
        "--",
        ":/",
        ":(exclude).agentic-harness",
    )
    patch = _git_bytes(
        worktree,
        [
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            base_commit,
            "--",
            ":/",
            ":(exclude).agentic-harness",
        ],
    )
    return patch, [line for line in names.splitlines() if line.strip()]


def _freeze_verifier_assets(
    root: Path,
    review_commands: list[list[str]],
    *,
    review_assets: list[str] | None = None,
) -> list[dict[str, str]]:
    tracked_paths = {
        relative
        for relative in _git(root, "ls-files", "-z").split("\0")
        if relative
    }
    return freeze_verifier_assets(
        root,
        review_commands,
        review_assets=review_assets,
        tracked_paths=tracked_paths,
    )


_verifier_asset_drift = verifier_asset_drift
_require_lexical_regular_path = require_lexical_regular_path


def _require_clean_git_root(root: Path) -> None:
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root:
        raise ConfigError(f"verified best-of-N must run from the Git root: {top}")
    changes = _meaningful_status(root)
    if changes:
        raise ConfigError(
            "verified best-of-N requires a clean workspace so candidate patches cannot "
            "overwrite existing work: " + ", ".join(changes[:10])
        )


def _require_unchanged_workspace(root: Path, base_commit: str) -> None:
    if _git(root, "rev-parse", "HEAD").strip() != base_commit:
        raise HarnessError("the original workspace commit changed during the tournament")
    changes = _meaningful_status(root)
    if changes:
        raise HarnessError(
            "the original workspace changed during the tournament: " + ", ".join(changes[:10])
        )


def _meaningful_status(root: Path) -> list[str]:
    raw = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return [
        line
        for line in raw.splitlines()
        if line.strip() and not line[3:].replace("\\", "/").startswith(f"{CONFIG_DIR}/")
    ]


def _copy_runtime_config(root: Path, worktree: Path) -> None:
    source = root / CONFIG_DIR / CONFIG_NAME
    if not source.is_file() or source.is_symlink():
        raise ConfigError("verified best-of-N requires a regular project config.yml")
    target = worktree / CONFIG_DIR / CONFIG_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def _remove_worktree(root: Path, worktree: Path) -> None:
    try:
        _git(root, "worktree", "remove", "--force", str(worktree))
    except (HarnessError, OSError):
        try:
            _git(root, "worktree", "prune")
        except (HarnessError, OSError):
            return


def _write_receipt(receipt_dir: Path, result: TournamentResult) -> None:
    encoded = (json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_private_bytes(receipt_dir / "receipt.json", encoded)


def _git(root: Path, *arguments: str) -> str:
    return _git_bytes(root, list(arguments)).decode("utf-8", errors="replace")


def _git_bytes(
    root: Path,
    arguments: list[str],
    *,
    input_bytes: bytes | None = None,
) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=subprocess_environment(),
            input=input_bytes,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(f"git {' '.join(arguments)} failed: {exc}") from exc
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        raise HarnessError(
            f"git {' '.join(arguments)} failed with exit code {proc.returncode}: {message}"
        )
    return proc.stdout


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
