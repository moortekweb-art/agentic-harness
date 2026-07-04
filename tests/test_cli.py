from __future__ import annotations

import json
import subprocess
import sys

from agentic_harness import Goal, Supervisor, Worker
from agentic_harness.cli import main
from agentic_harness.core.config import load_config


def test_init_creates_valid_project_config(tmp_path, capsys) -> None:
    rc = main(["--project-dir", str(tmp_path), "init"])

    assert rc == 0
    assert (tmp_path / ".agentic-harness" / "config.yml").exists()
    assert load_config(tmp_path).worker == "noop"
    assert "created" in capsys.readouterr().out


def test_doctor_runs_without_crashing_on_empty_config(tmp_path, capsys) -> None:
    rc = main(["--project-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is False
    assert payload["checks"][1]["name"] == "config"


def test_cli_start_status_continue_review_round_trip(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    capsys.readouterr()

    assert main(["--project-dir", str(tmp_path), "start", "ship cli"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["status"] == "planning"

    assert main(["--project-dir", str(tmp_path), "continue"]) == 0
    continued = json.loads(capsys.readouterr().out)
    assert continued["status"] == "review"

    assert main(["--project-dir", str(tmp_path), "review"]) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["status"] == "done"

    assert main(["--project-dir", str(tmp_path), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["id"] == started["id"]


def test_module_cli_init_works_in_temp_dir(tmp_path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_harness.cli",
            "--project-dir",
            str(tmp_path),
            "init",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert (tmp_path / ".agentic-harness" / "config.yml").exists()


def test_public_api_imports_cleanly() -> None:
    assert Goal is not None
    assert Supervisor is not None
    assert Worker is not None
