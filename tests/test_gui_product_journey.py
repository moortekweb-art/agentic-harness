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
        'id="starterCreate"',
        'id="starterFix"',
        'id="starterAudit"',
        'id="starterDocument"',
        'id="modeSection"',
        'id="modeSelect"',
        'id="modes"',
        'id="verificationDetails"',
        'id="verificationSummary"',
        'id="currentSubgoal"',
        'id="checkpoint"',
        'id="planList"',
        'id="requirementsList"',
        'id="eventTimeline"',
        'id="finalResult"',
        'id="completedDetails"',
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


def test_gui_recovers_from_slow_requests_and_failed_status_streams() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "const API_TIMEOUT_MS = 20000" in javascript
    assert "new AbortController()" in javascript
    assert "controller.abort()" in javascript
    assert "The server took too long to respond" in javascript
    assert 'singleFlight("task"' in javascript
    assert 'singleFlight("health"' in javascript
    assert 'singleFlight("history"' in javascript
    assert "function stopPolling()" in javascript
    assert 'socket.addEventListener("close", () => {\n    schedulePolling();' in javascript


def test_gui_status_stream_advances_managed_work_without_babysitting() -> None:
    server = Path("agentic_harness/gui/server.py").read_text(encoding="utf-8")

    assert "STREAM_MONITOR_INTERVAL_SECONDS = 8.0" in server
    assert "task = watch_task(bridge)" in server
    assert "next_monitor_at = time.monotonic() + STREAM_MONITOR_INTERVAL_SECONDS" in server


def test_gui_primary_form_puts_mode_and_verification_before_optional_scope() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    objective = html.index('id="objective"')
    modes = html.index('id="modes"')
    verification = html.index('id="checks"')
    optional_scope = html.index('class="boundaries"')
    safe_areas = html.index('id="safeAreas"')

    assert "Describe the result you want" in html[:optional_scope]
    assert "Verification command for this goal" in html[:optional_scope]
    assert "Pre-filled from Setup. Edit it here to override the default for this run." in html
    assert "Default verification command for this workspace" in html
    assert "No special prompt format is required" in html
    assert "What kind of help do you need?" in html
    assert "Optional: add your own success check" in javascript
    assert "question that only needs an answer" in html
    assert objective < modes < verification < optional_scope < safe_areas
    assert "Add scope and checks" not in html
    assert 'id="startHelp" role="status"' in html


def test_gui_explains_why_start_is_disabled() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'startHelp: byId("startHelp")' in javascript
    assert "Add the verification command that will prove this goal is complete" in javascript
    assert "Describe the outcome you want before starting." in javascript
    assert "Ready to start this verified goal." in javascript
    assert "The assistant will choose checks and show the evidence" in javascript
    assert "mode: state.mode" in javascript
    assert "resetNewGoalForm()" in javascript
    assert 'state.mode = "guided"' in javascript


def test_gui_compacts_mobile_intake_and_collapses_previous_evidence() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert '<select id="modeSelect" class="mode-select"' in html
    assert "View evidence from the previous goal" in html
    assert "Assistant report" in html
    assert "els.completedDetails.hidden = !terminal" in javascript
    assert "els.artifactsEvidence.hidden = receipt.terminal" in javascript
    assert ".mode-grid {\n    display: none;" in styles
    assert ".mode-select {\n    display: block;" in styles
    assert ".goal-starter-grid" in styles


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
