from __future__ import annotations

from pathlib import Path


STATIC = Path("agentic_harness/gui/static")


def test_gui_exposes_predictable_views_setup_progress_and_result_without_machine_specific_copy() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    for required in (
        'id="setupButton"',
        'id="homeTab"',
        'id="tasksTab"',
        'id="historyTab"',
        'id="homeView"',
        'id="tasksView"',
        'id="historyView"',
        'id="settingsView"',
        'id="executionChoice"',
        'id="providerEndpoint"',
        'id="providerModel"',
        'id="providerPreset"',
        'id="providerPresetHelp"',
        'id="providerApiKey" type="password"',
        'id="verificationCommand"',
        'id="saveSetupButton"',
        'id="connectionResult"',
        'id="workspacePath"',
        'id="demoCallout"',
        'id="demoButton"',
        'id="demoSetupButton"',
        'id="setupDemoButton"',
        'id="localModelDetection"',
        'id="detectedModelChoice"',
        'id="useDetectedModelButton"',
        'id="manualConnectionDetails"',
        'id="automaticCheckLabel"',
        'id="managedSettings"',
        'id="modeSection"',
        'id="modeSelect"',
        'id="modes"',
        'id="verificationDetails"',
        'id="verificationSummary"',
        'id="currentSubgoal"',
        'id="checkpoint"',
        'id="workApproachValue"',
        'id="planList"',
        'id="requirementsList"',
        'id="eventTimeline"',
        'id="finalResult"',
        'id="taskGuide"',
        'id="taskGuideTitle"',
        'id="taskGuideExplanation"',
        'id="taskGuideNext"',
        'id="completedDetails"',
        'id="continueDialog"',
        'id="continueFeedback"',
        'id="previewDialog"',
        'id="previewContent"',
        'id="resultOutput"',
        'id="resultOutputContent"',
        'id="resultOutputOpen"',
    ):
        assert required in html
    assert 'id="setupDialog"' not in html
    assert 'id="starterCreate"' not in html
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


def test_gui_offers_an_explicitly_scripted_first_success_before_real_setup() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert "No setup required" in html
    assert "It uses no AI model or API key" in html
    assert "Practice run" in html
    assert "never touches the project shown above" in html
    assert 'api("/api/demo"' in javascript
    assert 'api("/api/demo/dismiss"' in javascript
    assert 'api("/api/setup/local-models"' in javascript
    assert "setup.demo?.available !== true" in javascript
    assert "demo?.managed_overlay === true" in javascript
    assert "Return to real workspace" in javascript
    assert "document.body.dataset.demo = String(" in javascript
    assert "state.currentTask?.metadata?.demo?.enabled === true" in javascript
    assert 'workerClaim.label || "Scripted worker report (not AI)"' in javascript
    assert "(detected)" in javascript
    assert "template.key === previous" in javascript
    assert ".first-success-card" in styles
    assert ".local-model-detection" in styles


def test_gui_never_persists_provider_key_or_puts_gui_token_in_websocket_url() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'api("/api/setup"' in javascript
    assert 'api("/api/setup/credential"' in javascript
    assert 'api("/api/setup/test"' in javascript
    assert 'providerApiKey.value = ""' in javascript
    assert "api_key: apiKey" in javascript
    assert 'api_key_env: apiKey ? "" : els.providerApiKeyEnv.value.trim()' in javascript
    assert 'localStorage.setItem("api' not in javascript
    assert 'sessionStorage.setItem("api' not in javascript
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
    assert "function renderTaskGuide(task)" in javascript
    assert 'guide.title || "Your result is ready"' in javascript
    assert '"Ask for changes"' in javascript
    assert '"Approve and finish"' in javascript
    assert "/api/tasks/current/file?path=" in javascript
    assert "/api/tasks/current/artifact?path=" in javascript


def test_gui_keeps_durable_history_in_sync_with_live_status() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "refreshTask(), refreshHealth(), refreshHistory()" in javascript
    assert "const task = JSON.parse(event.data);" in javascript
    assert "state.liveTask = task;" in javascript
    assert "function taskMatchesPendingStart(task)" in javascript
    assert "adoptLiveTask(task);" in javascript
    assert "refreshHistory().catch(() => {});\n      refreshHealth().catch" in javascript


def test_gui_keeps_the_users_task_pinned_and_loads_its_readable_result() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    server = Path("agentic_harness/gui/server.py").read_text(encoding="utf-8")

    assert "FOREGROUND_TASK_KEY" in javascript
    assert "rememberForegroundTask(task)" in javascript
    assert "goal_id=${encodeURIComponent(pinned)}" in javascript
    assert "async function renderPrimaryResult(task)" in javascript
    assert 'els.resultOutputContent.textContent = "Loading the full result…"' in javascript
    assert 'foreground_metadata["foreground_task"] = True' in server
    assert "session.latest_foreground_task()" in server
    assert 'foreground_metadata["background_activity"]' in server
    assert 'foreground["allowed_actions"] = []' in server
    assert "task = public_managed_task(session.record(task))" in server


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
    assert 'socket.addEventListener("close", () => {' in javascript
    assert "if (state.socket === socket) state.socket = null" in javascript
    assert "schedulePolling();" in javascript
    assert 'window.addEventListener("pageshow", recoverVisibleSession)' in javascript
    assert 'window.addEventListener("online", recoverVisibleSession)' in javascript


def test_gui_status_stream_advances_managed_work_without_babysitting() -> None:
    server = Path("agentic_harness/gui/server.py").read_text(encoding="utf-8")

    assert "STREAM_MONITOR_INTERVAL_SECONDS = 8.0" in server
    assert "task = watch_task(bridge)" in server
    assert "next_monitor_at = time.monotonic() + STREAM_MONITOR_INTERVAL_SECONDS" in server


def test_gui_primary_form_uses_plain_language_and_progressive_disclosure() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    objective = html.index('id="objective"')
    modes = html.index('id="modes"')
    verification = html.index('id="checks"')
    access = html.index('class="boundaries"')
    safe_areas = html.index('id="safeAreas"')

    assert "What would you like done?" in html[:access]
    assert "Completion check · Automatic" in html[:access]
    assert "Work area · Entire project" in html
    assert "How should completion be checked?" in javascript
    assert 'id="manualConnectionDetails" class="manual-connection"' in html
    assert "function modePresentation(mode)" in javascript
    assert "function technicalModeLabel(option)" in javascript
    assert 'id="expectationHeading">What to expect' in html
    assert 'const DEFAULT_EXECUTION_EFFORT = "standard"' in javascript
    assert objective < modes < verification < access < safe_areas
    assert "Add scope and checks" not in html
    assert "Optional scope" not in html
    assert 'id="starterCreate"' not in html
    assert 'id="startHelp" role="status"' in html
    assert "You will review the result" in javascript
    assert "Automatic checks when possible" in javascript


def test_gui_explains_safe_review_pause_without_harness_jargon() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    api = Path("agentic_harness/gui/api.py").read_text(encoding="utf-8")

    for phrase in (
        "Work with the assistant",
        "Message the assistant",
    ):
        assert phrase in html
    for phrase in (
        "Your result is ready",
        "Review result",
        "Ask for changes",
        "Approve and finish",
        "Stop without approving",
    ):
        assert phrase in javascript
    assert "A quiet screen does not mean" in api
    assert "stopped safely at a review point" in api


def test_gui_explains_why_start_is_disabled() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'startHelp: byId("startHelp")' in javascript
    assert "No automatic project check was found. Add one in Settings" in javascript
    assert "Describe the outcome you want before starting." in javascript
    assert "Ready to start this verified task." in javascript
    assert "The assistant will choose checks and show the evidence" in javascript
    assert "route: usesHumanModes() ? state.route : undefined" in javascript
    assert "effort: usesHumanModes() ? state.effort : undefined" in javascript
    assert "strategy: usesHumanModes() ? undefined : state.effort" in javascript
    assert "mode: usesHumanModes() ? state.mode : undefined" not in javascript
    assert "resetNewGoalForm()" in javascript
    assert 'managedRouteSelection(state.routes, "", state.routeDefault)' in javascript
    assert "state.effort = state.effortDefault" in javascript
    assert "Choose Refresh to check the execution routes again." in javascript


def test_gui_compacts_mobile_intake_and_collapses_previous_evidence() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert '<select id="modeSelect" class="mode-select"' in html
    assert "View evidence from the previous task" in html
    assert "Assistant report" in html
    assert "els.completedDetails.hidden = !terminal" in javascript
    assert "els.artifactsEvidence.hidden = receipt.terminal" in javascript
    assert "#modes {\n    display: none;" in styles
    assert "Choose where it runs" in html
    assert ".advanced-mode-details {" in styles
    assert ".route-grid:not(.advanced-mode-grid),\n  .profile-grid {\n    display: none;" in styles
    assert ".mode-select,\n  .choice-select {\n    display: block;" in styles
    assert 'routeUnavailableReasons.id = "routeUnavailableReasons"' in javascript
    assert ".mobile-unavailable-reasons {" in styles
    assert ".mobile-unavailable-reasons[hidden] {" in styles
    assert ".primary-nav" in styles
    assert "flex: 1 1 0" in styles
    assert ".settings-summary" in styles
    assert ".goal-starter-grid" not in styles


def test_gui_guides_first_time_local_ai_setup() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'id="localModelGuide"' in html
    assert "New to local AI? Start with LM Studio." in html
    assert "https://lmstudio.ai/download" in html
    assert "open Developer and switch on Start server" in html
    assert "Agentic Harness will test the model before saving" in html
    assert "els.localModelGuide.hidden = found" in javascript


def test_gui_primary_actions_and_disabled_cursor_match_interaction_state() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert "Plan · Build · Verify" in html
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
