// Occurrence register + pipeline health strip (home page).
"use strict";

(function () {
  const { h, clear, fetchJSON, fmtDateTime, fmtNumber, stamp, copyIcon, showNotice } = window.PD;

  function figure(value, digits, unit, detail) {
    const shown = typeof value === "number" ? fmtNumber(value, digits) : "—";
    return [
      h("span", { class: "figure" }, shown),
      typeof value === "number" && unit ? h("span", { class: "unit" }, unit) : null,
      h("span", { class: "figure-detail" }, typeof value === "number" ? detail : "insufficient data"),
    ];
  }

  async function loadHealth() {
    const row = document.getElementById("health-figures");
    const notes = document.getElementById("health-notes");
    const res = await fetchJSON("/api/dora?days=30");
    if (!res.ok || !res.data) {
      notes.textContent = "Deployment records could not be retrieved.";
      return;
    }
    const d = res.data;
    const cells = row.querySelectorAll("td");
    const cfr = typeof d.change_failure_rate === "number" ? d.change_failure_rate * 100 : null;
    const values = [
      figure(d.deployment_frequency_per_week, 2, "per week", d.deployments + " deployment record" + (d.deployments === 1 ? "" : "s")),
      figure(d.lead_time_hours_median, 1, "h", "median, commit to production"),
      figure(cfr, 0, "%", "of deployments in window"),
      figure(d.time_to_restore_hours_median, 1, "h", "median"),
    ];
    cells.forEach((cell, i) => { clear(cell); values[i].forEach((n) => n && cell.appendChild(n)); });
    const text = (d.notes || []).concat(d.notice ? [d.notice] : []);
    notes.textContent = text.join(" ");
  }

  const MISSING = {
    pending: "investigation under way",
    skipped: "no report filed",
    diagnosed: "no job started",
  };

  function chain(item) {
    const cell = h("td", { class: "chain" });
    const parts = [[item.workflow, null], [item.job, null], [item.failed_step, "step"]].filter((p) => p[0]);
    parts.forEach(([part, cls], i) => {
      if (i > 0) cell.appendChild(h("span", { class: "sep", "aria-hidden": "true" }, "→"));
      if (i > 0) cell.appendChild(h("span", { class: "sr-only" }, ", "));
      cell.appendChild(h("span", { class: cls }, part));
    });
    if (parts.length < 3) {
      if (parts.length) cell.appendChild(h("span", { class: "sep", "aria-hidden": "true" }, "·"));
      cell.appendChild(h("span", { class: "muted" }, MISSING[item.status] || "particulars unavailable"));
    }
    if (typeof item.pr_number === "number") {
      cell.appendChild(h("span", { class: "pr" }, "PR #" + item.pr_number));
    }
    return cell;
  }

  function statusCell(status) {
    const labels = { diagnosed: "Diagnosed", pending: "Pending", skipped: "Skipped" };
    const cell = h("td", { class: "col-status", "data-label": "Status" },
      h("span", { class: "status status-" + status }, labels[status] || status));
    if (status === "pending") {
      cell.appendChild(h("span", { class: "progress-rule", role: "presentation" }));
    }
    return cell;
  }

  function row(item) {
    const reportHref = "/occurrences/" + encodeURIComponent(item.occurrence_id);
    const grade = item.confidence_grade
      ? h("span", { class: "grade" }, h("b", null, item.confidence_grade), h("span", { class: "val" }, fmtNumber(item.confidence, 2)))
      : h("span", { class: "muted" }, "—");
    return h("tr", null,
      h("td", { class: "occ-no" },
        h("span", null, h("a", { href: reportHref }, item.occurrence_id), copyIcon(item.occurrence_id, "Occurrence number"))),
      h("td", { class: "date", "data-label": "Recorded" }, fmtDateTime(item.created_at)),
      h("td", { class: "repo", "data-label": "Repository" }, item.repo),
      chain(item),
      h("td", { "data-label": "Category", class: item.category ? null : "blank" },
        item.category ? stamp(item.category) : h("span", { class: "muted" }, "—")),
      h("td", { "data-label": "Confidence", class: item.confidence_grade ? null : "blank" }, grade),
      statusCell(item.status));
  }

  async function loadRegister() {
    const body = document.getElementById("register-body");
    const meta = document.getElementById("register-meta");
    const res = await fetchJSON("/api/occurrences?limit=50");
    clear(body);
    if (!res.ok || !res.data) {
      body.appendChild(h("tr", { class: "empty" }, h("td", { colspan: "7" }, "The register could not be retrieved. Try again shortly.")));
      return;
    }
    const data = res.data;
    showNotice("register-notice", data.notice);
    if (!data.items.length) {
      body.appendChild(h("tr", { class: "empty" }, h("td", { colspan: "7" }, "No occurrences recorded. All pipelines nominal.")));
    } else {
      data.items.forEach((item) => body.appendChild(row(item)));
    }
    const n = data.items.length;
    meta.textContent = n + (n === 1 ? " entry" : " entries") + " · retrieved " + fmtDateTime(data.fetched_at) + " UTC";
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadHealth();
    loadRegister();
  });
})();
