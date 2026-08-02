// Local Network Portal loader.
//
// The portal ships with zero service topology. At load it fetches
// "services.local.json" (a relative URL, so it resolves next to portal.html
// at any mount, including reverse-proxy path prefixes) and renders one link
// card per entry. That file is written by the operator on the deployment
// machine and is gitignored; the tracked services.example.json documents the
// expected shape:
//
//   { "services": [ { "label": "...", "url": "https://...", "note": "..." } ] }
//
// A bare top-level array of entries is accepted too. "label" and "url" are
// required per entry; "note" is optional. Every failure mode falls back to the
// empty state that tells the operator how to create the file: a missing file
// shows just those instructions, while a transport failure, a non-404 HTTP
// status, invalid JSON, the wrong top-level shape, or entries missing a label
// or a usable http(s) url also show a notice naming the specific problem.
// Nothing here ever throws, spins forever, or renders a blank page.
"use strict";

(function () {
  const list = document.getElementById("serviceList");
  const empty = document.getElementById("portalEmpty");
  const status = document.getElementById("portalStatus");

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
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        return parsed.href;
      }
    } catch (error) {
      return "";
    }
    return "";
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
    if (rendered === 0) {
      showStatus(
        skipped === 1
          ? "services.local.json has 1 entry, but it is unusable: every entry needs a non-empty \"label\" and an http:// or https:// \"url\"."
          : "services.local.json has " + skipped + " entries, but none are usable: every entry needs a non-empty \"label\" and an http:// or https:// \"url\".",
      );
      showEmpty();
      return;
    }
    if (skipped > 0) {
      showStatus(
        "Skipped " + skipped + (skipped === 1 ? " entry" : " entries") +
          " in services.local.json: every entry needs a non-empty \"label\" and an http:// or https:// \"url\".",
      );
    } else {
      showStatus("");
    }
    empty.hidden = true;
    list.hidden = false;
  }

  async function loadServices() {
    let response;
    try {
      // Relative on purpose: resolves next to portal.html at any mount.
      response = await fetch("services.local.json", { cache: "no-store" });
    } catch (error) {
      // The page itself loaded, so this is a transport problem, not a missing
      // file: say so instead of implying the operator forgot to create it.
      showStatus(
        "Could not reach the server to load services.local.json. Check that the harness is still running, then reload.",
      );
      showEmpty();
      return;
    }
    if (response.status === 404) {
      // The normal "operator has not created the file yet" case: the setup
      // instructions in the empty state are the whole message.
      showEmpty();
      return;
    }
    if (!response.ok) {
      showStatus(
        "The server returned HTTP " + response.status + " for services.local.json. Check the file's permissions on the machine running the harness.",
      );
      showEmpty();
      return;
    }
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      showStatus("services.local.json exists but is not valid JSON. Fix it and reload.");
      showEmpty();
      return;
    }
    let entries;
    if (Array.isArray(payload)) {
      entries = payload;
    } else if (payload && typeof payload === "object" && Array.isArray(payload.services)) {
      entries = payload.services;
    } else {
      showStatus(
        'services.local.json is valid JSON but the wrong shape. It must be {"services": [ ... ]} or a bare array of entries.',
      );
      showEmpty();
      return;
    }
    if (entries.length === 0) {
      showStatus("");
      showEmpty();
      return;
    }
    renderServices(entries);
  }

  loadServices();
})();
