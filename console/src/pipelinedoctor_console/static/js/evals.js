// Eval ledger page: one inline-SVG line chart (accuracy per run, threshold as a dashed rule)
// and the ledger table. No chart library: about a hundred lines of SVG is enough.
"use strict";

(function () {
  const { h, clear, link, fetchJSON, fmtDateTime, fmtNumber, fmtSeconds, fmtCost, copyText, showNotice } = window.PD;
  const SVG = "http://www.w3.org/2000/svg";

  function s(tag, attrs, text) {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function shortDate(iso) {
    const d = new Date(iso);
    return String(d.getUTCMonth() + 1).padStart(2, "0") + "-" + String(d.getUTCDate()).padStart(2, "0");
  }

  function drawChart(frame, entries) {
    clear(frame);
    const points = entries.filter((e) => typeof e.category_accuracy === "number");
    if (points.length < 2) {
      frame.appendChild(h("p", { class: "muted" }, points.length
        ? "One run recorded; the chart starts with the second."
        : "No eval runs recorded yet."));
      return;
    }
    // Draw at the frame's real pixel width so axis text stays legible on a phone.
    const W = Math.max(300, Math.min(920, Math.round(frame.clientWidth || 720)));
    const narrow = W < 560;
    const H = narrow ? 220 : 260;
    const M = { top: 14, right: narrow ? 44 : 96, bottom: 30, left: 40 };
    const threshold = points[points.length - 1].thresholds && points[points.length - 1].thresholds.category_accuracy;
    const values = points.map((p) => p.category_accuracy).concat(typeof threshold === "number" ? [threshold] : []);
    const lo = Math.max(0, Math.floor((Math.min.apply(null, values) - 0.05) * 10) / 10);
    const hi = 1;
    const x = (i) => M.left + (i * (W - M.left - M.right)) / (points.length - 1);
    const y = (v) => M.top + ((hi - v) * (H - M.top - M.bottom)) / (hi - lo);

    const svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-labelledby": "chart-title" });
    svg.appendChild(s("title", { id: "chart-title" },
      "Category accuracy for " + points.length + " runs, latest " + fmtNumber(points[points.length - 1].category_accuracy, 3)));

    for (let v = lo; v <= hi + 1e-9; v += 0.1) {
      const yy = y(v);
      svg.appendChild(s("line", { class: v === lo ? "ax-base" : "ax-grid", x1: M.left, x2: W - M.right, y1: yy, y2: yy }));
      svg.appendChild(s("text", { class: "ax-label", x: M.left - 8, y: yy + 4, "text-anchor": "end" }, v.toFixed(1)));
    }
    const every = Math.ceil(points.length / Math.max(2, Math.floor((W - M.left - M.right) / 64)));
    points.forEach((p, i) => {
      const last = points.length - 1;
      // Regular ticks stop short of the final one so the two labels never overlap.
      if ((i % every === 0 && last - i >= Math.ceil(every / 2) + 1) || i === last) {
        svg.appendChild(s("text", { class: "ax-label", x: x(i), y: H - M.bottom + 18, "text-anchor": "middle" }, shortDate(p.recorded_at)));
      }
    });
    if (typeof threshold === "number") {
      svg.appendChild(s("line", { class: "threshold", x1: M.left, x2: W - M.right, y1: y(threshold), y2: y(threshold) }));
      svg.appendChild(narrow
        ? s("text", { class: "threshold-label", x: W - M.right, y: y(threshold) + 15, "text-anchor": "end" }, "threshold " + threshold.toFixed(2))
        : s("text", { class: "threshold-label", x: W - M.right + 8, y: y(threshold) + 4 }, "threshold " + threshold.toFixed(2)));
    }
    const path = points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.category_accuracy).toFixed(1)).join(" ");
    svg.appendChild(s("path", { class: "series", d: path }));
    const last = points[points.length - 1];
    svg.appendChild(s("text", { class: "ax-label", x: x(points.length - 1) + 10, y: y(last.category_accuracy) - 8 },
      fmtNumber(last.category_accuracy, 3)));

    const tip = h("div", { class: "chart-tip", hidden: true });
    points.forEach((p, i) => {
      const cx = x(i), cy = y(p.category_accuracy);
      const label = shortDate(p.recorded_at) + ", commit " + p.git_sha.slice(0, 7) + ", accuracy " +
        fmtNumber(p.category_accuracy, 3) + (p.passed ? ", passed" : ", failed");
      // A generous invisible hit target sits before the visible marker so hover/focus can style it.
      const hit = s("rect", { class: "hit", x: cx - 12, y: M.top, width: 24, height: H - M.top - M.bottom, tabindex: 0, "aria-label": label });
      const mark = p.passed
        ? s("rect", { class: "pt", x: cx - 4.5, y: cy - 4.5, width: 9, height: 9 })
        : s("rect", { class: "pt pt-fail", x: cx - 5, y: cy - 5, width: 10, height: 10, transform: "rotate(45 " + cx + " " + cy + ")" });
      function show() {
        clear(tip);
        tip.appendChild(h("div", null, h("span", { class: "mono" }, fmtDateTime(p.recorded_at) + " · " + p.git_sha.slice(0, 7))));
        tip.appendChild(h("div", null, "Accuracy " + fmtNumber(p.category_accuracy, 3) + " · " + (p.passed ? "passed" : "failed")));
        tip.style.left = (cx / W) * 100 + "%";
        tip.style.top = (cy / H) * 100 + "%";
        tip.hidden = false;
      }
      function hide() { tip.hidden = true; }
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("focus", show);
      hit.addEventListener("blur", hide);
      svg.appendChild(hit);
      svg.appendChild(mark);
    });
    frame.appendChild(svg);
    frame.appendChild(tip);
  }

  function metric(value, key, label, thresholds, digits) {
    const t = thresholds && thresholds[key];
    const below = typeof value === "number" && typeof t === "number" && value < t;
    return h("td", { class: "num" + (below ? " below" : ""), "data-label": label,
      title: typeof t === "number" ? "threshold " + t : null }, fmtNumber(value, digits));
  }

  function row(e) {
    const failures = e.failures && e.failures.length ? h("span", { class: "failures" }, e.failures.join("; ")) : null;
    return h("tr", null,
      h("td", { class: "date", "data-label": "Recorded" }, link(e.run_url, fmtDateTime(e.recorded_at))),
      h("td", { "data-label": "Commit" }, copyText(e.git_sha, "Commit SHA", e.git_sha.slice(0, 7))),
      h("td", { class: "mono", "data-label": "Prompt" }, e.prompt_version || "—"),
      h("td", { class: "num", "data-label": "Cases" }, (e.cases_scored != null ? e.cases_scored : "—") + "/" + (e.cases_total != null ? e.cases_total : "—")),
      metric(e.category_accuracy, "category_accuracy", "Accuracy", e.thresholds, 3),
      metric(e.evidence_validity, "evidence_validity", "Evidence", e.thresholds, 3),
      metric(e.key_fact_recall, "key_fact_recall", "Recall", e.thresholds, 3),
      metric(e.judge_score_mean, "judge_score_mean", "Judge", e.thresholds, 2),
      h("td", { class: "num", "data-label": "p95" }, fmtSeconds(e.latency_p95_ms)),
      h("td", { class: "num", "data-label": "Cost" }, fmtCost(e.total_cost_usd)),
      h("td", { "data-label": "Verdict" },
        h("span", { class: "status " + (e.passed ? "verdict-pass" : "verdict-fail") }, e.passed ? "Pass" : "Fail"),
        failures));
  }

  async function load() {
    const body = document.getElementById("ledger-body");
    const frame = document.getElementById("accuracy-chart");
    const meta = document.getElementById("ledger-meta");
    const res = await fetchJSON("/api/evals");
    clear(body);
    if (!res.ok || !res.data) {
      body.appendChild(h("tr", { class: "empty" }, h("td", { colspan: "11" }, "The ledger could not be retrieved.")));
      clear(frame);
      return;
    }
    const entries = res.data.entries || [];
    showNotice("evals-notice", res.data.notice);
    drawChart(frame, entries);
    let timer = null;
    let lastWidth = frame.clientWidth;
    window.addEventListener("resize", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (frame.clientWidth !== lastWidth) { lastWidth = frame.clientWidth; drawChart(frame, entries); }
      }, 150);
    });
    if (!entries.length) {
      body.appendChild(h("tr", { class: "empty" }, h("td", { colspan: "11" }, "No eval runs recorded yet. The first nightly run opens the ledger.")));
    }
    entries.slice().reverse().forEach((e) => body.appendChild(row(e)));
    const failed = entries.filter((e) => !e.passed).length;
    meta.textContent = entries.length + " run" + (entries.length === 1 ? "" : "s") + " · " + failed + " failed";
  }

  document.addEventListener("DOMContentLoaded", load);
})();
