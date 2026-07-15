const STORAGE_KEY = "agentic-harness-gui-form";
const THEME_KEY = "agentic-harness-theme";
const TOKEN_KEY = "agentic-harness-gui-session-token";
const TOKEN_PARAM = "token";
const ICON_PREFIX = "#icon-";
const API_TIMEOUT_MS = 20000;
const START_TIMEOUT_MS = 90000;
const DEFAULT_PUBLIC_STRATEGY = "plan";
const DEFAULT_MANAGED_MODE = "guided";

const GOAL_STARTERS = Object.freeze({
  create: {
    placeholder: "Example: Add a CSV download to the reports page and show me what changed.",
    hint: "Say what you want created or improved and any boundary that matters.",
  },
  fix: {
    placeholder: "Example: The Save button does nothing on iPhone. Find the cause and fix it.",
    hint: "Describe what is going wrong and what should happen instead.",
  },
  audit: {
    placeholder: "Example: Check this project for broken links and repair the ones you can verify.",
    hint: "Name what should be checked. The assistant will record findings and evidence.",
  },
  document: {
    placeholder: "Example: Write a setup guide that a new teammate can follow without help.",
    hint: "Say who the explanation is for and what they should understand or accomplish.",
  },
});

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
  mode: DEFAULT_PUBLIC_STRATEGY,
  modeDefault: DEFAULT_PUBLIC_STRATEGY,
  goalKind: "",
  modes: [],
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
  pendingStartObjective: "",
  lastRenderedTaskId: "",
  formReconciled: false,
  restoredDraftVersion: 0,
  providerTemplates: [],
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
  starterCreate: byId("starterCreate"),
  starterFix: byId("starterFix"),
  starterAudit: byId("starterAudit"),
  starterDocument: byId("starterDocument"),
  objectiveLabel: byId("objectiveLabel"),
  objectiveHint: byId("objectiveHint"),
  objective: byId("objective"),
  modeSection: byId("modeSection"),
  modeSelect: byId("modeSelect"),
  modes: byId("modes"),
  safeAreas: byId("safeAreas"),
  checks: byId("checks"),
  verificationDetails: byId("verificationDetails"),
  verificationSummary: byId("verificationSummary"),
  verificationLabel: byId("verificationLabel"),
  verificationHelp: byId("verificationHelp"),
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
  workApproachValue: byId("workApproachValue"),
  attemptsValue: byId("attemptsValue"),
  currentCard: byId("currentCard"),
  continueButton: byId("continueButton"),
  acceptButton: byId("acceptButton"),
  stopButton: byId("stopButton"),
  planList: byId("planList"),
  requirementsList: byId("requirementsList"),
  eventTimeline: byId("eventTimeline"),
  finalResult: byId("finalResult"),
  completedDetails: byId("completedDetails"),
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
  executionDisclosure: byId("executionDisclosure"),
  codingAgentFields: byId("codingAgentFields"),
  codingAgentChoice: byId("codingAgentChoice"),
  testCodingAgentButton: byId("testCodingAgentButton"),
  codingAgentConnectionResult: byId("codingAgentConnectionResult"),
  providerFields: byId("providerFields"),
  providerPreset: byId("providerPreset"),
  providerPresetHelp: byId("providerPresetHelp"),
  localModelRequirement: byId("localModelRequirement"),
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

async function api(path, options = {}, retry = true, timeoutMs = API_TIMEOUT_MS) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (state.authToken) headers.set("Authorization", `Bearer ${state.authToken}`);
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timeout = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
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
      return api(path, options, false, timeoutMs);
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

function starterButtons() {
  return [
    { button: els.starterCreate, kind: "create" },
    { button: els.starterFix, kind: "fix" },
    { button: els.starterAudit, kind: "audit" },
    { button: els.starterDocument, kind: "document" },
  ];
}

function selectGoalStarter(kind, { focus = false, persist = true } = {}) {
  state.goalKind = GOAL_STARTERS[kind] ? kind : "";
  starterButtons().forEach(({ button, kind: buttonKind }) => {
    button.setAttribute("aria-pressed", String(buttonKind === state.goalKind));
  });
  const starter = GOAL_STARTERS[state.goalKind];
  els.objective.placeholder = starter
    ? starter.placeholder
    : "Example: Add a CSV download to the reports page and show me what changed.";
  els.objectiveHint.textContent = starter
    ? starter.hint
    : "A normal sentence is enough. Include any limit that matters to you.";
  if (focus) els.objective.focus();
  if (persist) {
    pushUndo();
    persistForm();
  }
}

function formSnapshot() {
  return {
    objective: els.objective.value,
    safeAreas: els.safeAreas.value,
    checks: els.checks.value,
    mode: state.mode,
    goalKind: state.goalKind,
    draftVersion: 2,
  };
}

function applyFormSnapshot(snapshot) {
  els.objective.value = snapshot.objective || "";
  els.safeAreas.value = snapshot.safeAreas || "";
  els.checks.value = snapshot.checks || "";
  state.mode = snapshot.mode || state.modeDefault;
  selectGoalStarter(snapshot.goalKind || "", { persist: false });
  renderModes(state.modes);
  updateStartButton();
}

function resetNewGoalForm() {
  els.objective.value = "";
  els.safeAreas.value = "";
  if (usesHumanModes()) els.checks.value = "";
  state.mode = state.modeDefault;
  state.goalKind = "";
  selectGoalStarter("", { persist: false });
  renderModes(state.modes);
  sessionStorage.removeItem(STORAGE_KEY);
  state.restoredDraftVersion = 2;
  state.undoStack = [];
  state.redoStack = [];
  pushUndo();
  updateStartButton();
}

function reconcileCompletedDraft(task, receipt) {
  if (state.formReconciled) return;
  state.formReconciled = true;
  if (!receipt.terminal) return;
  const draft = els.objective.value.trim();
  const completedObjective = String(task.objective || "").trim();
  const legacyDraft = state.restoredDraftVersion > 0 && state.restoredDraftVersion < 2;
  if (draft && (legacyDraft || (completedObjective && draft === completedObjective))) {
    resetNewGoalForm();
  }
}

function usesHumanModes() {
  return state.setup?.editable === false && state.setup?.worker?.type === "local_goal";
}

function renderModes(modes, defaultMode = state.modeDefault) {
  state.modes = Array.isArray(modes) ? modes : [];
  state.modeDefault = defaultMode || DEFAULT_PUBLIC_STRATEGY;
  if (!state.modes.some((mode) => mode.key === state.mode)) {
    state.mode = state.modes.some((mode) => mode.key === state.modeDefault)
      ? state.modeDefault
      : state.modes[0]?.key || state.modeDefault;
  }
  els.modes.replaceChildren();
  els.modeSelect.replaceChildren();
  state.modes.forEach((mode) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "mode-card";
    card.setAttribute("aria-pressed", String(mode.key === state.mode));
    card.setAttribute("aria-label", `${mode.label}. ${mode.best_for}`);

    const title = document.createElement("strong");
    title.textContent = mode.label;
    const description = document.createElement("span");
    description.textContent = mode.best_for;
    const note = document.createElement("small");
    note.textContent = mode.caution;
    card.append(title, description, note);
    card.addEventListener("click", () => {
      state.mode = mode.key;
      renderModes(state.modes);
      pushUndo();
      persistForm();
      updateStartButton();
    });
    els.modes.append(card);

    const option = document.createElement("option");
    option.value = mode.key;
    option.textContent = `${mode.label} — ${mode.best_for}`;
    option.selected = mode.key === state.mode;
    els.modeSelect.append(option);
  });
  if (state.modes.some((mode) => mode.key === state.mode)) els.modeSelect.value = state.mode;
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
    try {
      const snapshot = JSON.parse(raw);
      state.restoredDraftVersion = Number(snapshot.draftVersion) || 1;
      applyFormSnapshot(snapshot);
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
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
  const verificationRequired = !usesHumanModes();
  const experimentNeedsModel = state.mode === "experiment"
    && state.setup?.worker?.type !== "model_agent";
  const experimentNeedsScope = state.mode === "experiment"
    && !els.safeAreas.value.trim();
  els.startButton.disabled = state.busy || !canStart || !hasObjective
    || (verificationRequired && !hasVerification)
    || experimentNeedsModel
    || experimentNeedsScope;
  if (state.busy) {
    els.startHelp.textContent = "Sending the goal. Planning can take up to a minute; this page will reconnect if your phone sleeps.";
  } else if (!canStart) {
    els.startHelp.textContent = state.readiness.next_action
      || state.readiness.summary
      || "Waiting for the current task state to become ready.";
  } else if (!hasObjective) {
    els.startHelp.textContent = "Describe the outcome you want before starting.";
  } else if (verificationRequired && !hasVerification) {
    els.startHelp.textContent = "Add the verification command that will prove this goal is complete to enable Start.";
  } else if (experimentNeedsModel) {
    els.startHelp.textContent = "Bounded experiment requires a local or cloud model in Setup so the selected file boundary can be enforced.";
  } else if (experimentNeedsScope) {
    els.startHelp.textContent = "Add at least one allowed file or folder under Optional scope for a bounded experiment.";
  } else if (!hasVerification) {
    els.startHelp.textContent = "Ready. The assistant will choose checks and show the evidence before calling this done.";
  } else {
    els.startHelp.textContent = "Ready to start this verified goal.";
  }
}

function renderHealth(health) {
  state.readiness = health.readiness || {};
  const ready = state.readiness.can_start === true;
  const needsSetup = ["setup_required", "credential_required", "verification_required"]
    .includes(state.readiness.state);
  const blocked = state.readiness.state === "blocked";
  const label = ready ? "Ready" : needsSetup ? "Setup needed" : blocked ? "Needs attention" : "Task active";
  els.healthText.textContent = label;
  els.health.className = ready ? "health ok" : needsSetup || blocked ? "health blocked" : "health";
  els.healthIcon.setAttribute("href", iconHref(ready ? "shield-check" : needsSetup || blocked ? "octagon-alert" : "loader-circle"));
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
  els.completedDetails.hidden = !terminal;
  els.finalResult.hidden = !terminal;
  els.finalResult.className = `final-result ${category}`;
  els.finalLabel.textContent = terminal ? final.label || "Result" : "Result";
  els.finalReason.textContent = terminal ? final.reason || final.summary || "" : "";
  const workerClaim = final.worker_claim && typeof final.worker_claim === "object"
    ? final.worker_claim
    : {};
  els.finalWorkerClaimLabel.textContent = "Assistant report";
  els.finalWorkerClaim.textContent = workerClaim.summary || "No assistant completion report was recorded.";
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
  reconcileCompletedDraft(task, receipt);
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
  document.body.dataset.taskComplete = String(receipt.terminal);
  const taskId = String(task.id || "");
  if (receipt.terminal && taskId !== state.lastRenderedTaskId) els.completedDetails.open = false;
  state.lastRenderedTaskId = taskId;
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
  const indeterminate = progress.determinate === false
    && ["starting", "working", "checking", "needs_review"].includes(status);
  const determinate = progress.determinate === true && Number.isFinite(percent);
  els.progressGroup.hidden = !(determinate || indeterminate);
  if (determinate) {
    const bounded = Math.max(0, Math.min(100, percent));
    els.progressValue.textContent = `${bounded}%`;
    els.progressBar.style.width = `${bounded}%`;
    els.progressTrack.className = "progress-track";
    els.progressTrack.setAttribute("aria-valuenow", String(bounded));
    els.progressTrack.removeAttribute("aria-valuetext");
  } else if (indeterminate) {
    els.progressValue.textContent = progress.label || "In progress";
    els.progressBar.style.width = "";
    els.progressTrack.className = "progress-track indeterminate";
    els.progressTrack.removeAttribute("aria-valuenow");
    els.progressTrack.setAttribute("aria-valuetext", progress.label || "In progress");
  } else {
    els.progressTrack.className = "progress-track";
    els.progressTrack.removeAttribute("aria-valuenow");
    els.progressTrack.removeAttribute("aria-valuetext");
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
  const strategy = task.metadata?.strategy;
  els.workApproachValue.textContent = strategy?.label
    || (usesHumanModes() ? "Managed route" : "Plan first");
  const execution = task.metadata?.execution;
  if (execution?.label) {
    const location = execution.data_location === "local"
      ? "Data stays local"
      : execution.data_location === "cloud_and_local"
        ? "Cloud planning + local execution"
        : "Managed data route";
    els.executionSummary.textContent = `${execution.label} · ${location}`;
    els.executionSummary.title = execution.detail || execution.label;
  }

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
  els.artifactsEvidence.hidden = receipt.terminal;

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
  const humanModes = setup.editable === false && setup.worker?.type === "local_goal";
  els.modeSection.hidden = false;
  els.checks.required = !humanModes;
  els.verificationDetails.className = humanModes
    ? "verification-details optional"
    : "verification-details required";
  els.verificationDetails.open = !humanModes;
  els.verificationSummary.textContent = humanModes
    ? "Optional: add your own success check"
    : "Required: add a success check";
  els.verificationLabel.textContent = humanModes
    ? "How should the result be checked? (optional)"
    : "Verification command for this goal";
  els.verificationHelp.textContent = humanModes
    ? "Leave this blank if you do not know. The assistant must still record its checks and evidence before the result can be accepted."
    : "Pre-filled from Setup. Edit it here to override the default for this run. This check runs independently, and the task is never verified done unless it passes.";
  els.setupButton.hidden = setup.editable === false;
  const workspace = setup.workspace || "";
  const workspaceName = workspace.split(/[\\/]/).filter(Boolean).at(-1) || "Current workspace";
  els.workspacePath.textContent = workspaceName.replaceAll("-", " ").replaceAll("_", " ");
  els.workspacePath.title = workspace || "Workspace path unavailable";
  const worker = setup.worker || {};
  renderDetectedAgents(setup, worker);
  const executionValidation = setup.execution_validation || {};
  els.executionSummary.textContent = setup.configured
    ? worker.type === "model_agent"
      ? `${worker.model || "Model"} · ${worker.data_location === "local" ? "data stays local" : "cloud endpoint"}`
        : worker.type === "local_goal"
        ? setup.execution_summary || "Managed runtime · route shown on active task"
        : executionValidation.verified
          ? `${worker.label || "Coding agent"} connection verified · model location set in agent`
          : `${worker.label || "Coding agent"} installed · connection not tested · model location set in agent`
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
  renderProviderTemplates(setup);
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

function renderProviderTemplates(setup) {
  const templates = Array.isArray(setup.provider_templates)
    ? setup.provider_templates
    : [{ key: "custom", label: "Custom OpenAI-compatible provider" }];
  state.providerTemplates = templates;
  refreshProviderPresets();
}

function refreshProviderPresets() {
  const execution = els.executionChoice.value;
  const location = execution === "local_model"
    ? "local"
    : execution === "cloud_model"
      ? "cloud"
      : "";
  const templates = state.providerTemplates.filter((template) => (
    template.key === "custom"
    || !location
    || !template.data_location
    || template.data_location === "both"
    || template.data_location === location
  ));
  els.providerPreset.replaceChildren();
  templates.forEach((template) => {
    const option = document.createElement("option");
    option.value = template.key;
    option.textContent = template.label;
    els.providerPreset.append(option);
  });
  els.providerPreset.value = "custom";
  els.providerPresetHelp.textContent = location === "local"
    ? "Choose a local-server preset, then enter the exact model ID loaded by that server."
    : location === "cloud"
      ? "Templates only pre-fill editable values. Your provider account controls model and entitlement availability."
      : "Choose an execution method to see matching provider templates.";
}

function applyProviderTemplate() {
  const template = state.providerTemplates.find((row) => row.key === els.providerPreset.value);
  if (!template || template.key === "custom") return;
  els.providerEndpoint.value = template.endpoint || "";
  els.providerModel.value = template.model || "";
  els.providerApiKeyEnv.value = template.api_key_env || "";
  els.connectionResult.textContent = template.entitlement_note || template.description || "";
  updateSetupFields();
}

function updateSetupFields({ resetProvider = false } = {}) {
  const execution = els.executionChoice.value;
  const model = execution !== "coding_agent";
  if (resetProvider && model) {
    els.providerEndpoint.value = "";
    els.providerModel.value = "";
    els.providerApiKeyEnv.value = "";
    els.providerApiKey.value = "";
    els.confirmRemoteData.checked = false;
    els.connectionResult.textContent = "";
  }
  els.providerFields.hidden = !model;
  els.codingAgentFields.hidden = model;
  els.remoteDataRow.hidden = execution !== "cloud_model";
  els.localModelRequirement.hidden = execution !== "local_model";
  els.executionDisclosure.textContent = execution === "local_model"
    ? "No cloud account is required. Work stays on this computer or your private LAN, but you must start a compatible local model server first."
    : execution === "cloud_model"
      ? "The selected file excerpts and tool results may be sent to your provider. You supply and control that account."
      : "The selected coding agent owns its own sign-in, model, and local-or-cloud routing. Agentic Harness adds the workflow and independent verification.";
  if (model) refreshProviderPresets();
}

async function refreshHealth() {
  return singleFlight("health", async () => {
    renderHealth(await api("/api/health"));
  });
}

async function refreshSetup() {
  renderSetup(await api("/api/setup"));
}

async function refreshModes() {
  const payload = await api("/api/modes");
  const fallback = payload.kind === "managed_route"
    ? DEFAULT_MANAGED_MODE
    : DEFAULT_PUBLIC_STRATEGY;
  renderModes(payload.modes || [], payload.default || fallback);
}

function taskMatchesPendingStart(task) {
  if (!state.pendingStartObjective) return true;
  return String(task?.objective || "").trim() === state.pendingStartObjective;
}

function adoptLiveTask(task, { force = false } = {}) {
  if (!taskMatchesPendingStart(task)) return false;
  state.liveTask = task;
  state.pendingStartObjective = "";
  if (force || !state.viewingHistoryId) {
    state.viewingHistoryId = "";
    renderTask(task);
  }
  return true;
}

async function refreshTask(force = false) {
  return singleFlight("task", async () => {
    const task = await api("/api/tasks/current");
    adoptLiveTask(task, { force });
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
  const objective = els.objective.value.trim();
  await runAction(async () => {
    const submittedAt = new Date().toISOString();
    state.pendingStartObjective = objective;
    const pendingTask = {
      id: `pending-${Date.now()}`,
      objective,
      human_title: objective.slice(0, 80),
      status: "starting",
      status_label: "Starting",
      result_category: "in_progress",
      summary: "Your goal was sent. The assistant is preparing it; you can safely return to this page if the connection changes.",
      progress: { determinate: false, percent: null, label: "Starting" },
      current: {
        cycle: 0,
        current_subgoal: "Preparing the task",
        checkpoint: "Connecting",
        last_event_at: submittedAt,
      },
      plan: [
        { status: "in_progress", step: "Understand the request" },
        { status: "pending", step: "Complete the requested work" },
        { status: "pending", step: "Verify the result" },
      ],
      requirements: [{ status: "active", text: `Requested outcome: ${objective}` }],
      events: [{ stage: "act", summary: "Goal sent to the assistant", checkpoint: "Starting" }],
      allowed_actions: [],
      metadata: { updated_at: submittedAt },
    };
    state.liveTask = pendingTask;
    renderTask(pendingTask);
    let task;
    try {
      task = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          mode: usesHumanModes() ? state.mode : undefined,
          strategy: usesHumanModes() ? undefined : state.mode,
          objective,
          safe_areas: linesFrom(els.safeAreas),
          checks: linesFrom(els.checks),
        }),
      }, true, START_TIMEOUT_MS);
    } catch (startError) {
      let recovered;
      try {
        recovered = await api("/api/tasks/current");
      } catch {
        state.pendingStartObjective = "";
        throw startError;
      }
      if (
        !["starting", "working", "checking", "needs_review"].includes(recovered.status)
        || !taskMatchesPendingStart(recovered)
      ) {
        state.pendingStartObjective = "";
        throw startError;
      }
      recovered.summary = "Your goal was accepted and is running. This page reconnected to the current task.";
      task = recovered;
    }
    if (task.metadata?.start_accepted === false) {
      state.pendingStartObjective = "";
      adoptLiveTask(task, { force: true });
      await refreshHistory();
      return;
    }
    if (!taskMatchesPendingStart(task)) {
      const recovered = await api("/api/tasks/current");
      if (taskMatchesPendingStart(recovered)) task = recovered;
    }
    if (!taskMatchesPendingStart(task)) {
      state.pendingStartObjective = "";
      throw new Error("The new goal was not confirmed. Your draft is still here; review the current task and try again.");
    }
    adoptLiveTask(task, { force: true });
    resetNewGoalForm();
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
    await Promise.all([refreshSetup(), refreshHealth(), refreshTask(true)]);
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

async function testCodingAgent() {
  els.codingAgentConnectionResult.textContent = "Testing without file access…";
  try {
    const result = await api("/api/setup/test", {
      method: "POST",
      body: JSON.stringify({
        execution: "coding_agent",
        agent: els.codingAgentChoice.value,
      }),
    }, true, START_TIMEOUT_MS);
    els.codingAgentConnectionResult.textContent = result.summary || (
      result.verified ? "Connection and model verified." : "Executable found."
    );
    await Promise.all([refreshSetup(), refreshHealth()]);
  } catch (error) {
    els.codingAgentConnectionResult.textContent = error instanceof Error
      ? error.message
      : String(error);
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
  if (state.socket && Number(state.socket.readyState) < 2) return;
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
      adoptLiveTask(task);
      refreshHistory().catch(() => {});
      refreshHealth().catch(() => {});
    } catch {
      refreshTask().catch(() => {});
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket === socket) state.socket = null;
    schedulePolling();
    const delay = state.reconnectDelay;
    state.reconnectDelay = Math.min(30000, state.reconnectDelay * 2);
    window.setTimeout(connectStatusStream, delay);
  });
}

function recoverVisibleSession() {
  if (document.visibilityState === "hidden") return;
  Promise.all([refreshTask(true), refreshHealth(), refreshHistory()]).catch(() => {});
  connectStatusStream();
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
els.executionChoice.addEventListener("change", () => updateSetupFields({ resetProvider: true }));
els.providerPreset.addEventListener("change", applyProviderTemplate);
els.testConnectionButton.addEventListener("click", testConnection);
els.testCodingAgentButton.addEventListener("click", testCodingAgent);
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
starterButtons().forEach(({ button, kind }) => {
  button.addEventListener("click", () => selectGoalStarter(kind, { focus: true }));
});
els.modeSelect.addEventListener("change", () => {
  state.mode = els.modeSelect.value || state.modeDefault;
  renderModes(state.modes);
  pushUndo();
  persistForm();
  updateStartButton();
});
els.verificationDetails.addEventListener("toggle", () => {
  if (!usesHumanModes() && !els.verificationDetails.open) {
    els.verificationDetails.open = true;
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
document.addEventListener("visibilitychange", recoverVisibleSession);
window.addEventListener("pageshow", recoverVisibleSession);
window.addEventListener("online", recoverVisibleSession);

captureTokenFromUrl();
applyTheme(localStorage.getItem(THEME_KEY) || "light");
restoreForm();
Promise.all([refreshHealth(), refreshSetup(), refreshModes(), refreshTask(), refreshHistory()])
  .then(connectStatusStream)
  .catch((error) => {
    els.statusLabel.textContent = "Needs attention";
    els.summary.textContent = error instanceof Error ? error.message : "The app could not start cleanly.";
  });
