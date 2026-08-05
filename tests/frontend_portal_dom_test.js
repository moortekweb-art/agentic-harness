// Executable DOM test for the Local Network Portal loader (portal.js).
//
// The portal renders private-network topology, so its states are a
// confidentiality surface, not just cosmetics. This runs the real portal.js in
// a stub DOM with a stub fetch — no test framework, no new dependencies, same
// pattern as tests/frontend_token_race_test.js — and asserts each state the
// operator can actually reach: loading, unauthenticated, transport failure,
// non-OK status, unreadable payload, broken configuration, unconfigured,
// empty, ready, and partially-usable. It also pins the two properties that
// carry the security guarantee: the request is authenticated with the token
// the main GUI stored, and its URL is mount-relative so "/hub" and "/hub/"
// both address the prefixed API rather than escaping to the root.
//
//   node tests/frontend_portal_dom_test.js

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const portalPath =
  process.env.PORTAL_JS_PATH || path.join(process.cwd(), "agentic_harness/gui/static/portal.js");
const portalSource = fs.readFileSync(portalPath, "utf8");
const TOKEN_KEY = "agentic-harness-gui-session-token";

class Element {
  constructor(id = "", tag = "") {
    this.id = id;
    this.tag = tag;
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.href = "";
    this.children = [];
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  // Every descendant, so assertions can look at rendered links directly.
  descendants() {
    return this.children.flatMap((child) => [child, ...child.descendants()]);
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
  };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

/**
 * Run portal.js once.
 *
 * @param {object} options
 *   token      - value in sessionStorage, or null for none
 *   pathname   - document location, e.g. "/", "/hub", "/hub/portal.html"
 *   respond    - (url, options) => response-like object, or throws for a
 *                transport failure
 *   storageThrows - make sessionStorage access throw (blocked storage)
 */
async function runPortal({ token = null, pathname = "/", respond, storageThrows = false } = {}) {
  const elements = new Map();
  for (const id of ["serviceList", "portalEmpty", "portalStatus"]) {
    elements.set(id, new Element(id));
  }
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element(id));
      return elements.get(id);
    },
    createElement(tag) {
      return new Element("", tag);
    },
  };

  const session = storage(token === null ? {} : { [TOKEN_KEY]: token });
  const sessionStorage = storageThrows
    ? {
        getItem() {
          throw new Error("storage is blocked");
        },
        setItem() {
          throw new Error("storage is blocked");
        },
        removeItem() {},
      }
    : session;

  const fetchCalls = [];
  const href = `http://127.0.0.1:8769${pathname}`;
  const context = {
    console: { log() {}, error() {} },
    document,
    URL,
    setTimeout,
    fetch: async (url, options = {}) => {
      fetchCalls.push({ url, options });
      return respond(url, options);
    },
    window: {
      location: { pathname, href, protocol: "http:", host: "127.0.0.1:8769" },
      sessionStorage,
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(portalSource, context, { filename: portalPath });
  await tick();
  await tick();
  await tick();

  const list = elements.get("serviceList");
  return {
    fetchCalls,
    list,
    empty: elements.get("portalEmpty"),
    status: elements.get("portalStatus"),
    // Rendered links, in order.
    links: list
      .descendants()
      .filter((node) => node.tag === "a")
      .map((node) => ({
        href: node.href,
        text: node.descendants().map((child) => child.textContent),
      })),
  };
}

const ok = (payload) => async () => ({
  status: 200,
  ok: true,
  json: async () => payload,
});

// --------------------------------------------------------------------------

async function testReadyStateRendersEveryConfiguredService() {
  const view = await runPortal({
    respond: ok({
      ok: true,
      configured: true,
      services: [
        { label: "Dashboard", url: "https://a.example.invalid/", note: "The panel" },
        { label: "Cluster", url: "https://b.example.invalid/cluster/" },
      ],
      warnings: [],
    }),
  });

  assert.equal(view.list.hidden, false, "the list must be visible");
  assert.equal(view.empty.hidden, true, "the empty state must be hidden");
  assert.equal(view.status.hidden, true, "no notice when everything is usable");
  assert.deepEqual(
    view.links.map((link) => link.href),
    ["https://a.example.invalid/", "https://b.example.invalid/cluster/"],
  );
  assert.ok(view.links[0].text.includes("Dashboard"));
  assert.ok(view.links[0].text.includes("The panel"));
}

async function testUnconfiguredShowsOnlySetupInstructions() {
  const view = await runPortal({
    respond: ok({ ok: true, configured: false, services: [], warnings: [] }),
  });

  assert.equal(view.empty.hidden, false, "the setup instructions must show");
  assert.equal(view.list.hidden, true);
  // No scary notice: not being set up yet is the normal first-run state.
  assert.equal(view.status.hidden, true);
  assert.equal(view.status.textContent, "");
}

async function testConfiguredButEmptySaysSoDistinctly() {
  const view = await runPortal({
    respond: ok({ ok: true, configured: true, services: [], warnings: [] }),
  });

  assert.equal(view.empty.hidden, false);
  assert.equal(view.status.hidden, false);
  assert.match(view.status.textContent, /configured but empty/);
}

async function testBrokenConfigurationReportsTheServerReason() {
  const view = await runPortal({
    respond: ok({
      ok: true,
      configured: true,
      services: [],
      warnings: [],
      error: "portal configuration is not valid JSON",
    }),
  });

  assert.equal(view.empty.hidden, false);
  assert.equal(view.status.hidden, false);
  assert.match(view.status.textContent, /could not be read/);
  assert.match(view.status.textContent, /not valid JSON/);
}

async function testUnauthenticatedStateDirectsToTheTokenFlow() {
  for (const status of [401, 403]) {
    const view = await runPortal({
      respond: async () => ({ status, ok: false, json: async () => ({ ok: false }) }),
    });

    assert.equal(view.empty.hidden, false, `status ${status} must not blank the page`);
    assert.equal(view.status.hidden, false);
    assert.match(view.status.textContent, /access token/, `status ${status}`);
    assert.match(view.status.textContent, /main Agentic Harness GUI/, `status ${status}`);
  }
}

async function testServerErrorNamesTheStatus() {
  const view = await runPortal({
    respond: async () => ({ status: 500, ok: false, json: async () => ({}) }),
  });

  assert.equal(view.empty.hidden, false);
  assert.match(view.status.textContent, /HTTP 500/);
}

async function testTransportFailureIsNotReportedAsMissingConfiguration() {
  const view = await runPortal({
    respond: async () => {
      throw new TypeError("network down");
    },
  });

  assert.equal(view.empty.hidden, false, "must not throw or blank the page");
  assert.equal(view.status.hidden, false);
  assert.match(view.status.textContent, /Could not reach the harness/);
  // Crucially: it must not claim the operator forgot to configure anything.
  assert.doesNotMatch(view.status.textContent, /configured but empty/);
}

async function testUnreadablePayloadsNeverThrow() {
  const responses = [
    async () => ({
      status: 200,
      ok: true,
      json: async () => {
        throw new SyntaxError("bad json");
      },
    }),
    ok(null),
    ok("a string"),
    ok(42),
  ];
  for (const respond of responses) {
    const view = await runPortal({ respond });
    assert.equal(view.empty.hidden, false);
    assert.equal(view.status.hidden, false);
    assert.ok(view.status.textContent.length > 0);
    assert.equal(view.links.length, 0);
  }
}

async function testMissingServicesArrayIsTreatedAsEmpty() {
  const view = await runPortal({
    respond: ok({ ok: true, configured: true, warnings: [] }),
  });

  assert.equal(view.empty.hidden, false);
  assert.equal(view.links.length, 0);
}

async function testUnsafeUrlsAreNeverPutInTheDom() {
  const view = await runPortal({
    respond: ok({
      ok: true,
      configured: true,
      services: [
        { label: "Fine", url: "https://a.example.invalid/" },
        { label: "Script", url: "javascript:alert(1)" },
        { label: "File", url: "file:///etc/passwd" },
        { label: "Data", url: "data:text/html,<b>x</b>" },
        { label: "Creds", url: "https://user:pw@b.example.invalid/" },
        { label: "No url", url: "" },
        { url: "https://c.example.invalid/" },
        "not-an-object",
        null,
      ],
      warnings: [],
    }),
  });

  // Only the safe entry survives, even though the server should already have
  // dropped the rest: the renderer never trusts the payload.
  assert.deepEqual(
    view.links.map((link) => link.href),
    ["https://a.example.invalid/"],
  );
  const rendered = JSON.stringify(view.links);
  assert.doesNotMatch(rendered, /javascript:/);
  assert.doesNotMatch(rendered, /file:/);
  assert.doesNotMatch(rendered, /data:/);
  assert.doesNotMatch(rendered, /pw@/);
  // Partially usable: the list shows, with a notice about what was dropped.
  assert.equal(view.list.hidden, false);
  assert.equal(view.status.hidden, false);
  assert.match(view.status.textContent, /Skipped 8 entries/);
}

async function testAllEntriesUnusableFallsBackToTheEmptyState() {
  const view = await runPortal({
    respond: ok({
      ok: true,
      configured: true,
      services: [{ label: "Script", url: "javascript:alert(1)" }],
      warnings: [],
    }),
  });

  assert.equal(view.links.length, 0);
  assert.equal(view.empty.hidden, false);
  assert.match(view.status.textContent, /No usable services/);
}

async function testServerWarningsAreShownToTheOperator() {
  const view = await runPortal({
    respond: ok({
      ok: true,
      configured: true,
      services: [{ label: "Fine", url: "https://a.example.invalid/" }],
      warnings: ["skipped Signed: url query parameter 'sig' looks like a credential"],
    }),
  });

  assert.equal(view.list.hidden, false);
  assert.equal(view.status.hidden, false);
  assert.match(view.status.textContent, /looks like a credential/);
  // Nothing was skipped client-side, so it must not claim otherwise.
  assert.doesNotMatch(view.status.textContent, /Skipped 0/);
}

async function testRequestIsAuthenticatedWithTheStoredToken() {
  const view = await runPortal({
    token: "stored-token",
    respond: ok({ ok: true, configured: true, services: [], warnings: [] }),
  });

  assert.equal(view.fetchCalls.length, 1);
  const call = view.fetchCalls[0];
  assert.equal(call.options.headers.Authorization, "Bearer stored-token");
  assert.equal(call.options.cache, "no-store");
}

async function testNoTokenSendsNoAuthorizationHeader() {
  const view = await runPortal({
    respond: ok({ ok: true, configured: true, services: [], warnings: [] }),
  });

  assert.equal(view.fetchCalls[0].options.headers.Authorization, undefined);
}

async function testBlockedStorageDegradesToAnUnauthenticatedRequest() {
  const view = await runPortal({
    storageThrows: true,
    respond: ok({ ok: true, configured: true, services: [], warnings: [] }),
  });

  // Must not throw; simply behaves as "no token".
  assert.equal(view.fetchCalls.length, 1);
  assert.equal(view.fetchCalls[0].options.headers.Authorization, undefined);
}

async function testRequestUrlIsMountRelativeAtEveryPrefix() {
  const cases = [
    ["/", "/api/portal/services"],
    ["/portal.html", "/api/portal/services"],
    ["/index.html", "/api/portal/services"],
    // The no-trailing-slash mount is the regression that escaped the prefix.
    ["/hub", "/hub/api/portal/services"],
    ["/hub/", "/hub/api/portal/services"],
    ["/hub/portal.html", "/hub/api/portal/services"],
    ["/a/b/portal.html", "/a/b/api/portal/services"],
  ];

  for (const [pathname, expected] of cases) {
    const view = await runPortal({
      pathname,
      respond: ok({ ok: true, configured: true, services: [], warnings: [] }),
    });
    assert.equal(view.fetchCalls[0].url, expected, `pathname ${pathname}`);
    // Never the static file the portal used to read, and never root-absolute
    // when mounted under a prefix.
    assert.doesNotMatch(view.fetchCalls[0].url, /services\.local\.json/);
  }
}

async function testTheStaticServicesFileIsNeverRequested() {
  const view = await runPortal({
    respond: ok({ ok: true, configured: true, services: [], warnings: [] }),
  });

  for (const call of view.fetchCalls) {
    assert.doesNotMatch(call.url, /\.local\.json/);
  }
}

(async () => {
  await testReadyStateRendersEveryConfiguredService();
  await testUnconfiguredShowsOnlySetupInstructions();
  await testConfiguredButEmptySaysSoDistinctly();
  await testBrokenConfigurationReportsTheServerReason();
  await testUnauthenticatedStateDirectsToTheTokenFlow();
  await testServerErrorNamesTheStatus();
  await testTransportFailureIsNotReportedAsMissingConfiguration();
  await testUnreadablePayloadsNeverThrow();
  await testMissingServicesArrayIsTreatedAsEmpty();
  await testUnsafeUrlsAreNeverPutInTheDom();
  await testAllEntriesUnusableFallsBackToTheEmptyState();
  await testServerWarningsAreShownToTheOperator();
  await testRequestIsAuthenticatedWithTheStoredToken();
  await testNoTokenSendsNoAuthorizationHeader();
  await testBlockedStorageDegradesToAnUnauthenticatedRequest();
  await testRequestUrlIsMountRelativeAtEveryPrefix();
  await testTheStaticServicesFileIsNeverRequested();
  console.log("portal DOM tests passed");
})();
