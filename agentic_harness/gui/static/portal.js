// Local Network Portal loader.
//
// The portal ships with zero service topology. At load it asks the harness for
// the operator's service list over the authenticated API:
//
//   GET api/portal/services  ->  { ok, configured, services: [...], warnings }
//
// The list lives in an owner-controlled config file outside the installed
// package (AGENTIC_HARNESS_PORTAL_SERVICES, else
// $XDG_CONFIG_HOME/agentic-harness/services.json, else
// ~/.config/agentic-harness/services.json). It is deliberately NOT a static
// asset: private hostnames must never be readable without the session token,
// and the server 404s every *.local.json under static/ for that reason.
//
// The request URL is mount-relative, so the page works at the bare mount and
// behind a reverse-proxy path prefix, with or without a trailing slash. When
// the harness requires a token, the one the main GUI stored in sessionStorage
// is reused; a 401 asks the operator to sign in there rather than failing
// silently. Every failure mode reaches a readable state: loading, unconfigured,
// error, empty, or ready. Nothing here throws, spins forever, or renders a
// blank page.
"use strict";

(function () {
  // Same key the main GUI (app.js) writes after a successful token prompt.
  const TOKEN_KEY = "agentic-harness-gui-session-token";
  const SERVICES_PATH = "api/portal/services";

  const list = document.getElementById("serviceList");
  const empty = document.getElementById("portalEmpty");
  const status = document.getElementById("portalStatus");

  // Directory of the current document, so "api/..." resolves against the mount
  // rather than the last path segment. "/hub" (no trailing slash) and "/hub/"
  // must both produce "/hub/".
  function appRoot() {
    const path = String((window.location && window.location.pathname) || "/");
    if (path.endsWith("/")) return path;
    const last = path.lastIndexOf("/");
    const dir = path.slice(0, last + 1);
    // A bare "/hub" is the mount itself, not a file inside "/": treat the
    // final segment as the directory unless it looks like a document.
    if (/\.[a-z0-9]+$/i.test(path.slice(last + 1))) return dir;
    return path + "/";
  }

  function servicesUrl() {
    return appRoot() + SERVICES_PATH;
  }

  function storedToken() {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY) || "";
    } catch (error) {
      // Storage can be blocked entirely; that is just "no token".
      return "";
    }
  }

  function showStatus(message) {
    if (!message) {
      status.hidden = true;
      status.textContent = "";
      return;
    }
    status.textContent = message;
    status.hidden = false;
  }

  function showEmpty() {
    list.hidden = true;
    empty.hidden = false;
  }

  function safeHref(raw) {
    const value = String(raw || "").trim();
    if (!value) return "";
    try {
      const parsed = new URL(value, window.location.href);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      // The server already drops credential-bearing URLs; refuse them here too
      // so a compromised or stale response cannot put one in the DOM.
      if (parsed.username || parsed.password) return "";
      return parsed.href;
    } catch (error) {
      return "";
    }
  }

  function renderServices(entries) {
    list.textContent = "";
    let rendered = 0;
    let skipped = 0;
    for (const entry of entries) {
      if (typeof entry !== "object" || entry === null) {
        skipped += 1;
        continue;
      }
      const label = String(entry.label || "").trim();
      const href = safeHref(entry.url);
      if (!label || !href) {
        skipped += 1;
        continue;
      }

      const item = document.createElement("li");
      const link = document.createElement("a");
      link.className = "service";
      link.href = href;

      const name = document.createElement("span");
      name.className = "service-name";
      name.textContent = label;
      link.appendChild(name);

      const note = String(entry.note || "").trim();
      if (note) {
        const desc = document.createElement("span");
        desc.className = "service-desc";
        desc.textContent = note;
        link.appendChild(desc);
      }

      const url = document.createElement("span");
      url.className = "service-url";
      url.textContent = href;
      link.appendChild(url);

      item.appendChild(link);
      list.appendChild(item);
      rendered += 1;
    }
    return { rendered: rendered, skipped: skipped };
  }

  function describeSkipped(count, warnings) {
    const detail = warnings.join(" ");
    if (count === 0) return detail;
    const noun = count === 1 ? " entry" : " entries";
    const head = "Skipped " + count + noun + " from your service list.";
    return detail ? head + " " + detail : head;
  }

  async function loadServices() {
    showStatus("Loading your service list…");

    const headers = { Accept: "application/json" };
    const token = storedToken();
    if (token) headers.Authorization = "Bearer " + token;

    let response;
    try {
      response = await fetch(servicesUrl(), { cache: "no-store", headers: headers });
    } catch (error) {
      // The page itself loaded, so this is a transport problem, not a missing
      // configuration: say so instead of implying the operator forgot to
      // create the file.
      showStatus(
        "Could not reach the harness to load your service list. Check that it is still running, then reload.",
      );
      showEmpty();
      return;
    }

    if (response.status === 401 || response.status === 403) {
      showStatus(
        "Your service list needs an access token. Open the main Agentic Harness GUI, enter the token it asks for, then reload this page.",
      );
      showEmpty();
      return;
    }
    if (!response.ok) {
      showStatus(
        "The harness returned HTTP " + response.status + " for your service list. Check its logs, then reload.",
      );
      showEmpty();
      return;
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      showStatus("The harness sent an unreadable service list. Check its logs, then reload.");
      showEmpty();
      return;
    }
    if (!payload || typeof payload !== "object") {
      showStatus("The harness sent an unreadable service list. Check its logs, then reload.");
      showEmpty();
      return;
    }

    const warnings = Array.isArray(payload.warnings)
      ? payload.warnings.map((item) => String(item || "").trim()).filter(Boolean)
      : [];

    if (payload.error) {
      // The config file exists but could not be used. The server sends a
      // description of the problem and never the file's contents or path.
      showStatus("Your service list could not be read: " + String(payload.error));
      showEmpty();
      return;
    }
    if (!payload.configured) {
      // The normal "not set up yet" case: the setup instructions in the empty
      // state are the whole message.
      showStatus("");
      showEmpty();
      return;
    }

    const entries = Array.isArray(payload.services) ? payload.services : [];
    if (entries.length === 0) {
      showStatus(
        warnings.length
          ? "No usable services in your service list. " + warnings.join(" ")
          : "Your service list is configured but empty. Add an entry, then reload.",
      );
      showEmpty();
      return;
    }

    const counts = renderServices(entries);
    if (counts.rendered === 0) {
      showStatus(
        "No usable services in your service list: every entry needs a non-empty \"label\" and an http:// or https:// \"url\".",
      );
      showEmpty();
      return;
    }
    const skipped = counts.skipped;
    if (skipped > 0 || warnings.length) {
      showStatus(describeSkipped(skipped, warnings));
    } else {
      showStatus("");
    }
    empty.hidden = true;
    list.hidden = false;
  }

  loadServices();
})();
