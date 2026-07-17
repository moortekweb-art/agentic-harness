from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agentic_harness.gui.api import modes_payload


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "agentic_harness" / "gui" / "static"


def test_v011_plain_language_and_guided_setup_contract() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    for phrase in (
        "Build or improve",
        "Fix a problem",
        "Review safely",
        "Long-running task",
        "Work area · Entire project",
        "Completion check · Automatic",
        "Connect, test, then start",
        "How should the assistant run?",
    ):
        assert phrase in html
    assert "friendly_name" in javascript
    assert "technical_label" in javascript


def test_v011_generated_assets_are_small_packaged_and_have_provenance() -> None:
    assets = (
        STATIC / "illustrations" / "local-ai-connection.webp",
        STATIC / "illustrations" / "verified-archive.webp",
        STATIC / "illustrations" / "setup-recovery.webp",
    )
    assert all(asset.is_file() for asset in assets)
    assert sum(asset.stat().st_size for asset in assets) < 600_000
    provenance = (ROOT / "docs" / "IMG2IMG_ASSET_PROVENANCE.md").read_text(encoding="utf-8")
    assert all(asset.name in provenance for asset in assets)
    package_data = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"static/illustrations/*"' in package_data


def test_managed_routes_expose_additive_product_metadata() -> None:
    routes = modes_payload()
    assert routes
    for route in routes:
        assert route["friendly_name"]
        assert route["short_purpose"]
        assert route["location_label"]
        assert route["availability_state"] == "unavailable"
        assert route["technical_label"]
        assert isinstance(route["capabilities"], list)


def test_selftest_uses_active_interpreter_not_path_python() -> None:
    source = (ROOT / "agentic_harness" / "cli.py").read_text(encoding="utf-8")
    start = source.index("def run_selftest()")
    end = source.index("\ndef run_release_smoke", start)
    implementation = source[start:end]
    assert '[sys.executable, "-c", "print(\'worker ok\')"]' in implementation
    assert json.dumps(["python", "-c"]) not in implementation


def test_selftest_runs_when_parent_path_has_no_python(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path / "empty-path")
    completed = subprocess.run(
        [sys.executable, "-m", "agentic_harness.cli", "selftest"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Selftest: passed" in completed.stdout
