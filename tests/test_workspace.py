from pathlib import Path

import pytest

import agentic_harness.core.workspace as workspace


def test_snapshot_skips_file_that_vanishes_during_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vanished = tmp_path / "atomic-write.tmp"
    retained = tmp_path / "retained.txt"

    monkeypatch.setattr(
        workspace,
        "_iter_workspace_files",
        lambda root: iter((vanished, retained)),
    )

    def fingerprint(path: Path) -> dict[str, object]:
        if path == vanished:
            raise FileNotFoundError(path)
        return {"size": 8, "mode": 0, "sha256": "retained"}

    monkeypatch.setattr(workspace, "_file_fingerprint", fingerprint)

    snapshot = workspace.capture_workspace_snapshot(tmp_path)

    assert snapshot["files"] == {
        "retained.txt": {"size": 8, "mode": 0, "sha256": "retained"}
    }
