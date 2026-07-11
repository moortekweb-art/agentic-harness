from __future__ import annotations

from pathlib import Path


STATIC = Path("agentic_harness/gui/static")


def test_gui_exposes_setup_goal_progress_and_result_without_machine_specific_copy() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    for required in (
        'id="setupButton"',
        'id="setupDialog"',
        'id="executionChoice"',
        'id="providerEndpoint"',
        'id="providerModel"',
        'id="providerApiKey" type="password"',
        'id="verificationCommand"',
        'id="testConnectionButton"',
        'id="connectionResult"',
        'id="workspacePath"',
        'id="currentSubgoal"',
        'id="checkpoint"',
        'id="planList"',
        'id="requirementsList"',
        'id="eventTimeline"',
        'id="finalResult"',
        'id="continueDialog"',
        'id="continueFeedback"',
        'id="previewDialog"',
        'id="previewContent"',
    ):
        assert required in html
    for machine_specific in (
        "GLM",
        "Jarvis",
        "Hermes",
        "Node1",
        "Mode 3A",
        "Readiness gate",
        "Local loop",
        "Perceive",
    ):
        assert machine_specific.lower() not in html.lower()


def test_gui_never_persists_provider_key_or_puts_gui_token_in_websocket_url() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'api("/api/setup"' in javascript
    assert 'api("/api/setup/credential"' in javascript
    assert 'api("/api/setup/test"' in javascript
    assert "providerApiKey.value = \"\"" in javascript
    assert "api_key: els.providerApiKey.value" in javascript
    assert "localStorage.setItem(\"api" not in javascript
    assert "sessionStorage.setItem(\"api" not in javascript
    assert "encodeURIComponent(token)" not in javascript
    assert "tokenQuery" not in javascript


def test_gui_renders_backend_supplied_actions_and_measured_progress() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "task.allowed_actions" in javascript
    assert "progress.determinate" in javascript
    assert "progress.percent" in javascript
    assert "task.current.current_subgoal" in javascript
    assert "task.current.checkpoint" in javascript
    assert "task.events" in javascript
    assert "task.final_result" in javascript
    assert "window.confirm" in javascript
    assert "continueFeedback" in javascript
    assert "/api/tasks/current/file?path=" in javascript
    assert "/api/tasks/current/artifact?path=" in javascript


def test_gui_keeps_durable_history_in_sync_with_live_status() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "refreshTask(), refreshHealth(), refreshHistory()" in javascript
    assert "const task = JSON.parse(event.data);" in javascript
    assert "state.liveTask = task;" in javascript
    assert "if (!state.viewingHistoryId) renderTask(task);" in javascript
    assert "refreshHistory().catch(() => {});\n      refreshHealth().catch" in javascript
