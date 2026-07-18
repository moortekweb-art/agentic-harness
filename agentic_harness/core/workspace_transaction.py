"""Durable workspace fingerprints, patch rollback, and interrupted recovery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from agentic_harness.core.config import CONFIG_DIR
from agentic_harness.core.errors import ConfigError, HarnessError
from agentic_harness.core.safety import subprocess_environment
from agentic_harness.core.verifier_manifest import require_lexical_regular_path


def workspace_fingerprint(root: Path) -> str:
    """Hash tracked and non-ignored project state while excluding runtime data."""

    digest = hashlib.sha256()
    names = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for relative in sorted(name for name in names.split("\0") if name):
        if relative == CONFIG_DIR or relative.startswith(f"{CONFIG_DIR}/"):
            continue
        path = root / relative
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(b"missing\0")
            continue
        digest.update(oct(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
        digest.update(b"\0")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            digest.update(b"unsupported\0")
    return digest.hexdigest()


def rollback_patch(root: Path, patch: bytes) -> str:
    try:
        _git_bytes(root, ["apply", "--check", "--reverse", "--binary", "-"], patch)
        _git_bytes(root, ["apply", "--reverse", "--binary", "-"], patch)
        return ""
    except (HarnessError, OSError) as exc:
        return str(exc)


def recover_interrupted_tournament(
    project_dir: str | Path,
    receipt_path: str | Path,
    *,
    contract: str,
) -> tuple[bool, str]:
    """Restore the clean preimage after interrupted verified patch application."""

    root = Path(project_dir).resolve()
    receipt = Path(receipt_path)
    if not receipt.is_absolute():
        receipt = root / receipt
    try:
        require_lexical_regular_path(root, receipt, label=str(receipt_path))
    except ConfigError as exc:
        return False, str(exc)
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"tournament receipt could not be read safely: {exc}"
    if payload.get("contract") != contract:
        return False, "tournament receipt contract is not supported"
    if payload.get("transaction_phase") != "applying_verified":
        return False, "tournament did not stop during verified patch application"
    if _git(root, "rev-parse", "HEAD").strip() != payload.get("base_commit"):
        return False, "workspace commit changed after the interrupted tournament"

    candidates = payload.get("candidates")
    winner = payload.get("winner")
    selected = (
        next(
            (
                item
                for item in candidates
                if isinstance(item, dict) and item.get("number") == winner
            ),
            None,
        )
        if isinstance(candidates, list)
        else None
    )
    if not isinstance(selected, dict):
        return False, "interrupted tournament receipt has no selected candidate"
    patch_path = root / str(selected.get("patch_file") or "")
    try:
        require_lexical_regular_path(root, patch_path, label=str(patch_path))
    except ConfigError as exc:
        return False, str(exc)
    try:
        patch = patch_path.read_bytes()
    except OSError as exc:
        return False, f"selected patch could not be read safely: {exc}"
    if hashlib.sha256(patch).hexdigest() != selected.get("patch_sha256"):
        return False, "selected patch checksum does not match the interrupted receipt"

    current = workspace_fingerprint(root)
    base = str(payload.get("base_workspace_sha256") or "")
    expected = str(payload.get("expected_workspace_sha256") or "")
    if current == base:
        restored = True
    elif current == expected:
        rollback_error = rollback_patch(root, patch)
        if rollback_error:
            return False, f"automatic recovery rollback failed: {rollback_error}"
        restored = workspace_fingerprint(root) == base
    else:
        return False, "workspace diverged from both the preimage and verified patch state"
    if not restored:
        return False, "automatic recovery could not prove the clean preimage was restored"

    payload.update(
        {
            "applied": False,
            "status": "blocked",
            "transaction_phase": "recovered_rolled_back",
            "reason": "interrupted verified patch application was restored to its clean preimage",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_private_bytes(
        receipt,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return True, "interrupted verified patch application was restored to its clean preimage"


def write_private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            Path(temporary).unlink()
        except OSError:
            pass
        raise


def _git(root: Path, *arguments: str) -> str:
    return _git_bytes(root, list(arguments)).decode("utf-8", errors="replace")


def _git_bytes(root: Path, arguments: list[str], input_bytes: bytes | None = None) -> bytes:
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
