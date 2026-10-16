// Shared helpers. Everything that comes from GitHub, a log or a language model is inserted with
// document.createTextNode / textContent only. There is no HTML string building anywhere in this
// console (a test fails the build if one appears).
"use strict";

(function () {
  // ---- Theme: applied before first paint; the choice is a per-browser convenience only ----
  const THEME_KEY = "pd-console-theme";
  function storedTheme() {
    try { return window.localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }
  function storeTheme(value) {
    try { window.localStorage.setItem(THEME_KEY, value); } catch (e) { /* private mode: ignore */ }
  }
  const initial = storedTheme();
  if (initial === "light" || initial === "dark") {
    document.documentElement.setAttribute("data-theme", initial);
  }
  function effectiveTheme() {
    const set = document.documentElement.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  // ---- DOM construction ----
  function h(tag, attrs) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = String(value);
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : String(value));
      }
    }
    for (let i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }
  function append(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach((c) => append(node, c)); return; }
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // Only http(s) links from the API become hrefs; anything else (javascript:, data:) is dropped.
  function safeUrl(url) {
    if (typeof url !== "string") return null;
    try {
      const parsed = new URL(url, window.location.origin);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch (e) {
      return null;
    }
  }
  function link(url, label, attrs) {
    const href = safeUrl(url);
    if (!href) return document.createTextNode(String(label));
    return h("a", Object.assign({ href: href }, attrs || {}), label);
  }

  // ---- Data ----
  async function fetchJSON(url) {
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      let data = null;
      try { data = await response.json(); } catch (e) { data = null; }
      return { ok: response.ok, status: response.status, data: data };
    } catch (e) {
      return { ok: false, status: 0, data: null };
    }
  }

  // ---- Formatting ----
  function pad(n) { return String(n).padStart(2, "0"); }
  function parseDate(iso) {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }
  function fmtDateTime(iso) {
    const d = parseDate(iso);
    if (!d) return "—";
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()) +
      " " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
  }
  function fmtTime(iso) {
    const d = parseDate(iso);
    if (!d) return "—";
    return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds());
  }
  function fmtNumber(value, digits) {
    return typeof value === "number" && isFinite(value) ? value.toFixed(digits) : "—";
  }
  function fmtSeconds(ms) {
    if (typeof ms !== "number") return "—";
    return ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s";
  }
  function fmtCost(usd) {
    return typeof usd === "number" ? "$" + usd.toFixed(4) : "—";
  }

  // ---- Category stamps ----
  const CATEGORY_LABELS = {
    dependency: "Dependency",
    test: "Test",
    lint: "Lint",
    build: "Build",
    docker: "Docker",
    config: "Config",
    secret_permission: "Secret / Permission",
    flaky: "Flaky",
    infrastructure: "Infrastructure",
    unknown: "Unknown",
  };
  function stamp(category, large) {
    const known = Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, category);
    const key = known ? category : "unknown";
    return h("span", {
      class: "stamp stamp-" + key + (large ? " stamp-lg" : ""),
      title: "Category: " + CATEGORY_LABELS[key],
    }, CATEGORY_LABELS[key]);
  }

  // ---- Copy to clipboard ----
  function announce(text) {
    const region = document.getElementById("announcer");
    if (region) region.textContent = text;
  }
  async function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = h("textarea", { class: "sr-only", readonly: true });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }
  function flashCopied(anchor) {
    const existing = anchor.parentNode && anchor.parentNode.querySelector(".copied-note");
    if (existing) existing.remove();
    const note = h("span", { class: "copied-note", "aria-hidden": "true" }, "copied");
    anchor.after(note);
    window.setTimeout(() => note.remove(), 1600);
  }
  async function copy(text, anchor, what) {
    try {
      await writeClipboard(text);
      flashCopied(anchor);
      announce((what || "Text") + " copied to clipboard");
    } catch (e) {
      announce("Copy failed");
    }
  }
  // Text that copies itself when clicked (occurrence numbers, SHAs).
  function copyText(text, what, display) {
    const button = h("button", { type: "button", class: "copy mono", title: "Copy " + what.toLowerCase() }, display || text);
    button.addEventListener("click", () => copy(text, button, what));
    return button;
  }
  // A small icon button, for places where the text itself is a link.
  function copyIcon(text, what) {
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("width", "13");
    svg.setAttribute("height", "13");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    // Two offset sheets of paper, drawn as plain rectangles.
    [["2.5", "4.5"], ["5.5", "1.5"]].forEach(([x, y], i) => {
      const r = document.createElementNS(svgNS, "rect");
      r.setAttribute("x", x); r.setAttribute("y", y);
      r.setAttribute("width", "8"); r.setAttribute("height", "10");
      r.setAttribute("fill", i === 1 ? "var(--paper)" : "none");
      r.setAttribute("stroke", "currentColor");
      r.setAttribute("stroke-width", "1.3");
      svg.appendChild(r);
    });
    const button = h("button", { type: "button", class: "copy-icon", "aria-label": "Copy " + what.toLowerCase() + " " + text, title: "Copy " + what.toLowerCase() }, svg);
    button.addEventListener("click", () => copy(text, button, what));
    return button;
  }

  function showNotice(id, text) {
    const node = document.getElementById(id);
    if (!node) return;
    node.hidden = !text;
    node.textContent = text || "";
  }

  document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.getElementById("theme-toggle");
    const label = document.getElementById("theme-label");
    if (!toggle || !label) return;
    function sync() {
      const dark = effectiveTheme() === "dark";
      label.textContent = dark ? "Day shift" : "Night shift";
      toggle.setAttribute("aria-pressed", dark ? "true" : "false");
      toggle.setAttribute("title", dark ? "Switch to the paper theme" : "Switch to the night-shift theme");
    }
    toggle.addEventListener("click", () => {
      const next = effectiveTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      storeTheme(next);
      sync();
    });
    sync();
  });

  window.PD = {
    h: h, append: append, clear: clear, link: link, safeUrl: safeUrl, fetchJSON: fetchJSON,
    fmtDateTime: fmtDateTime, fmtTime: fmtTime, fmtNumber: fmtNumber, fmtSeconds: fmtSeconds,
    fmtCost: fmtCost, parseDate: parseDate, stamp: stamp, copyText: copyText, copyIcon: copyIcon,
    showNotice: showNotice, announce: announce, CATEGORY_LABELS: CATEGORY_LABELS,
  };
})();
