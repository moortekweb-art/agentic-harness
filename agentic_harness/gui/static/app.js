const STORAGE_KEY = "agentic-harness-gui-form";
const THEME_KEY = "agentic-harness-theme";
const TOKEN_KEY = "agentic-harness-gui-session-token";
const TOKEN_PARAM = "token";
const ICON_PREFIX = "#icon-";
const API_TIMEOUT_MS = 20000;

const STATUS_ICONS = Object.freeze({
  ready: "circle-check",
  starting: "loader-circle",
  working: "loader-circle",
  checking: "loader-circle",
  stopping: "loader-circle",
  needs_review: "circle-alert",
  done: "circle-check",
  blocked: "octagon-alert",
  stopped: "circle-stop",
});

const state = {
  busy: false,
  authToken: "",
  authPromptPromise: null,
  setupPrompted: false,
  readiness: {},
  setup: null,
  currentTask: null,
  liveTask: null,
  viewingHistoryId: "",
  undoStack: [],
  redoStack: [],
  socket: null,
  pollTimer: null,
  reconnectDelay: 1000,
  refreshes: {},
};

const byId = (id) => document.getElementById(id);
const els = {
  health: byId("health"),
  healthText: byId("healthText"),
  healthIcon: byId("healthIcon"),
  setupButton: byId("setupButton"),
  themeButton: byId("themeButton"),
  themeIcon: byId("themeIcon"),
  shortcutsButton: byId("shortcutsButton"),
  workspacePath: byId("workspacePath"),
  executionSummary: byId("executionSummary"),
  objective: byId("objective"),
  safeAreas: byId("safeAreas"),
  checks: byId("checks"),
  startButton: byId("startButton"),
  startHelp: byId("startHelp"),
  checkButton: byId("checkButton"),
  statusLabel: byId("statusLabel"),
  statusIndicator: byId("statusIndicator"),
  statusIcon: byId("statusIcon"),
  summary: byId("summary"),
  progressGroup: byId("progressGroup"),
  progressTrack: byId("progressTrack"),
  progressValue: byId("progressValue"),
  progressBar: byId("progressBar"),
  currentSubgoal: byId("currentSubgoal"),
  checkpoint: byId("checkpoint"),
  attemptsValue: byId("attemptsValue"),
  currentCard: byId("currentCard"),
  continueButton: byId("continueButton"),
  acceptButton: byId("acceptButton"),
  stopButton: byId("stopButton"),
  planList: byId("planList"),
  requirementsList: byId("requirementsList"),
  eventTimeline: byId("eventTimeline"),
  finalResult: byId("finalResult"),
  finalLabel: byId("finalLabel"),
  finalReason: byId("finalReason"),
  finalWorkerClaimLabel: byId("finalWorkerClaimLabel"),
  finalWorkerClaim: byId("finalWorkerClaim"),
  finalAttempts: byId("finalAttempts"),
  finalRetries: byId("finalRetries"),
  finalChangedFiles: byId("finalChangedFiles"),
  finalVerification: byId("finalVerification"),
  finalRemaining: byId("finalRemaining"),
  workDetailGrid: byId("workDetailGrid"),
  activitySection: byId("activitySection"),
  changedFilesEvidence: byId("changedFilesEvidence"),
  verificationEvidence: byId("verificationEvidence"),
  artifactsEvidence: byId("artifactsEvidence"),
  changedFiles: byId("changedFiles"),
  verification: byId("verification"),
  artifacts: byId("artifacts"),
  historySearch: byId("historySearch"),
  historyList: byId("historyList"),
  exportButton: byId("exportButton"),
  exportButtonLabel: byId("exportButtonLabel"),
  advancedDetails: byId("advancedDetails"),
  statusUpdated: byId("statusUpdated"),
  shortcutsDialog: byId("shortcutsDialog"),
  setupDialog: byId("setupDialog"),
  setupForm: byId("setupForm"),
  closeSetupButton: byId("closeSetupButton"),
  executionChoice: byId("executionChoice"),
  codingAgentFields: byId("codingAgentFields"),
  codingAgentChoice: byId("codingAgentChoice"),
  providerFields: byId("providerFields"),
  providerEndpoint: byId("providerEndpoint"),
  providerModel: byId("providerModel"),
  providerApiKeyEnv: byId("providerApiKeyEnv"),
  providerApiKey: byId("providerApiKey"),
  testConnectionButton: byId("testConnectionButton"),
  connectionResult: byId("connectionResult"),
  remoteDataRow: byId("remoteDataRow"),
  confirmRemoteData: byId("confirmRemoteData"),
  verificationCommand: byId("verificationCommand"),
  maxCycles: byId("maxCycles"),
  maxMinutes: byId("maxMinutes"),
  maxTokens: byId("maxTokens"),
  maxProviderCalls: byId("maxProviderCalls"),
  maxToolCalls: byId("maxToolCalls"),
  setupError: byId("setupError"),
  continueDialog: byId("continueDialog"),
  continueForm: byId("continueForm"),
  closeContinueButton: byId("closeContinueButton"),
  continueFeedback: byId("continueFeedback"),
  previewDialog: byId("previewDialog"),
  previewTitle: byId("previewTitle"),
  previewContent: byId("previewContent"),
};

function iconHref(name) {
  return `${ICON_PREFIX}${name}`;
}

function iconMarkup(name) {
  return `<svg class="icon" aria-hidden="true"><use href="${iconHref(name)}"></use></svg>`;
}

function captureTokenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get(TOKEN_PARAM) || "";
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
    params.delete(TOKEN_PARAM);
    const query = params.toString();
    history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
  }
  state.authToken = token || sessionStorage.getItem(TOKEN_KEY) || "";
}

function clearAuthToken() {
  state.authToken = "";
  sessionStorage.removeItem(TOKEN_KEY);
}

function showTokenDialog() {
  if (state.authPromptPromise) return state.authPromptPromise;
  state.authPromptPromise = new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "token-dialog";
    dialog.innerHTML = `
      <form method="dialog">
        <div class="dialog-head">
          <h2>Access token required</h2>
          <button value="cancel" title="Cancel">${iconMarkup("x")}<span>Cancel</span></button>
        </div>
        <label class="field-label" for="authTokenInput">Token</label>
        <input id="authTokenInput" name="authTokenInput" type="password" autocomplete="off" />
        <div class="actions compact">
          <button class="primary" value="confirm">${iconMarkup("arrow-right")}<span>Continue</span></button>
        </div>
      </form>
    `;
    document.body.appendChild(dialog);
    const input = dialog.querySelector("input");
    dialog.addEventListener("close", () => {
      const value = dialog.returnValue === "confirm" && input ? input.value.trim() : "";
      dialog.remove();
      state.authPromptPromise = null;
      resolve(value);
    });
    dialog.showModal();
    if (input) input.focus();
  });
  return state.authPromptPromise;
}

async function api(path, options = {}, retry = true) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (state.authToken) headers.set("Authorization", `Bearer ${state.authToken}`);
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timeout = controller
    ? window.setTimeout(() => controller.abort(), API_TIMEOUT_MS)
    : null;
  let response;
  try {
    response = await fetch(path, { ...options, headers, signal: controller?.signal });
  } catch (error) {
    if (controller?.signal.aborted) {
      throw new Error("The server took too long to respond. Try Refresh; your task state is preserved.");
    }
    throw error;
  } finally {
    if (timeout !== null) window.clearTimeout(timeout);
  }
  if (response.status === 401 && retry) {
    clearAuthToken();
    const entered = await showTokenDialog();
    if (entered) {
      state.authToken = entered;
      sessionStorage.setItem(TOKEN_KEY, entered);
      return api(path, options, false);
    }
  }
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) {
    throw new Error(
      payload.error || (response.status === 401 ? "Authorization required." : `Request failed (${response.status})`),
    );
  }
  return payload;
}

function linesFrom(field) {
  return field.value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function formSnapshot() {
  return {
    objective: els.objective.value,
    safeAreas: els.safeAreas.value,
    checks: els.checks.value,
  };
}

function applyFormSnapshot(snapshot) {
  els.objective.value = snapshot.objective || "";
  els.safeAreas.value = snapshot.safeAreas || "";
  els.checks.value = snapshot.checks || "";
  updateStartButton();
}

function pushUndo() {
  const snapshot = JSON.stringify(formSnapshot());
  if (state.undoStack.at(-1) !== snapshot) state.undoStack.push(snapshot);
  state.undoStack = state.undoStack.slice(-50);
  state.redoStack = [];
}

function undoForm() {
  if (state.undoStack.length < 2) return;
  state.redoStack.push(state.undoStack.pop());
  applyFormSnapshot(JSON.parse(state.undoStack.at(-1)));
  persistForm();
}

function redoForm() {
  const snapshot = state.redoStack.pop();
  if (!snapshot) return;
  state.undoStack.push(snapshot);
  applyFormSnapshot(JSON.parse(snapshot));
  persistForm();
}

function persistForm() {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(formSnapshot()));
}

function restoreForm() {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (raw) {
    try { applyFormSnapshot(JSON.parse(raw)); } catch { sessionStorage.removeItem(STORAGE_KEY); }
  }
  pushUndo();
}

function setBusy(busy) {
  state.busy = busy;
  updateStartButton();
  [els.startButton, els.checkButton, els.continueButton, els.acceptButton, els.stopButton].forEach((button) => {
    button.setAttribute("aria-busy", String(busy));
  });
  [els.checkButton, els.continueButton, els.acceptButton, els.stopButton].forEach((button) => {
    button.disabled = busy;
  });
}

function updateStartButton() {
  const canStart = state.readiness.can_start === true;
  const hasObjective = Boolean(els.objective.value.trim());
  const hasVerification = Boolean(els.checks.value.trim());
  els.startButton.disabled = state.busy || !canStart || !hasObjective || !hasVerification;
  if (state.busy) {
    els.startHelp.textContent = "Starting the goal. This can take a few seconds.";
  } else if (!canStart) {
    els.startHelp.textContent = state.readiness.next_action
      || state.readiness.summary
      || "Waiting for the current task state to become ready.";
  } else if (!hasObjective) {
    els.startHelp.textContent = "Describe the outcome you want before starting.";
  } else if (!hasVerification) {
    els.startHelp.textContent = "Add the verification command that will prove this goal is complete to enable Start.";
  } else {
    els.startHelp.textContent = "Ready to start this verified goal.";
  }
}

function renderHealth(health) {
  state.readiness = health.readiness || {};
  const ready = state.readiness.can_start === true;
  const needsSetup = ["setup_required", "credential_required", "verification_required"]
    .includes(state.readiness.state);
  const label = ready ? "Ready" : needsSetup ? "Setup needed" : "Task active";
  els.healthText.textContent = label;
  els.health.className = ready ? "health ok" : needsSetup ? "health blocked" : "health";
  els.healthIcon.setAttribute("href", iconHref(ready ? "shield-check" : needsSetup ? "octagon-alert" : "loader-circle"));
  els.health.setAttribute("aria-label", label);
  els.health.title = state.readiness.summary || label;
  updateStartButton();
}

function textList(element, rows, formatter, emptyText) {
  element.replaceChildren();
  if (!Array.isArray(rows) || rows.length === 0) {
    const item = document.createElement("li");
    item.className = "empty-item";
    item.textContent = emptyText;
    element.append(item);
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("li");
    const formatted = formatter(row);
    item.textContent = formatted.text;
    if (formatted.className) item.className = formatted.className;
    element.append(item);
  });
}

function previewList(element, rows, kind, emptyText, goalId = "") {
  element.replaceChildren();
  if (!Array.isArray(rows) || rows.length === 0) {
    const item = document.createElement("li");
    item.className = "empty-item";
    item.textContent = emptyText;
    element.append(item);
    return;
  }
  rows.forEach((row) => {
    const path = typeof row === "string" ? row : row.path || "";
    const label = typeof row === "string"
      ? row
      : kind === "file"
        ? `${row.status || "changed"}: ${path}`
        : row.name || path || "Artifact";
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "evidence-link";
    button.textContent = label;
    button.disabled = !path;
    button.addEventListener("click", () => openPreview(kind, path, goalId));
    item.append(button);
    element.append(item);
  });
}

async function openPreview(kind, path, goalId = "") {
  const baseRoute = kind === "file"
    ? `/api/tasks/current/file?path=${encodeURIComponent(path)}`
    : `/api/tasks/current/artifact?path=${encodeURIComponent(path)}`;
  const route = goalId
    ? `${baseRoute}&goal_id=${encodeURIComponent(goalId)}`
    : baseRoute;
  try {
    const preview = await api(route);
    els.previewTitle.textContent = preview.path || "Evidence preview";
    els.previewContent.textContent = preview.content || "";
    els.previewDialog.showModal();
  } catch (error) {
    window.alert(error instanceof Error ? error.message : String(error));
  }
}

function normalizeProgress(task) {
  if (task.progress && typeof task.progress === "object") return task.progress;
  if (Number.isFinite(task.progress)) return { determinate: true, percent: task.progress };
  return { determinate: false, percent: null };
}

function hasAction(task, name) {
  return Array.isArray(task.allowed_actions)
    && task.allowed_actions.some((row) => row && row.action === name && row.enabled !== false);
}

function receiptContext(task) {
  const final = task.final_result && typeof task.final_result === "object"
    ? task.final_result
    : {};
  const category = task.result_category || "in_progress";
  return {
    category,
    final,
    terminal: ["verified_done", "blocked", "failed"].includes(category),
  };
}

function verificationSource(row) {
  return row.source || (row.independent ? "independent" : "worker-reported");
}

function independentReviewRows(final) {
  const rows = [];
  (Array.isArray(final.review_attempts) ? final.review_attempts : []).forEach((attempt) => {
    (Array.isArray(attempt.checks) ? attempt.checks : []).forEach((check) => {
      const source = verificationSource(check);
      if (source !== "independent") return;
      rows.push({
        attempt: attempt.number || 1,
        source,
        passed: check.passed === true,
        message: check.message || check.name || attempt.summary || "Verification recorded",
      });
    });
  });
  if (rows.length) return rows;
  (Array.isArray(final.checks) ? final.checks : []).forEach((check) => {
    if (!check || typeof check !== "object") return;
    const source = verificationSource(check);
    if (source !== "independent") return;
    rows.push({
      attempt: 1,
      source,
      passed: check.passed === true,
      message: check.message || check.name || "Verification recorded",
    });
  });
  return rows;
}

function renderFinalReceipt(task, receipt) {
  const { final, terminal, category } = receipt;
  els.finalResult.hidden = !terminal;
  els.finalResult.className = `final-result ${category}`;
  els.finalLabel.textContent = terminal ? final.label || "Result" : "Result";
  els.finalReason.textContent = terminal ? final.reason || final.summary || "" : "";
  const workerClaim = final.worker_claim && typeof final.worker_claim === "object"
    ? final.worker_claim
    : {};
  els.finalWorkerClaimLabel.textContent = workerClaim.label || "Worker claim (untrusted)";
  els.finalWorkerClaim.textContent = workerClaim.summary || "No worker completion claim recorded.";
  els.finalAttempts.textContent = String(Number.isFinite(final.attempts) ? final.attempts : 0);
  els.finalRetries.textContent = String(Number.isFinite(final.retries) ? final.retries : 0);
  const changedEvidence = final.what_changed_evidence && typeof final.what_changed_evidence === "object"
    ? final.what_changed_evidence
    : task.changed_files_evidence || {};
  const noChangedFiles = changedEvidence.available === false && changedEvidence.reason
    ? changedEvidence.reason
    : "No workspace changes recorded.";
  previewList(
    els.finalChangedFiles,
    Array.isArray(final.what_changed) ? final.what_changed : task.changed_files,
    "file",
    noChangedFiles,
    task.id || "",
  );
  const verificationCommands = Array.isArray(final.verification_commands)
    ? final.verification_commands
    : [];
  const verificationRows = [
    ...verificationCommands.map((command, index) => ({ command, index })),
    ...independentReviewRows(final),
  ];
  textList(els.finalVerification, verificationRows, (row) => (
    row.command
      ? {
        text: `Command ${row.index + 1}: ${row.command}`,
        className: "command",
      }
      : {
        text: `Attempt ${row.attempt} · ${row.source} · ${row.passed ? "Passed" : "Failed"}: ${row.message}`,
        className: row.passed ? "passed" : "failed",
      }
  ), "No independent verification evidence recorded.");
  const remaining = Array.isArray(final.remaining) ? final.remaining : [];
  els.finalRemaining.textContent = remaining.length
    ? `Still open: ${remaining.join("; ")}`
    : "Nothing remains open.";
}

function renderTask(task) {
  state.currentTask = task;
  const status = task.status || "ready";
  const receipt = receiptContext(task);
  const rawDoneUnverified = status === "done" && !receipt.terminal;
  const visualStatus = rawDoneUnverified
    ? "checking"
    : receipt.category === "verified_done"
    ? "done"
    : receipt.category === "failed"
      ? "stopped"
      : receipt.category === "blocked"
        ? "blocked"
        : status;
  document.body.dataset.taskActive = String(
    ["starting", "working", "checking", "stopping", "needs_review", "blocked"].includes(status),
  );
  els.statusLabel.textContent = receipt.terminal && receipt.final.label
    ? receipt.final.label
    : rawDoneUnverified
      ? "Checking evidence"
      : task.status_label || status.replaceAll("_", " ");
  els.summary.textContent = receipt.terminal
    ? receipt.final.reason || receipt.final.summary || "No trusted result reason was recorded."
    : rawDoneUnverified
      ? "Completion is not verified yet."
      : task.summary || "No task is running.";
  els.statusIndicator.className = `status-indicator ${visualStatus}`;
  els.statusIcon.setAttribute("href", iconHref(STATUS_ICONS[visualStatus] || "loader-circle"));
  els.statusIndicator.setAttribute("aria-label", els.statusLabel.textContent);
  els.statusIndicator.title = els.statusLabel.textContent;

  const progress = normalizeProgress(task);
  const percent = Number(progress.percent);
  els.progressGroup.hidden = !(progress.determinate && Number.isFinite(percent));
  if (!els.progressGroup.hidden) {
    const bounded = Math.max(0, Math.min(100, percent));
    els.progressValue.textContent = `${bounded}%`;
    els.progressBar.style.width = `${bounded}%`;
    els.progressTrack.setAttribute("aria-valuenow", String(bounded));
  }

  const current = task.current && typeof task.current === "object" ? task.current : {};
  els.currentSubgoal.textContent = task.current && task.current.current_subgoal
    ? task.current.current_subgoal
    : "Waiting for the next step";
  els.checkpoint.textContent = task.current && task.current.checkpoint
    ? task.current.checkpoint.replaceAll("_", " ")
    : "Not started";
  els.attemptsValue.textContent = String(
    Number.isFinite(receipt.final.attempts) ? receipt.final.attempts : current.cycle || 0,
  );

  textList(els.planList, task.plan, (row) => ({
    text: `${row.status || "pending"}: ${row.step || row.text || "Plan item"}`,
    className: String(row.status || "pending").toLowerCase(),
  }), "The plan will appear after the first model or agent response.");
  textList(els.requirementsList, task.requirements, (row) => ({
    text: `${row.status || "pending"}: ${row.text || row.id || "Requirement"}`,
    className: String(row.status || "pending").toLowerCase(),
  }), "Requirements will appear as the goal is understood.");
  textList(els.eventTimeline, task.events, (row) => ({
    text: `${row.summary || "Progress recorded"}${row.checkpoint ? ` — ${row.checkpoint}` : ""}`,
    className: row.stage || "act",
  }), "No tool or check events recorded yet.");
  previewList(
    els.changedFiles,
    task.changed_files,
    "file",
    "No workspace changes reported yet.",
    task.id || "",
  );
  textList(els.verification, task.verification, (row) => ({
    text: typeof row === "string"
      ? row
      : `${verificationSource(row) === "independent" ? "Independent" : "Worker-reported"} · ${row.passed ? "Passed" : "Failed"}: ${row.message || row.name || "Check"}`,
    className: typeof row === "object" && row.passed ? "passed" : "failed",
  }), "No verification evidence reported yet.");
  previewList(
    els.artifacts,
    task.artifacts,
    "artifact",
    "No artifacts reported yet.",
    task.id || "",
  );

  renderFinalReceipt(task, receipt);
  els.currentCard.hidden = receipt.terminal;
  els.workDetailGrid.hidden = receipt.terminal;
  els.activitySection.hidden = receipt.terminal;
  els.changedFilesEvidence.hidden = receipt.terminal;
  els.verificationEvidence.hidden = receipt.terminal;
  els.artifactsEvidence.hidden = false;

  const viewingHistory = Boolean(state.viewingHistoryId);
  els.continueButton.hidden = viewingHistory || !hasAction(task, "continue");
  els.acceptButton.hidden = viewingHistory || !hasAction(task, "accept");
  els.stopButton.hidden = viewingHistory || !hasAction(task, "stop");
  els.advancedDetails.textContent = JSON.stringify({
    id: task.id || "",
    contract: task.contract || "",
    safety: task.safety || {},
    metadata: task.metadata || {},
  }, null, 2);
  renderStatusFooter(task);
}

function renderStatusFooter(task) {
  const current = task.current && typeof task.current === "object" ? task.current : {};
  const metadata = task.metadata && typeof task.metadata === "object" ? task.metadata : {};
  const value = current.last_event_at || metadata.updated_at;
  if (!value) {
    els.statusUpdated.textContent = "No progress recorded yet";
    return;
  }
  const timestamp = new Date(value);
  els.statusUpdated.textContent = Number.isNaN(timestamp.getTime())
    ? "Progress time unavailable"
    : `Last meaningful update ${timestamp.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}

function renderHistory(tasks) {
  els.historyList.replaceChildren();
  (tasks || []).forEach((task) => {
    const receipt = receiptContext(task);
    const rawDoneUnverified = task.status === "done" && !receipt.terminal;
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-entry";
    const label = receipt.terminal && receipt.final.label
      ? receipt.final.label
      : rawDoneUnverified
        ? "Checking evidence"
        : task.status_label || task.status;
    button.textContent = `${label}: ${task.objective || task.summary || task.id}`;
    button.addEventListener("click", () => {
      if (state.liveTask && state.liveTask.id === task.id) {
        state.viewingHistoryId = "";
        renderTask(state.liveTask);
        return;
      }
      state.viewingHistoryId = task.id;
      renderTask(task);
    });
    item.append(button);
    els.historyList.append(item);
  });
}

function renderDetectedAgents(setup, worker) {
  const options = Array.isArray(setup.execution_options) ? setup.execution_options : [];
  const codingAgent = options.find((option) => option && option.key === "coding_agent");
  const agents = codingAgent && Array.isArray(codingAgent.agents) ? codingAgent.agents : [];
  if (!agents.length) return;

  const selected = setup.configured && worker.agent
    ? worker.agent
    : codingAgent.recommended_agent || agents.find((agent) => agent.available)?.key || "";
  els.codingAgentChoice.replaceChildren();
  agents.forEach((agent) => {
    const option = document.createElement("option");
    option.value = agent.key;
    option.disabled = agent.available !== true;
    option.selected = agent.key === selected;
    option.textContent = agent.available
      ? `${agent.label}${agent.recommended ? " (recommended)" : ""}`
      : `${agent.label} (not found)`;
    els.codingAgentChoice.append(option);
  });
  if (selected) els.codingAgentChoice.value = selected;
  if (!setup.configured && codingAgent.recommended) els.executionChoice.value = "coding_agent";
}

function renderSetup(setup) {
  const previousSetup = state.setup;
  state.setup = setup;
  els.setupButton.hidden = setup.editable === false;
  els.workspacePath.textContent = setup.workspace || "Unknown workspace";
  const worker = setup.worker || {};
  renderDetectedAgents(setup, worker);
  els.executionSummary.textContent = setup.configured
    ? worker.type === "model_agent"
      ? `${worker.model || "Model"} · ${worker.credential_source || "no key"}`
      : worker.label || worker.type || "Configured"
    : "Setup required";
  const previousCheck = previousSetup
    ? previousSetup.verification_command || previousSetup.suggested_check || ""
    : "";
  const effectiveCheck = setup.verification_command || setup.suggested_check || "";
  if (!els.verificationCommand.value.trim() || els.verificationCommand.value === previousCheck) {
    els.verificationCommand.value = effectiveCheck;
  }
  if (!els.checks.value.trim() || els.checks.value === previousCheck) {
    els.checks.value = effectiveCheck;
  }
  if (setup.provider) {
    els.providerEndpoint.value = setup.provider.endpoint || "";
    els.providerModel.value = setup.provider.model || "";
    els.providerApiKeyEnv.value = setup.provider.api_key_env || "";
    els.executionChoice.value = setup.provider.data_location === "cloud" ? "cloud_model" : "local_model";
  } else if (worker.type === "coding_agent") {
    els.executionChoice.value = "coding_agent";
  }
  if (setup.limits) {
    els.maxCycles.value = String(setup.limits.max_cycles || 100);
    els.maxMinutes.value = String(
      Math.max(1, Math.round((setup.limits.max_elapsed_seconds || 7200) / 60)),
    );
    els.maxTokens.value = String(setup.limits.max_total_tokens || 500000);
    els.maxProviderCalls.value = String(setup.limits.max_provider_calls || 200);
    els.maxToolCalls.value = String(setup.limits.max_tool_calls || 1000);
  }
  updateSetupFields();
  updateStartButton();
  if (
    setup.configured === false
    && setup.editable !== false
    && !state.setupPrompted
    && !els.setupDialog.open
  ) {
    state.setupPrompted = true;
    els.setupDialog.showModal();
  }
}

function updateSetupFields() {
  const execution = els.executionChoice.value;
  const model = execution !== "coding_agent";
  els.providerFields.hidden = !model;
  els.codingAgentFields.hidden = model;
  els.remoteDataRow.hidden = execution !== "cloud_model";
}

async function refreshHealth() {
  return singleFlight("health", async () => {
    renderHealth(await api("/api/health"));
  });
}

async function refreshSetup() {
  renderSetup(await api("/api/setup"));
}

async function refreshTask(force = false) {
  return singleFlight("task", async () => {
    const task = await api("/api/tasks/current");
    state.liveTask = task;
    if (force || !state.viewingHistoryId) {
      state.viewingHistoryId = "";
      renderTask(task);
    }
  });
}

async function refreshHistory() {
  return singleFlight("history", async () => {
    const query = encodeURIComponent(els.historySearch.value.trim());
    const payload = await api(`/api/tasks/history${query ? `?q=${query}` : ""}`);
    renderHistory(payload.tasks || []);
  });
}

function singleFlight(key, operation) {
  if (state.refreshes[key]) return state.refreshes[key];
  const pending = Promise.resolve().then(operation);
  const tracked = pending.finally(() => {
    if (state.refreshes[key] === tracked) delete state.refreshes[key];
  });
  state.refreshes[key] = tracked;
  return tracked;
}

async function startWork() {
  if (els.startButton.disabled) return;
  await runAction(async () => {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        objective: els.objective.value.trim(),
        safe_areas: linesFrom(els.safeAreas),
        checks: linesFrom(els.checks),
      }),
    });
    state.viewingHistoryId = "";
    state.liveTask = task;
    renderTask(task);
    await refreshHistory();
  });
}

async function postAction(path, body = {}) {
  await runAction(async () => {
    const task = await api(path, { method: "POST", body: JSON.stringify(body) });
    state.viewingHistoryId = "";
    state.liveTask = task;
    renderTask(task);
    await refreshHistory();
  });
}

async function saveSetup(event) {
  event.preventDefault();
  els.setupError.textContent = "";
  const apiKey = els.providerApiKey.value;
  const payload = {
    execution: els.executionChoice.value,
    agent: els.codingAgentChoice.value,
    endpoint: els.providerEndpoint.value.trim(),
    model: els.providerModel.value.trim(),
    api_key_env: els.providerApiKeyEnv.value.trim(),
    api_key: els.providerApiKey.value,
    confirm_remote_data: els.confirmRemoteData.checked,
    verification_command: els.verificationCommand.value.trim(),
    max_cycles: Number(els.maxCycles.value),
    max_elapsed_seconds: Number(els.maxMinutes.value) * 60,
    max_total_tokens: Number(els.maxTokens.value),
    max_provider_calls: Number(els.maxProviderCalls.value),
    max_tool_calls: Number(els.maxToolCalls.value),
  };
  try {
    const configured = await api("/api/setup", { method: "POST", body: JSON.stringify(payload) });
    if (apiKey && configured.credential && configured.credential.source === "session") {
      await api("/api/setup/credential", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey }),
      });
    }
    els.providerApiKey.value = "";
    await Promise.all([refreshSetup(), refreshHealth()]);
    els.setupDialog.close();
  } catch (error) {
    els.providerApiKey.value = "";
    els.setupError.textContent = error instanceof Error ? error.message : String(error);
  }
}

async function testConnection() {
  const apiKey = els.providerApiKey.value;
  els.connectionResult.textContent = "Testing…";
  try {
    const result = await api("/api/setup/test", {
      method: "POST",
      body: JSON.stringify({
        endpoint: els.providerEndpoint.value.trim(),
        model: els.providerModel.value.trim(),
        api_key_env: els.providerApiKeyEnv.value.trim(),
        api_key: apiKey,
      }),
    });
    els.connectionResult.textContent = result.structured_actions
      ? "Connected; structured actions work."
      : "Connected, but autonomous actions are unavailable.";
  } catch (error) {
    els.connectionResult.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    els.providerApiKey.value = "";
  }
}

async function runAction(action) {
  if (state.busy) return;
  setBusy(true);
  try {
    await action();
    await refreshHealth();
  } catch (error) {
    els.summary.textContent = error instanceof Error ? error.message : "The request failed.";
    els.statusLabel.textContent = "Needs attention";
  } finally {
    setBusy(false);
  }
}

function schedulePolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(() => {
    Promise.all([refreshTask(), refreshHealth(), refreshHistory()]).catch(() => {});
  }, 2000);
}

function stopPolling() {
  if (!state.pollTimer) return;
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function connectStatusStream() {
  if (state.authToken || !("WebSocket" in window)) {
    schedulePolling();
    return;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${window.location.host}/api/tasks/stream`);
  state.socket = socket;
  socket.addEventListener("open", () => {
    state.reconnectDelay = 1000;
    stopPolling();
  });
  socket.addEventListener("message", (event) => {
    try {
      const task = JSON.parse(event.data);
      state.liveTask = task;
      if (!state.viewingHistoryId) renderTask(task);
      refreshHistory().catch(() => {});
      refreshHealth().catch(() => {});
    } catch {
      refreshTask().catch(() => {});
    }
  });
  socket.addEventListener("close", () => {
    schedulePolling();
    const delay = state.reconnectDelay;
    state.reconnectDelay = Math.min(30000, state.reconnectDelay * 2);
    window.setTimeout(connectStatusStream, delay);
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  const dark = theme === "dark";
  els.themeIcon.setAttribute("href", iconHref(dark ? "sun" : "moon"));
  els.themeButton.title = dark ? "Use light theme" : "Use dark theme";
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

async function exportSession() {
  const session = await api("/api/session");
  await navigator.clipboard.writeText(JSON.stringify(session, null, 2));
  els.exportButtonLabel.textContent = "Copied";
  window.setTimeout(() => { els.exportButtonLabel.textContent = "Copy history"; }, 1200);
}

function handleShortcut(event) {
  if (!event.ctrlKey && !event.metaKey) return;
  if (event.key === "Enter") {
    event.preventDefault();
    startWork();
  } else if (event.key.toLowerCase() === "r") {
    event.preventDefault();
    runAction(refreshTask);
  } else if (event.key.toLowerCase() === "k") {
    event.preventDefault();
    els.historySearch.focus();
  } else if (event.key === "/") {
    event.preventDefault();
    els.shortcutsDialog.showModal();
  } else if (event.key.toLowerCase() === "z" && event.shiftKey) {
    event.preventDefault();
    redoForm();
  } else if (event.key.toLowerCase() === "z") {
    event.preventDefault();
    undoForm();
  }
}

els.startButton.addEventListener("click", startWork);
els.checkButton.addEventListener("click", () => runAction(() => refreshTask(true)));
els.setupButton.addEventListener("click", () => els.setupDialog.showModal());
els.closeSetupButton.addEventListener("click", () => els.setupDialog.close());
els.setupForm.addEventListener("submit", saveSetup);
els.executionChoice.addEventListener("change", updateSetupFields);
els.testConnectionButton.addEventListener("click", testConnection);
els.continueButton.addEventListener("click", () => els.continueDialog.showModal());
els.closeContinueButton.addEventListener("click", () => els.continueDialog.close());
els.continueForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const feedback = els.continueFeedback.value.trim();
  els.continueDialog.close();
  postAction("/api/tasks/current/continue", { feedback });
});
els.acceptButton.addEventListener("click", () => postAction("/api/tasks/current/accept"));
els.stopButton.addEventListener("click", () => {
  if (window.confirm("Stop after the current safe step? Progress and evidence will be kept.")) {
    postAction("/api/tasks/current/stop");
  }
});
els.themeButton.addEventListener("click", toggleTheme);
els.shortcutsButton.addEventListener("click", () => els.shortcutsDialog.showModal());
els.historySearch.addEventListener("input", () => refreshHistory().catch(() => {}));
els.exportButton.addEventListener("click", () => exportSession().catch((error) => window.alert(error.message)));
[els.objective, els.safeAreas, els.checks].forEach((field) => {
  field.addEventListener("input", () => {
    pushUndo();
    persistForm();
    updateStartButton();
  });
});
document.addEventListener("keydown", handleShortcut);

captureTokenFromUrl();
applyTheme(localStorage.getItem(THEME_KEY) || "light");
restoreForm();
Promise.all([refreshHealth(), refreshSetup(), refreshTask(), refreshHistory()])
  .then(connectStatusStream)
  .catch((error) => {
    els.statusLabel.textContent = "Needs attention";
    els.summary.textContent = error instanceof Error ? error.message : "The app could not start cleanly.";
  });
