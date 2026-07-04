from __future__ import annotations

import json
import subprocess
import sys

from agentic_harness import Goal, Supervisor, Worker
from agentic_harness.cli import main
from agentic_harness.core.config import load_config
from agentic_harness.core.errors import ConfigError


def test_init_creates_valid_project_config(tmp_path, capsys) -> None:
    rc = main(["--project-dir", str(tmp_path), "init"])

    assert rc == 0
    assert (tmp_path / ".agentic-harness" / "config.yml").exists()
    assert load_config(tmp_path).worker == "noop"
    assert "created" in capsys.readouterr().out


def test_config_parses_explicit_noop_success(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.worker == "noop"
    assert config.allow_noop_success is True


def test_config_rejects_unknown_keys(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: noop\nsurprise: true\n",
        encoding="utf-8",
    )

    try:
        load_config(tmp_path)
    except ConfigError as exc:
        assert "unknown config key" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_config_rejects_unsupported_version(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("version: 2\nworker: noop\n", encoding="utf-8")

    try:
        load_config(tmp_path)
    except ConfigError as exc:
        assert "unsupported config version" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_doctor_runs_without_crashing_on_empty_config(tmp_path, capsys) -> None:
    rc = main(["--project-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is False
    assert payload["checks"][1]["name"] == "config"


def test_cli_start_status_continue_review_round_trip(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
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
