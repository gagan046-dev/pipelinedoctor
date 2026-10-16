// Investigation report view: /occurrences/<occurrence id>.
"use strict";

(function () {
  const { h, clear, link, fetchJSON, fmtDateTime, fmtTime, fmtNumber, fmtSeconds, fmtCost,
    parseDate, stamp, copyText } = window.PD;
  const POLL_MS = 15000;
  const SOURCE_LABELS = { llm: "LLM", rules_fallback: "rule-based fallback", rules_only: "rules only (no LLM)" };

  function occurrenceIdFromPath() {
    const last = window.location.pathname.split("/").filter(Boolean).pop() || "";
    try { return decodeURIComponent(last); } catch (e) { return last; }
  }

  function section(number, title, content) {
    return h("section", { "aria-labelledby": "s" + number },
      h("h2", { id: "s" + number }, h("span", { class: "num" }, number + "."), h("span", null, title)),
      content);
  }

  function duration(step) {
    const a = parseDate(step.started_at);
    const b = parseDate(step.completed_at);
    if (!a || !b) return "";
    const s = Math.max(0, Math.round((b - a) / 1000));
    return s < 60 ? s + " s" : Math.floor(s / 60) + " m " + String(s % 60).padStart(2, "0") + " s";
  }

  // The engine uses failed_job {id: 0, name: "(no jobs)"} for runs that never started a job.
  function realJob(r) {
    return r.failed_job && r.failed_job.id ? r.failed_job : null;
  }

  function chainTitle(r) {
    const job = realJob(r);
    return [r.run.workflow_name, job && job.name, r.failed_step && r.failed_step.name]
      .filter(Boolean).join(" → ") || r.run.workflow_name || "Workflow run";
  }

  function cover(id, report, statusLine) {
    const top = h("div", { class: "report-cover-top" },
      h("div", null,
        h("p", { class: "report-kind sc" }, "Investigation report"),
        h("p", { class: "report-no" }, copyText(id, "Occurrence number"))),
      report ? stamp(report.category, true) : null);
    const children = [top];
    if (report) children.push(h("h1", null, chainTitle(report)));
    if (statusLine) children.push(statusLine);
    return h("header", { class: "report-cover" }, children);
  }

  function particulars(r) {
    const rows = [];
    function add(label, value) {
      if (value === null || value === undefined || value === "") return;
      rows.push(h("tr", null, h("th", { scope: "row" }, label), h("td", null, value)));
    }
    add("Repository", h("span", { class: "mono" }, r.repo));
    add("Workflow run", link(r.run.html_url, r.run.workflow_name + (r.run.run_number ? " #" + r.run.run_number : "") +
      (r.run.run_attempt > 1 ? ", attempt " + r.run.run_attempt : "")));
    const job = realJob(r);
    add("Failed job", job ? link(job.html_url, job.name) : h("span", { class: "muted" }, "none: no job started"));
    add("Event", r.run.event ? h("span", { class: "mono" }, r.run.event) : null);
    add("Branch", r.run.head_branch ? h("span", { class: "mono" }, r.run.head_branch) : null);
    add("Commit", r.run.head_sha ? copyText(r.run.head_sha, "Commit SHA", r.run.head_sha.slice(0, 12)) : null);
    add("Pull request", r.pull_request ? link(r.pull_request.html_url, "#" + r.pull_request.number) : null);
    add("Recorded", h("span", { class: "mono" }, fmtDateTime(r.created_at) + " UTC"));
    add("Confidence", h("span", { class: "grade" }, h("b", null, r.confidence_grade || "—"),
      h("span", { class: "val" }, fmtNumber(r.confidence, 2))));
    if (r.other_failed_jobs && r.other_failed_jobs.length) {
      add("Also failed", r.other_failed_jobs.map((j, i) => [i ? ", " : "", link(j.html_url, j.name)]));
    }
    return h("table", { class: "particulars" }, h("tbody", null, rows));
  }

  function timeline(r) {
    if (!r.timeline || !r.timeline.length) {
      return h("p", { class: "muted" }, "No step timeline: the run produced no job logs.");
    }
    const failedNumber = r.failed_step ? r.failed_step.number : null;
    return h("ol", { class: "timeline" }, r.timeline.map((step) => {
      const failed = step.conclusion === "failure" || step.conclusion === "timed_out" ||
        (failedNumber !== null && step.number === failedNumber);
      const cls = failed ? "failed" : step.conclusion === "skipped" ? "skipped" : null;
      return h("li", { class: cls },
        h("span", { class: "t" }, fmtTime(step.started_at)),
        h("span", { class: "n" }, step.number !== null && step.number !== undefined ? String(step.number) : ""),
        h("span", { class: "name" }, step.name,
          failed ? h("span", { class: "verdict sc" }, step.conclusion === "timed_out" ? "timed out" : "failed") : null,
          step.conclusion === "skipped" ? h("span", { class: "sr-only" }, " (skipped)") : null),
        h("span", { class: "dur" }, step.conclusion === "skipped" ? "skipped" : duration(step)));
    }));
  }

  function exhibit(ev, excerpt) {
    const lines = [];
    for (let n = ev.line - 4; n <= ev.line + 4; n++) {
      if (n === ev.line) continue;
      if (Object.prototype.hasOwnProperty.call(excerpt, String(n))) lines.push(n);
    }
    lines.push(ev.line);
    lines.sort((a, b) => a - b);
    const pre = h("pre", { class: "log", tabindex: "0", "aria-label": "Log excerpt around line " + ev.line },
      lines.map((n) => {
        const cited = n === ev.line;
        const text = cited ? ev.text : excerpt[String(n)];
        return h("span", { class: "line " + (cited ? "cited" : "context") },
          h("span", { class: "ln" }, String(n)), text);
      }));
    return h("figure", { class: "exhibit" },
      h("div", { class: "exhibit-head" },
        h("span", { class: "exhibit-label" }, "EXHIBIT " + ev.exhibit),
        h("span", { class: "exhibit-ref" }, "log line " + ev.line)),
      pre,
      ev.why ? h("figcaption", null, ev.why) : null);
  }

  function evidence(r, excerpt) {
    if (!r.evidence || !r.evidence.length) {
      return h("p", { class: "muted" }, "No log line could be cited as evidence for this occurrence.");
    }
    return r.evidence.map((ev) => exhibit(ev, excerpt || {}));
  }

  function actions(r) {
    if (!r.recommended_actions || !r.recommended_actions.length) {
      return h("p", { class: "muted" }, "No actions recommended.");
    }
    return h("ol", { class: "actions" }, r.recommended_actions.map((a) =>
      h("li", null,
        h("span", { class: "action-id" }, a.id),
        h("div", null,
          h("p", null, a.action),
          a.command ? h("div", { class: "command" },
            h("pre", null, a.command),
            window.PD.copyIcon(a.command, "Command")) : null))));
  }

  function imprint(r) {
    const llm = r.llm || {};
    const source = SOURCE_LABELS[r.source] || r.source;
    const usedLLM = r.source === "llm";
    // Non-LLM reports carry zeros for latency and tokens; show them as "not applicable".
    const measured = (value, format) => (usedLLM || value ? format(value) : "—");
    const items = [
      ["Source", source],
      ["Model", llm.model || (usedLLM ? "—" : "none")],
      ["Prompt", llm.prompt_id ? llm.prompt_id + "." + (llm.prompt_version || "?") +
        (llm.prompt_sha256 ? " · " + llm.prompt_sha256.slice(0, 12) : "") : "—"],
      ["Latency", measured(llm.latency_ms, fmtSeconds)],
      ["Cost", measured(llm.cost_usd, fmtCost)],
      ["Tokens in / out", measured(llm.input_tokens, String) + " / " + measured(llm.output_tokens, String)],
      ["Engine", r.engine_version || "—"],
      ["Log window", r.log_window ? (r.log_window.excerpt_lines || 0) + " of " + (r.log_window.total_lines || 0) +
        " lines · " + (r.log_window.redactions || 0) + " redacted" : "—"],
    ];
    if (r.pre_classification) {
      const p = r.pre_classification;
      items.push(["Rule pre-classifier", p.category + (p.rule_id ? " · " + p.rule_id : "") +
        (typeof p.confidence === "number" ? " · " + p.confidence.toFixed(2) : "")]);
    }
    const children = [
      h("p", { class: "imprint-title sc" }, "Publication data"),
      h("dl", null, items.map(([k, v]) => h("div", null, h("dt", null, k), h("dd", null, v)))),
    ];
    if (llm.attempts && llm.attempts.length) {
      children.push(h("p", { class: "attempts" }, "Attempts: " + llm.attempts.map((a) =>
        a.model.replace(/^openrouter\//, "") + " " + a.outcome + (a.latency_ms != null ? " (" + fmtSeconds(a.latency_ms) + ")" : "")).join("; ")));
    }
    if (r.warnings && r.warnings.length) {
      children.push(h("p", { class: "imprint-title sc" }, "Investigator's notes"));
      children.push(h("ul", { class: "notes-list" }, r.warnings.map((w) => h("li", null, w))));
    }
    return h("footer", { class: "imprint" }, children);
  }

  function renderReport(root, id, data) {
    const r = data.report;
    root.appendChild(cover(id, r, data.notice ? h("p", { class: "notice" }, data.notice) : null));
    root.appendChild(particulars(r));
    root.appendChild(section(1, "Synopsis", h("p", null, r.summary || "—")));
    root.appendChild(section(2, "Sequence of events", timeline(r)));
    root.appendChild(section(3, "Evidence", evidence(r, data.excerpt)));
    root.appendChild(section(4, "Probable cause",
      h("div", { class: "cause" }, h("span", { class: "cause-label sc" }, "Probable cause"), h("p", null, r.probable_cause || "—"))));
    root.appendChild(section(5, "Recommended actions", actions(r)));
    root.appendChild(section(6, "Verification", r.verification && r.verification.length
      ? h("ul", { class: "verification" }, r.verification.map((v) => h("li", null, v)))
      : h("p", { class: "muted" }, "No verification steps given.")));
    root.appendChild(imprint(r));
    document.title = id + " — PipelineDoctor Console";
  }

  function renderStatus(root, id, data) {
    const pending = data.status === "pending";
    const line = h("div", null,
      h("p", null, pending
        ? "Investigation in progress. This page refreshes when the report is filed."
        : "No report was filed for this occurrence."),
      pending ? h("span", { class: "progress-rule", role: "presentation" }) : null,
      data.notice ? h("p", { class: "notice" }, data.notice) : null,
      data.doctor_run_url ? h("p", null, link(data.doctor_run_url, "Open the Doctor run on GitHub")) : null);
    root.appendChild(cover(id, null, line));
    document.title = id + " — PipelineDoctor Console";
  }

  async function load() {
    const root = document.getElementById("report");
    const id = occurrenceIdFromPath();
    const res = await fetchJSON("/api/occurrences/" + encodeURIComponent(id));
    clear(root);
    root.setAttribute("aria-busy", "false");
    if (res.status === 404) {
      root.appendChild(cover(id, null, h("p", null, "This number is not on the register. Reports older than 90 days expire with their workflow artifacts.")));
      return;
    }
    if (!res.ok || !res.data) {
      const detail = res.data && res.data.detail ? res.data.detail : "The report could not be retrieved.";
      root.appendChild(cover(id, null, h("p", { class: "notice" }, detail)));
      return;
    }
    if (res.data.report) renderReport(root, id, res.data);
    else renderStatus(root, id, res.data);
    if (res.data.status === "pending") window.setTimeout(load, POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", load);
})();
