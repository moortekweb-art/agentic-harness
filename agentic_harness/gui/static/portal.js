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
// A bare top-level array of entries is accepted too. Missing file, invalid
// JSON, or an empty list all fall back to a friendly empty state that tells
// the operator how to create the file.
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
    for (const entry of entries) {
      if (typeof entry !== "object" || entry === null) continue;
      const label = String(entry.label || "").trim();
      const href = safeHref(entry.url);
      if (!label || !href) continue;

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
      showEmpty();
      return;
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
      showEmpty();
      return;
    }
    if (!response.ok) {
      // 404 is the normal "operator has not created the file yet" case.
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
    const entries = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.services)
        ? payload.services
        : [];
    if (entries.length === 0) {
      showEmpty();
      return;
    }
    renderServices(entries);
  }

  loadServices();
})();
