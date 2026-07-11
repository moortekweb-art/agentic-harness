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
  constructor(document) {
    super();
    this.document = document;
    this.returnValue = "";
    this.input = new Element("authTokenInput");
  }

  querySelector(selector) {
    return selector === "input" ? this.input : null;
  }

  showModal() {
    this.document.openDialogs.push(this);
  }

  remove() {
    this.document.removedDialogs += 1;
    this.document.openDialogs = this.document.openDialogs.filter((dialog) => dialog !== this);
  }

  close(returnValue = "") {
    this.returnValue = returnValue;
    this.listeners.close();
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

function okPayloadFor(url, setupPayload = null) {
  if (url === "/api/health") {
    return { ok: true, local_goal_available: true, readiness: { state: "ready", can_start: true } };
  }
  if (url === "/api/modes") {
    return { modes: [{ key: "cloud", label: "Cloud", best_for: "tests", caution: "" }] };
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
    return { status: "ready", status_label: "Ready", summary: "Backend ready", progress: 0 };
  }
  if (url.startsWith("/api/tasks/history")) {
    return { tasks: [] };
  }
  return { ok: true };
}

async function tick() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function runApp({ initialToken = "", publicAccess = false, setupPayload = null } = {}) {
  const elements = new Map();
  const document = {
    body: new Element("body"),
    documentElement: new Element("html"),
    openDialogs: [],
    removedDialogs: 0,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element(id));
      return elements.get(id);
    },
    createElement(tag) {
      return tag === "dialog" ? new Dialog(document) : new Element();
    },
    addEventListener() {},
  };
  const sessionStorage = storage(initialToken ? { "agentic-harness-gui-session-token": initialToken } : {});
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
      fetchCalls.push({ url, auth });
      if (!publicAccess && auth !== "Bearer correct-token") {
        return { status: 401, ok: false, json: async () => ({ ok: false }) };
      }
      return { status: 200, ok: true, json: async () => okPayloadFor(url, setupPayload) };
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
  return { context, document, elements, fetchCalls, sessionStorage, websocketUrls, consoleErrors };
}

async function testConcurrentStartup401sShareOnePromptAndAllRetry() {
  const app = await runApp();
  assert.equal(app.document.openDialogs.length, 1);
  assert.equal(app.fetchCalls.length, 4);
  assert.deepEqual(
    app.fetchCalls.map((call) => call.auth),
    [null, null, null, null],
  );

  app.document.openDialogs[0].input.value = "correct-token";
  app.document.openDialogs[0].close("confirm");
  await tick();
  await tick();

  assert.equal(app.document.removedDialogs, 1);
  assert.equal(app.document.openDialogs.length, 0);
  assert.equal(app.fetchCalls.length, 8);
  assert.equal(app.fetchCalls.slice(4).every((call) => call.auth === "Bearer correct-token"), true);
  assert.equal(app.elements.get("summary").textContent, "Backend ready");
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-session-token"), "correct-token");
  assert.equal(app.websocketUrls.length, 0);
  assert.deepEqual(app.consoleErrors, []);
}

async function testStaleTokenIsClearedPromptedOnceAndReplaced() {
  const app = await runApp({ initialToken: "stale-token" });
  assert.equal(app.document.openDialogs.length, 1);
  assert.equal(app.fetchCalls.length, 4);
  assert.equal(app.fetchCalls.every((call) => call.auth === "Bearer stale-token"), true);
  assert.equal(app.sessionStorage.getItem("agentic-harness-gui-session-token"), null);

  app.document.openDialogs[0].input.value = "correct-token";
  app.document.openDialogs[0].close("confirm");
  await tick();
  await tick();

  assert.equal(app.document.removedDialogs, 1);
  assert.equal(app.fetchCalls.length, 8);
  assert.equal(app.fetchCalls.slice(4).every((call) => call.auth === "Bearer correct-token"), true);
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
  assert.equal(app.fetchCalls.length, 4);
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

  assert.equal(app.elements.get("workspacePath").textContent, "/tmp/legacy-workspace");
  assert.equal(app.elements.get("executionSummary").textContent, "Existing local-goal runtime");
  assert.equal(app.elements.get("setupButton").hidden, true);
  assert.deepEqual(app.websocketUrls, ["ws://127.0.0.1:41111/api/tasks/stream"]);
  assert.equal(app.fetchCalls.some((call) => call.url === "/api/setup"), true);
  assert.deepEqual(app.consoleErrors, []);
}

(async () => {
  await testConcurrentStartup401sShareOnePromptAndAllRetry();
  await testStaleTokenIsClearedPromptedOnceAndReplaced();
  await testCancelResolvesAllWaitersWithoutSecondDialogOrLeakingToken();
  await testLegacySetupContractCompletesBootstrapAndAttachesStatusStream();
})();
