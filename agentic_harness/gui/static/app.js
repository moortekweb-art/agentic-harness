const STORAGE_KEY = "agentic-harness-gui-session";
const THEME_KEY = "agentic-harness-theme";
const TOKEN_KEY = "agentic-harness-gui-session-token";
const TOKEN_PARAM = "token";

const state = {
  mode: "cloud",
  busy: false,
  modes: [],
  undoStack: [],
  redoStack: [],
  socket: null,
  authToken: "",
  authPromptPromise: null,
};

const els = {
  health: document.getElementById("health"),
  modes: document.getElementById("modes"),
  objective: document.getElementById("objective"),
  safeAreas: document.getElementById("safeAreas"),
  checks: document.getElementById("checks"),
  startButton: document.getElementById("startButton"),
  checkButton: document.getElementById("checkButton"),
  watchButton: document.getElementById("watchButton"),
  continueButton: document.getElementById("continueButton"),
  acceptButton: document.getElementById("acceptButton"),
  stopButton: document.getElementById("stopButton"),
  undoButton: document.getElementById("undoButton"),
  redoButton: document.getElementById("redoButton"),
  themeButton: document.getElementById("themeButton"),
  shortcutsButton: document.getElementById("shortcutsButton"),
  shortcutsDialog: document.getElementById("shortcutsDialog"),
  statusLabel: document.getElementById("statusLabel"),
  statusDot: document.getElementById("statusDot"),
  progressBar: document.getElementById("progressBar"),
  summary: document.getElementById("summary"),
  readinessCard: document.getElementById("readinessCard"),
  readinessStatus: document.getElementById("readinessStatus"),
  readinessSummary: document.getElementById("readinessSummary"),
  agentLoop: document.getElementById("agentLoop"),
  changedFiles: document.getElementById("changedFiles"),
  verification: document.getElementById("verification"),
  artifacts: document.getElementById("artifacts"),
  advancedDetails: document.getElementById("advancedDetails"),
  historySearch: document.getElementById("historySearch"),
  historyList: document.getElementById("historyList"),
  exportButton: document.getElementById("exportButton"),
  importButton: document.getElementById("importButton"),
};

function captureTokenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const captured = (params.get(TOKEN_PARAM) || "").trim();
  if (captured) {
    state.authToken = captured;
    sessionStorage.setItem(TOKEN_KEY, captured);
    params.delete(TOKEN_PARAM);
    const nextQuery = params.toString();
    const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}${window.location.hash}`;
    history.replaceState(history.state, "", nextUrl);
    return;
  }
  state.authToken = sessionStorage.getItem(TOKEN_KEY) || "";
}

function authHeaders(options = {}) {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (state.authToken) {
    headers.set("Authorization", `Bearer ${state.authToken}`);
  }
  return headers;
}

async function api(path, options = {}, retry = true) {
  const response = await fetch(path, {
    ...options,
    headers: authHeaders(options),
  });
  if (response.status === 401 && retry) {
    clearAuthToken();
    const entered = await showTokenDialog();
    if (entered) {
      state.authToken = entered;
      sessionStorage.setItem(TOKEN_KEY, entered);
      return api(path, options, false);
    }
  }
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Authorization required." : `Request failed: ${response.status}`);
  }
  return response.json();
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
          <button value="cancel" title="Cancel">Cancel</button>
        </div>
        <label class="field-label" for="authTokenInput">Token</label>
        <input id="authTokenInput" name="authTokenInput" type="password" autocomplete="off" />
        <div class="actions compact">
          <button class="primary" value="confirm">Continue</button>
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

function clearAuthToken() {
  state.authToken = "";
  sessionStorage.removeItem(TOKEN_KEY);
}

function linesFrom(textarea) {
  return textarea.value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function setBusy(isBusy) {
  state.busy = isBusy;
  [
    els.startButton,
    els.checkButton,
    els.watchButton,
    els.continueButton,
    els.acceptButton,
    els.stopButton,
  ].forEach((button) => {
    button.disabled = isBusy;
  });
}

function formSnapshot() {
  return {
    objective: els.objective.value,
    safeAreas: els.safeAreas.value,
    checks: els.checks.value,
    mode: state.mode,
  };
}

function applyFormSnapshot(snapshot) {
  els.objective.value = snapshot.objective || "";
  els.safeAreas.value = snapshot.safeAreas || "";
  els.checks.value = snapshot.checks || "";
  state.mode = snapshot.mode || "cloud";
  renderModes(state.modes);
  persistLocal();
}

function pushUndo() {
  const current = JSON.stringify(formSnapshot());
  const previous = state.undoStack[state.undoStack.length - 1];
  if (previous !== current) {
    state.undoStack.push(current);
    state.undoStack = state.undoStack.slice(-50);
    state.redoStack = [];
  }
  persistLocal();
}

function undoForm() {
  if (state.undoStack.length < 2) return;
  state.redoStack.push(state.undoStack.pop());
  applyFormSnapshot(JSON.parse(state.undoStack[state.undoStack.length - 1]));
}

function redoForm() {
  const next = state.redoStack.pop();
  if (!next) return;
  state.undoStack.push(next);
  applyFormSnapshot(JSON.parse(next));
}

function renderModes(modes) {
  state.modes = modes;
  els.modes.innerHTML = "";
  modes.forEach((mode) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "mode-card";
    card.setAttribute("aria-pressed", String(mode.key === state.mode));
    card.title = mode.caution || mode.best_for || mode.label;
    card.innerHTML = `
      <strong>${escapeHtml(mode.label)}</strong>
      <span>${escapeHtml(mode.best_for)}</span>
      <small>${escapeHtml(mode.caution)}</small>
    `;
    card.addEventListener("click", () => {
      pushUndo();
      state.mode = mode.key;
      renderModes(modes);
      pushUndo();
    });
    els.modes.appendChild(card);
  });
}

function renderTask(task) {
  const status = task.status || "working";
  const progress = Number(task.progress || 0);
  els.statusLabel.textContent = task.status_label || "Working";
  els.statusDot.className = `status-dot ${status}`;
  els.progressBar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
  els.summary.textContent = task.summary || "No detail returned yet.";
  renderReadiness({
    ...(task.readiness_gate || {}),
    agent_loop: task.agent_loop || (task.readiness_gate || {}).agent_loop,
  });
  renderList(els.changedFiles, task.changed_files || []);
  renderList(els.verification, task.verification || []);
  renderArtifacts(task.artifacts || []);
  els.advancedDetails.textContent = JSON.stringify(task.advanced_details || task, null, 2);
}

function renderReadiness(readiness = {}) {
  const stateName = readiness.state || "ready";
  els.readinessCard.className = `readiness-card ${stateName}`;
  els.readinessStatus.textContent = readiness.requires_review
    ? "Needs review before new work"
    : readiness.can_start === false
      ? "Not ready for new work"
      : "Ready for work";
  els.readinessSummary.textContent = readiness.next_action || readiness.summary || "Waiting for local status.";
  const loop = readiness.agent_loop || {};
  const steps = Array.isArray(loop.steps) ? loop.steps : ["Perceive", "Plan", "Act", "Check", "Review"];
  const current = loop.stage || "Perceive";
  els.agentLoop.innerHTML = "";
  steps.forEach((step) => {
    const item = document.createElement("li");
    item.textContent = step;
    if (step === current) item.className = "active";
    els.agentLoop.appendChild(item);
  });
}

function renderList(container, values) {
  container.innerHTML = "";
  if (values.length === 0) {
    const item = document.createElement("li");
    item.textContent = "Nothing reported yet.";
    container.appendChild(item);
    return;
  }
  values.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    container.appendChild(item);
  });
}

function renderArtifacts(values) {
  els.artifacts.innerHTML = "";
  if (values.length === 0) {
    const item = document.createElement("li");
    item.textContent = "Nothing reported yet.";
    els.artifacts.appendChild(item);
    return;
  }
  values.forEach((artifact) => {
    const item = document.createElement("li");
    item.textContent = artifact.name || artifact.path || String(artifact);
    els.artifacts.appendChild(item);
  });
}

function renderHistory(tasks) {
  els.historyList.innerHTML = "";
  if (tasks.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No matching tasks.";
    els.historyList.appendChild(item);
    return;
  }
  tasks.forEach((task) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = task.summary || task.human_title || "Untitled task";
    button.title = task.status_label || task.status || "Task";
    button.addEventListener("click", () => renderTask(task));
    item.appendChild(button);
    els.historyList.appendChild(item);
  });
}

async function refreshHealth() {
  const health = await api("/api/health");
  const readiness = health.readiness || {};
  els.health.textContent = readiness.requires_review
    ? "Needs review"
    : health.local_goal_available
      ? "Worker ready"
      : "Worker missing";
  els.health.className = readiness.requires_review
    ? "health review"
    : health.local_goal_available
      ? "health ok"
      : "health blocked";
  renderReadiness(readiness);
}

async function refreshModes() {
  const payload = await api("/api/modes");
  renderModes(payload.modes || []);
}

async function refreshTask() {
  const task = await api("/api/tasks/current");
  renderTask(task);
}

async function refreshHistory() {
  const query = encodeURIComponent(els.historySearch.value.trim());
  const payload = await api(`/api/tasks/history${query ? `?q=${query}` : ""}`);
  renderHistory(payload.tasks || []);
}

async function startWork() {
  const objective = els.objective.value.trim();
  await runAction(async () => {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        mode: state.mode,
        objective,
        safe_areas: linesFrom(els.safeAreas),
        checks: linesFrom(els.checks),
      }),
    });
    renderTask(task);
    await refreshHistory();
  });
}

async function postAction(path, body = {}) {
  await runAction(async () => {
    const task = await api(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
    renderTask(task);
    await refreshHistory();
  });
}

async function runAction(action) {
  if (state.busy) return;
  setBusy(true);
  try {
    await action();
    await refreshHealth();
  } catch (error) {
    renderTask({
      status: "blocked",
      status_label: "Blocked",
      summary: error instanceof Error ? error.message : "The request failed.",
      advanced_details: { error: String(error) },
    });
  } finally {
    setBusy(false);
  }
}

function connectStatusStream() {
  if (!("WebSocket" in window)) return;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const token = state.authToken;
  const tokenQuery = token ? `?${TOKEN_PARAM}=${encodeURIComponent(token)}` : "";
  const socket = new WebSocket(`${scheme}://${window.location.host}/api/tasks/stream${tokenQuery}`);
  state.socket = socket;
  socket.addEventListener("message", (event) => {
    try {
      renderTask(JSON.parse(event.data));
    } catch {
      refreshTask().catch(() => {});
    }
  });
  socket.addEventListener("close", () => {
    window.setTimeout(connectStatusStream, 5000);
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

function persistLocal() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      form: formSnapshot(),
      undoStack: state.undoStack,
      redoStack: state.redoStack,
    }),
  );
}

function restoreLocal() {
  applyTheme(localStorage.getItem(THEME_KEY) || "light");
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    pushUndo();
    return;
  }
  try {
    const saved = JSON.parse(raw);
    if (saved.form) applyFormSnapshot(saved.form);
    state.undoStack = Array.isArray(saved.undoStack) ? saved.undoStack : [];
    state.redoStack = Array.isArray(saved.redoStack) ? saved.redoStack : [];
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
  if (state.undoStack.length === 0) pushUndo();
}

async function exportSession() {
  const session = await api("/api/session");
  await navigator.clipboard.writeText(JSON.stringify(session, null, 2));
  els.exportButton.textContent = "Copied";
  window.setTimeout(() => {
    els.exportButton.textContent = "Export";
  }, 1200);
}

async function importSession() {
  const raw = window.prompt("Paste exported session JSON");
  if (!raw) return;
  const payload = JSON.parse(raw);
  await api("/api/session/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await refreshHistory();
}

function handleShortcut(event) {
  if (!event.ctrlKey && !event.metaKey) return;
  if (event.key === "Enter") {
    event.preventDefault();
    startWork();
  } else if (event.key.toLowerCase() === "r") {
    event.preventDefault();
    runAction(refreshTask);
  } else if (event.key.toLowerCase() === "m") {
    event.preventDefault();
    postAction("/api/tasks/current/watch");
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

els.startButton.addEventListener("click", startWork);
els.checkButton.addEventListener("click", () => runAction(refreshTask));
els.watchButton.addEventListener("click", () => postAction("/api/tasks/current/watch"));
els.continueButton.addEventListener("click", () => postAction("/api/tasks/current/continue"));
els.acceptButton.addEventListener("click", () => postAction("/api/tasks/current/accept"));
els.stopButton.addEventListener("click", () => postAction("/api/tasks/current/stop"));
els.undoButton.addEventListener("click", undoForm);
els.redoButton.addEventListener("click", redoForm);
els.themeButton.addEventListener("click", toggleTheme);
els.shortcutsButton.addEventListener("click", () => els.shortcutsDialog.showModal());
els.historySearch.addEventListener("input", () => refreshHistory().catch(() => {}));
els.exportButton.addEventListener("click", () => exportSession().catch((error) => window.alert(error.message)));
els.importButton.addEventListener("click", () => importSession().catch((error) => window.alert(error.message)));
[els.objective, els.safeAreas, els.checks].forEach((field) => {
  field.addEventListener("input", () => {
    pushUndo();
    persistLocal();
  });
});
document.addEventListener("keydown", handleShortcut);

captureTokenFromUrl();
restoreLocal();
Promise.all([refreshHealth(), refreshModes(), refreshTask(), refreshHistory()])
  .then(connectStatusStream)
  .catch((error) => {
    renderTask({
      status: "blocked",
      status_label: "Blocked",
      summary: error instanceof Error ? error.message : "The GUI could not start cleanly.",
      advanced_details: { error: String(error) },
    });
  });
