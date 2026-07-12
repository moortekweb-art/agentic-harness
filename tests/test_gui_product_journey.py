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
    assert "els.currentCard.hidden = receipt.terminal" in javascript
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


def test_gui_primary_form_puts_required_verification_before_optional_scope() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    objective = html.index('id="objective"')
    verification = html.index('id="checks"')
    optional_scope = html.index('class="boundaries"')
    safe_areas = html.index('id="safeAreas"')

    assert "Objective" in html[:optional_scope]
    assert "Verification command for this goal" in html[:optional_scope]
    assert "Pre-filled from Setup. Edit it here to override the default for this run." in html
    assert "Default verification command for this workspace" in html
    assert objective < verification < optional_scope < safe_areas
    assert "Add scope and checks" not in html


def test_gui_primary_actions_and_disabled_cursor_match_interaction_state() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert "Coding-agent work, independently verified" in html
    assert 'id="startButton"' in html
    assert 'id="checkButton"' in html
    assert 'id="undoButton"' not in html
    assert 'id="redoButton"' not in html
    assert "Ctrl Z</dt><dd>Undo form edit" in html
    assert "Ctrl Shift Z</dt><dd>Redo form edit" in html
    assert 'button.setAttribute("aria-busy", String(busy))' in javascript
    assert "cursor: not-allowed" in styles
    assert 'button[aria-busy="true"]:disabled' in styles
    assert "cursor: progress" in styles


def test_gui_receipt_card_uses_attempts_and_trusted_terminal_fields() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "attemptsValue",
        "finalLabel",
        "finalReason",
        "finalWorkerClaimLabel",
        "finalWorkerClaim",
        "finalAttempts",
        "finalRetries",
        "finalChangedFiles",
        "finalVerification",
        "workDetailGrid",
        "activitySection",
        "changedFilesEvidence",
        "verificationEvidence",
        "artifactsEvidence",
    ):
        assert f'id="{element_id}"' in html
    assert ">Cycle<" not in html
    assert "task.result_category" in javascript
    assert "final.label" in javascript
    assert "final.reason" in javascript
    assert "final.worker_claim" in javascript
    assert "final.review_attempts" in javascript
    assert 'row.source || (row.independent ? "independent" : "worker-reported")' in javascript


def test_gui_form_draft_is_session_only() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "sessionStorage.setItem(STORAGE_KEY" in javascript
    assert "sessionStorage.getItem(STORAGE_KEY" in javascript
    assert "sessionStorage.removeItem(STORAGE_KEY" in javascript
    assert "localStorage.setItem(STORAGE_KEY" not in javascript
    assert "localStorage.getItem(STORAGE_KEY" not in javascript
