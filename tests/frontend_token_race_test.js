const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appPath = process.env.APP_JS_PATH || path.join(process.cwd(), "agentic_harness/gui/static/app.js");
const appSource = fs.readFileSync(appPath, "utf8");

class HeadersShim {
  constructor(headers = {}) {
    this.values = new Map();
    Object.entries(headers).forEach(([key, value]) => this.set(key, value));
  }

  has(key) {
    return this.values.has(key.toLowerCase());
  }

  set(key, value) {
    this.values.set(key.toLowerCase(), String(value));
  }

  get(key) {
    return this.values.get(key.toLowerCase()) || null;
  }
}

class Element {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.disabled = false;
    this.hidden = false;
    this.open = false;
    this.checked = false;
    this.selected = false;
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.listeners = {};
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  append(...children) {
    this.children.push(...children);
  }

  setAttribute(name, value) {
    this[name] = value;
  }

  focus() {}
}

class Dialog extends Element {
  constructor(document, id = "") {
    super(id);
    this.document = document;
    this.returnValue = "";
    this.input = new Element("authTokenInput");
    this.showCount = 0;
  }

  querySelector(selector) {
    return selector === "input" ? this.input : null;
  }

  showModal() {
    this.showCount += 1;
    if (!this.open) this.document.openDialogs.push(this);
    this.open = true;
  }

  remove() {
    this.document.removedDialogs += 1;
    this.document.openDialogs = this.document.openDialogs.filter((dialog) => dialog !== this);
  }

  close(returnValue = "") {
    this.returnValue = returnValue;
    this.open = false;
    this.document.openDialogs = this.document.openDialogs.filter((dialog) => dialog !== this);
    if (this.listeners.close) this.listeners.close();
  }
}

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
    values,
  };
}

function okPayloadFor(url, setupPayload = null, taskPayload = null, healthPayload = null) {
  if (url === "/api/health") {
    return healthPayload || {
      ok: true,
      local_goal_available: true,
      readiness: { state: "ready", can_start: true },
    };
  }
  if (url === "/api/modes") {
    return {
      modes: [
        { key: "local", label: "Quick task", best_for: "small work", caution: "" },
        { key: "guided", label: "Plan first", best_for: "important work", caution: "recommended" },
        { key: "cloud", label: "Keep working", best_for: "large work", caution: "" },
        { key: "experimental", label: "Safe experiment", best_for: "tiny trials", caution: "" },
      ],
    };
  }
  if (url === "/api/setup") {
    return setupPayload || {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      workspace: "/tmp/project",
      worker: { type: "coding_agent" },
      suggested_check: "python -m pytest -q",
    };
  }
  if (url === "/api/tasks/current") {
    return taskPayload || {
      status: "ready",
      status_label: "Ready",
      result_category: "in_progress",
      summary: "Backend ready",
      progress: 0,
    };
  }
  if (url.startsWith("/api/tasks/history")) {
    return { tasks: [] };
  }
  return { ok: true };
}

async function tick() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function runApp({
  initialToken = "",
  initialDraft = null,
  publicAccess = false,
  setupPayload = null,
  taskPayload = null,
  healthPayload = null,
} = {}) {
  const elements = new Map();
  const document = {
    body: new Element("body"),
    documentElement: new Element("html"),
    openDialogs: [],
    removedDialogs: 0,
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, id.endsWith("Dialog") ? new Dialog(document, id) : new Element(id));
      }
      return elements.get(id);
    },
    createElement(tag) {
      return tag === "dialog" ? new Dialog(document) : new Element();
    },
    addEventListener() {},
  };
  const initialSession = {};
  if (initialToken) initialSession["agentic-harness-gui-session-token"] = initialToken;
  if (initialDraft) initialSession["agentic-harness-gui-form"] = JSON.stringify(initialDraft);
  const sessionStorage = storage(initialSession);
  const localStorage = storage();
  const fetchCalls = [];
  const websocketUrls = [];
  const consoleErrors = [];

  class WebSocketShim {
    constructor(url) {
      this.url = url;
      websocketUrls.push(url);
      this.listeners = {};
    }

    addEventListener(type, handler) {
      this.listeners[type] = handler;
    }
  }

  const context = {
    assert,
    console: {
      log() {},
      error(...args) {
        consoleErrors.push(args);
      },
    },
    document,
    fetch: async (url, options = {}) => {
      const auth = options.headers.get("Authorization");
      fetchCalls.push({ url, auth, options });
      if (!publicAccess && auth !== "Bearer correct-token") {
        return { status: 401, ok: false, json: async () => ({ ok: false }) };
      }
      return {
        status: 200,
        ok: true,
        json: async () => okPayloadFor(url, setupPayload, taskPayload, healthPayload),
      };
    },
    Headers: HeadersShim,
    history: {
      state: {},
      replaceState() {},
    },
    localStorage,
    navigator: { clipboard: { writeText: async () => {} } },
    sessionStorage,
    URLSearchParams,
    WebSocket: WebSocketShim,
    window: {
      location: { search: "", pathname: "/", hash: "", protocol: "http:", host: "127.0.0.1:41111" },
      setTimeout,
      setInterval() { return 1; },
      clearInterval() {},
      alert(message) {
        throw new Error(message);
      },
    },
    setTimeout,
    setInterval() { return 1; },
    clearInterval() {},
  };
  context.window.WebSocket = WebSocketShim;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: appPath });
  await tick();
  await tick();
  return {
    context,
    document,
    elements,
    fetchCalls,
    localStorage,
    sessionStorage,
    websocketUrls,
    consoleErrors,
  };
}

async function testConcurrentStartup401sShareOnePromptAndAllRetry() {
  const app = await runApp();
  assert.equal(app.document.openDialogs.length, 1);
  assert.equal(app.fetchCalls.length, 5);
  assert.deepEqual(
    app.fetchCalls.map((call) => call.auth),
    [null, null, null, null, null],
  );

  app.document.openDialogs[0].input.value = "correct-token";
  app.document.openDialogs[0].close("confirm");
  await tick();
  await tick();

  assert.equal(app.document.removedDialogs, 1);
  assert.equal(app.document.openDialogs.length, 0);
  assert.equal(app.fetchCalls.length, 10);
  assert.equal(app.fetchCalls.slice(5).every((call) => call.auth === "Bearer correct-token"), true);
  assert.equal(app.elements.get("summary").textContent, "Backend ready");
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-session-token"), "correct-token");
  assert.equal(app.websocketUrls.length, 0);
  assert.deepEqual(app.consoleErrors, []);
}

async function testStaleTokenIsClearedPromptedOnceAndReplaced() {
  const app = await runApp({ initialToken: "stale-token" });
  assert.equal(app.document.openDialogs.length, 1);
  assert.equal(app.fetchCalls.length, 5);
  assert.equal(app.fetchCalls.every((call) => call.auth === "Bearer stale-token"), true);
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-session-token"), null);

  app.document.openDialogs[0].input.value = "correct-token";
  app.document.openDialogs[0].close("confirm");
  await tick();
  await tick();

  assert.equal(app.document.removedDialogs, 1);
  assert.equal(app.fetchCalls.length, 10);
  assert.equal(app.fetchCalls.slice(5).every((call) => call.auth === "Bearer correct-token"), true);
  assert.equal(app.elements.get("summary").textContent, "Backend ready");
}

async function testCancelResolvesAllWaitersWithoutSecondDialogOrLeakingToken() {
  const app = await runApp();
  assert.equal(app.document.openDialogs.length, 1);

  app.document.openDialogs[0].input.value = "do-not-store";
  app.document.openDialogs[0].close("cancel");
  await tick();
  await tick();

  assert.equal(app.document.removedDialogs, 1);
  assert.equal(app.document.openDialogs.length, 0);
  assert.equal(app.fetchCalls.length, 5);
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-session-token"), null);
  assert.equal(app.elements.get("statusLabel").textContent, "Needs attention");
  assert.equal(app.elements.get("summary").textContent, "Authorization required.");
  assert.equal(app.websocketUrls.length, 0);
}

async function testLegacySetupContractCompletesBootstrapAndAttachesStatusStream() {
  const app = await runApp({
    publicAccess: true,
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      editable: false,
      workspace: "/tmp/legacy-workspace",
      worker: { type: "local_goal", label: "Existing local-goal runtime" },
    },
  });

  assert.equal(app.elements.get("workspacePath").textContent, "legacy workspace");
  assert.equal(app.elements.get("workspacePath").title, "/tmp/legacy-workspace");
  assert.equal(app.elements.get("executionSummary").textContent, "Verified agent ready");
  assert.equal(app.elements.get("setupButton").hidden, true);
  assert.deepEqual(app.websocketUrls, ["ws://127.0.0.1:41111/api/tasks/stream"]);
  assert.equal(app.fetchCalls.some((call) => call.url === "/api/setup"), true);
  assert.deepEqual(app.consoleErrors, []);
}

async function testFreshSetupOpensOnceAndSelectsTheRecommendedDetectedAgent() {
  const setupPayload = {
    contract: "agentic_harness.gui_setup.v1",
    configured: false,
    workspace: "/tmp/new-project",
    suggested_check: "npm test",
    execution_options: [
      {
        key: "coding_agent",
        available: true,
        recommended: true,
        recommended_agent: "codex",
        agents: [
          { key: "codex", label: "Codex", available: true, recommended: true },
          { key: "aider", label: "Aider", available: false, recommended: false },
        ],
      },
      { key: "local_model", available: true, recommended: false },
      { key: "cloud_model", available: true, recommended: false },
    ],
  };
  const app = await runApp({ publicAccess: true, setupPayload });
  const setupDialog = app.elements.get("setupDialog");
  const agentChoice = app.elements.get("codingAgentChoice");

  assert.equal(setupDialog.open, true);
  assert.equal(setupDialog.showCount, 1);
  assert.equal(agentChoice.value, "codex");
  assert.equal(agentChoice.children.length, 2);
  assert.equal(agentChoice.children[0].textContent, "Codex (recommended)");
  assert.equal(agentChoice.children[0].disabled, false);
  assert.equal(agentChoice.children[1].textContent, "Aider (not found)");
  assert.equal(agentChoice.children[1].disabled, true);
  assert.equal(app.elements.get("checks").value, "npm test");
  assert.equal(app.elements.get("verificationCommand").value, "npm test");

  await app.context.refreshSetup();
  assert.equal(setupDialog.showCount, 1);
}

async function testConfiguredVerificationReplacesOnlyThePreviousSuggestion() {
  const app = await runApp({
    publicAccess: true,
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: false,
      workspace: "/tmp/new-project",
      suggested_check: "npm test",
    },
  });

  app.context.renderSetup({
    contract: "agentic_harness.gui_setup.v1",
    configured: true,
    workspace: "/tmp/new-project",
    worker: { type: "coding_agent", agent: "codex", label: "Codex" },
    verification_command: "npm run verify",
    suggested_check: "npm test",
  });
  assert.equal(app.elements.get("checks").value, "npm run verify");

  app.elements.get("checks").value = "npm run verify:focused";
  app.context.renderSetup({
    contract: "agentic_harness.gui_setup.v1",
    configured: true,
    workspace: "/tmp/new-project",
    worker: { type: "coding_agent", agent: "codex", label: "Codex" },
    verification_command: "npm run verify:default",
  });
  assert.equal(app.elements.get("checks").value, "npm run verify:focused");
}

async function testRunRequiresObjectiveAndEffectiveVerificationAndUsesSessionDraft() {
  const app = await runApp({
    publicAccess: true,
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      workspace: "/tmp/project",
      worker: { type: "coding_agent", agent: "codex", label: "Codex" },
      verification_command: "",
      suggested_check: "",
    },
  });
  const objective = app.elements.get("objective");
  const checks = app.elements.get("checks");
  const start = app.elements.get("startButton");

  assert.equal(app.elements.get("verificationDetails").open, true);
  assert.equal(app.elements.get("verificationSummary").textContent, "Required: add a success check");

  objective.value = "Fix the regression";
  objective.listeners.input();
  assert.equal(start.disabled, true);

  checks.value = "python -m pytest -q";
  checks.listeners.input();
  assert.equal(start.disabled, false);
  assert.equal(app.localStorage.getItem("agentic-harness-gui-form"), null);
  assert.deepEqual(
    JSON.parse(app.sessionStorage.getItem("agentic-harness-gui-form")),
    {
      objective: "Fix the regression",
      safeAreas: "",
      checks: "python -m pytest -q",
      mode: "guided",
      goalKind: "",
      draftVersion: 2,
    },
  );
}

async function testLegacyHumanCanChooseEveryModeWithoutWritingACommand() {
  const app = await runApp({
    publicAccess: true,
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      editable: false,
      workspace: "/tmp/legacy-workspace",
      worker: { type: "local_goal", label: "Existing local-goal runtime" },
    },
  });
  const objective = app.elements.get("objective");
  const checks = app.elements.get("checks");
  const start = app.elements.get("startButton");
  const modeSection = app.elements.get("modeSection");
  const modes = app.elements.get("modes");

  assert.equal(modeSection.hidden, false);
  assert.equal(modes.children.length, 4);
  assert.equal(checks.required, false);
  assert.equal(app.elements.get("verificationDetails").open, false);
  assert.equal(
    app.elements.get("verificationSummary").textContent,
    "Optional: add your own success check",
  );
  objective.value = "Please audit my system and give me a simple report.";
  objective.listeners.input();
  assert.equal(start.disabled, false);
  assert.match(app.elements.get("startHelp").textContent, /assistant will choose checks/i);

  for (const card of [...modes.children]) {
    objective.value = "Please audit my system and give me a simple report.";
    objective.listeners.input();
    card.listeners.click();
    await app.context.startWork();
  }
  const submissions = app.fetchCalls.filter((call) => call.url === "/api/tasks");
  assert.deepEqual(
    submissions.map((call) => JSON.parse(call.options.body).mode),
    ["local", "guided", "cloud", "experimental"],
  );
  assert.equal(submissions.every((call) => JSON.parse(call.options.body).checks.length === 0), true);
  assert.equal(objective.value, "");
  assert.equal(app.elements.get("modeSelect").value, "guided");
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-form"), null);
}

async function testGoalStartersAndCompactModeSelectorKeepPlainLanguageDraftState() {
  const app = await runApp({
    publicAccess: true,
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      editable: false,
      workspace: "/tmp/legacy-workspace",
      worker: { type: "local_goal", label: "Existing local-goal runtime" },
    },
  });

  const fixStarter = app.elements.get("starterFix");
  fixStarter.listeners.click();
  assert.equal(fixStarter["aria-pressed"], "true");
  assert.match(app.elements.get("objective").placeholder, /Save button does nothing on iPhone/);
  assert.match(app.elements.get("objectiveHint").textContent, /what should happen instead/i);

  const modeSelect = app.elements.get("modeSelect");
  assert.equal(modeSelect.children.length, 4);
  assert.equal(modeSelect.value, "guided");
  modeSelect.value = "cloud";
  modeSelect.listeners.change();
  assert.equal(modeSelect.value, "cloud");
  assert.deepEqual(
    JSON.parse(app.sessionStorage.getItem("agentic-harness-gui-form")),
    {
      objective: "",
      safeAreas: "",
      checks: "",
      mode: "cloud",
      goalKind: "fix",
      draftVersion: 2,
    },
  );
}

async function testCompletedGoalClearsOnlyItsMatchingStaleDraft() {
  const completedObjective = "Audit this project and explain the findings.";
  const app = await runApp({
    publicAccess: true,
    initialDraft: {
      objective: completedObjective,
      safeAreas: "reports",
      checks: "",
      mode: "experimental",
      goalKind: "audit",
    },
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      editable: false,
      workspace: "/tmp/legacy-workspace",
      worker: { type: "local_goal", label: "Existing local-goal runtime" },
    },
    taskPayload: {
      id: "completed-goal",
      objective: completedObjective,
      status: "done",
      result_category: "verified_done",
      final_result: {
        label: "Verified complete",
        reason: "The audit passed independent review.",
        worker_claim: { summary: "Audit complete." },
      },
    },
  });

  assert.equal(app.elements.get("objective").value, "");
  assert.equal(app.elements.get("safeAreas").value, "");
  assert.equal(app.elements.get("modeSelect").value, "guided");
  assert.equal(app.elements.get("starterAudit")["aria-pressed"], "false");
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-form"), null);
}

async function testCompletedGoalPreservesASecondGenerationNewDraft() {
  const nextObjective = "Create a new onboarding checklist.";
  const app = await runApp({
    publicAccess: true,
    initialDraft: {
      objective: nextObjective,
      safeAreas: "docs",
      checks: "",
      mode: "guided",
      goalKind: "create",
      draftVersion: 2,
    },
    setupPayload: {
      contract: "agentic_harness.gui_setup.v1",
      configured: true,
      editable: false,
      workspace: "/tmp/legacy-workspace",
      worker: { type: "local_goal", label: "Existing local-goal runtime" },
    },
    taskPayload: {
      id: "completed-goal",
      status: "done",
      result_category: "verified_done",
      final_result: { label: "Verified complete", reason: "Previous task passed." },
    },
  });

  assert.equal(app.elements.get("objective").value, nextObjective);
  assert.equal(app.elements.get("safeAreas").value, "docs");
  assert.equal(app.elements.get("starterCreate")["aria-pressed"], "true");
}

async function testPausedBackgroundAssistantIsNotCalledAnActiveTask() {
  const app = await runApp({
    publicAccess: true,
    healthPayload: {
      ok: true,
      readiness: {
        state: "blocked",
        can_start: false,
        summary: "The background assistant is paused.",
        next_action: "Ask the workspace owner to restart it, then refresh this page.",
      },
    },
  });

  assert.equal(app.elements.get("healthText").textContent, "Needs attention");
  assert.equal(app.elements.get("health").className, "health blocked");
  assert.equal(
    app.elements.get("startHelp").textContent,
    "Ask the workspace owner to restart it, then refresh this page.",
  );
  assert.notEqual(app.elements.get("healthText").textContent, "Task active");
}

async function testTerminalReceiptOverridesRawDoneAndRendersTrustedEvidence() {
  const app = await runApp({
    publicAccess: true,
    taskPayload: {
      id: "goal-1",
      status: "done",
      status_label: "Done",
      result_category: "failed",
      summary: "Worker says everything is done.",
      progress: { determinate: true, percent: 100 },
      current: { cycle: 2, current_subgoal: "finished", checkpoint: "claimed_done" },
      changed_files: [{ status: "modified", path: "src/app.py" }],
      verification: [
        {
          name: "command_passes",
          passed: false,
          message: "independent command failed",
          independent: true,
          source: "independent",
        },
      ],
      artifacts: [],
      final_result: {
        label: "Failed with evidence",
        accepted: false,
        summary: "Done state lacks passed independent verification.",
        reason: "Done state lacks passed independent verification.",
        worker_claim: {
          label: "Worker claim (untrusted)",
          trusted: false,
          summary: "Worker says everything is done.",
        },
        attempts: 2,
        retries: 1,
        review_attempts: [
          {
            number: 1,
            source: "current",
            passed: false,
            summary: "independent command failed",
            checks: [
              {
                name: "command_passes",
                passed: false,
                message: "independent command failed",
                independent: true,
                source: "independent",
              },
            ],
          },
        ],
        what_changed: [{ status: "modified", path: "src/app.py" }],
        checks: [],
        remaining: ["Verification must pass"],
      },
      allowed_actions: [{ action: "new_task", enabled: true }],
    },
  });

  assert.equal(app.elements.get("statusLabel").textContent, "Failed with evidence");
  assert.equal(
    app.elements.get("summary").textContent,
    "Done state lacks passed independent verification.",
  );
  assert.equal(app.elements.get("finalResult").hidden, false);
  assert.equal(app.elements.get("finalLabel").textContent, "Failed with evidence");
  assert.equal(
    app.elements.get("finalReason").textContent,
    "Done state lacks passed independent verification.",
  );
  assert.equal(app.elements.get("finalWorkerClaimLabel").textContent, "Assistant report");
  assert.equal(app.elements.get("finalWorkerClaim").textContent, "Worker says everything is done.");
  assert.equal(app.elements.get("finalAttempts").textContent, "2");
  assert.equal(app.elements.get("finalRetries").textContent, "1");
  assert.equal(app.elements.get("attemptsValue").textContent, "2");
  assert.equal(app.elements.get("checkpoint").textContent, "claimed done");
  assert.equal(app.elements.get("workDetailGrid").hidden, true);
  assert.equal(app.elements.get("activitySection").hidden, true);
  assert.equal(app.elements.get("changedFilesEvidence").hidden, true);
  assert.equal(app.elements.get("verificationEvidence").hidden, true);
  assert.equal(app.elements.get("artifactsEvidence").hidden, true);
  assert.equal(app.elements.get("completedDetails").hidden, false);
  assert.equal(app.elements.get("completedDetails").open, false);
  assert.equal(app.elements.get("finalChangedFiles").children[0].children[0].textContent, "modified: src/app.py");
  assert.match(
    app.elements.get("finalVerification").children[0].textContent,
    /Attempt 1 · independent · Failed: independent command failed/,
  );
  assert.notEqual(app.elements.get("statusLabel").textContent, "Done");
}

async function testRawDoneWithoutTrustedReceiptRemainsUnverified() {
  const app = await runApp({
    publicAccess: true,
    taskPayload: {
      status: "done",
      status_label: "Done",
      summary: "Worker-only completion claim",
      final_result: { accepted: true, summary: "Worker-only completion claim" },
    },
  });

  assert.equal(app.elements.get("statusLabel").textContent, "Checking evidence");
  assert.equal(app.elements.get("summary").textContent, "Completion is not verified yet.");
  assert.equal(app.elements.get("finalResult").hidden, true);

  app.context.renderHistory([
    {
      id: "legacy-done",
      objective: "Legacy completion claim",
      status: "done",
      status_label: "Done",
    },
  ]);
  assert.equal(
    app.elements.get("historyList").children[0].children[0].textContent,
    "Checking evidence: Legacy completion claim",
  );
}

(async () => {
  await testConcurrentStartup401sShareOnePromptAndAllRetry();
  await testStaleTokenIsClearedPromptedOnceAndReplaced();
  await testCancelResolvesAllWaitersWithoutSecondDialogOrLeakingToken();
  await testLegacySetupContractCompletesBootstrapAndAttachesStatusStream();
  await testFreshSetupOpensOnceAndSelectsTheRecommendedDetectedAgent();
  await testConfiguredVerificationReplacesOnlyThePreviousSuggestion();
  await testRunRequiresObjectiveAndEffectiveVerificationAndUsesSessionDraft();
  await testLegacyHumanCanChooseEveryModeWithoutWritingACommand();
  await testGoalStartersAndCompactModeSelectorKeepPlainLanguageDraftState();
  await testCompletedGoalClearsOnlyItsMatchingStaleDraft();
  await testCompletedGoalPreservesASecondGenerationNewDraft();
  await testPausedBackgroundAssistantIsNotCalledAnActiveTask();
  await testTerminalReceiptOverridesRawDoneAndRendersTrustedEvidence();
  await testRawDoneWithoutTrustedReceiptRemainsUnverified();
})();
