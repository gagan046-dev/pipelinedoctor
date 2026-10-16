"""Markdown for the PR comment and the job summary.

Everything shown here came from an untrusted log or an LLM, so it is made inert: HTML is
stripped, @mentions cannot ping people, Markdown links and images are defanged (a prompt-injected
log could otherwise make the bot show a phishing link or a tracking pixel), and log lines sit in
code fences that the content cannot close.
"""

from __future__ import annotations

import re

from pipelinedoctor.github.comments import wrap_section
from pipelinedoctor.schemas import Category, DiagnosisReport

_TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
_MENTION_RE = re.compile(r"@(?=[A-Za-z0-9_-])")
_BACKTICKS_RE = re.compile(r"`+")
_LINK_RE = re.compile(r"\]\s*\(")
_AUTOLINK_RE = re.compile(r"\b(https?)://", re.IGNORECASE)

STAMPS = {category: category.value.upper() for category in Category}
STAMPS[Category.SECRET_PERMISSION] = "SECRET / PERMISSION"
SOURCE_LABELS = {"llm": "llm", "rules_fallback": "rule-based fallback", "rules_only": "rule-based"}


def safe_md(text: str | None, limit: int = 1200) -> str:
    """Inline-safe text: no HTML, no pings, bounded length."""
    if not text:
        return ""
    text = _TAG_RE.sub("", text)
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = _MENTION_RE.sub("@\u200b", text)
    # `[x](u)` and `![x](u)` stop being links once `](` is split; bare URLs stop auto-linking.
    text = text.replace("![", "!\u200b[")
    text = _LINK_RE.sub("]\u200b(", text)
    text = _AUTOLINK_RE.sub(lambda m: f"{m.group(1)}\u200b://", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fence(content: str, info: str = "") -> str:
    """A code fence one backtick longer than the longest backtick run inside `content`."""
    # An HTML comment inside a log line could otherwise fake our own section markers.
    content = content.replace("<!--", "<!-\u200b-")
    longest = max((len(m) for m in _BACKTICKS_RE.findall(content)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{content}\n{ticks}"


def _footer(report: DiagnosisReport) -> str:
    llm = report.llm
    model = llm.model or "none"
    seconds = llm.latency_ms / 1000
    if report.run.run_number:
        run = f"run #{report.run.run_number}"
    else:
        run = f"run {report.run.id}" if report.run.id else "local log"
    return (
        f"<sub>model {safe_md(model, 120)} · prompt {llm.prompt_id}.{llm.prompt_version} "
        f"({llm.prompt_sha256[:12]}) · {seconds:.1f} s · ${llm.cost_usd:.4f} · {run} · "
        f"engine {report.engine_version}</sub>"
    )


def render_body(report: DiagnosisReport) -> str:
    """The section content shared by the PR comment and the job summary."""
    stamp = STAMPS[report.category]
    job = safe_md(report.failed_job.name, 200)
    step = safe_md(report.failed_step.name, 200) if report.failed_step else "(unknown step)"
    workflow = safe_md(report.run.workflow_name, 200)
    source = SOURCE_LABELS[report.source]
    out = [
        f"### {stamp} · {workflow} → {job} → {step}",
        f"**Occurrence** `{report.occurrence_id}` · **Confidence** {report.confidence_grade} "
        f"({report.confidence:.2f}) · **Source** {source}",
        "",
        f"**1. Synopsis** {safe_md(report.summary, 600)}",
        "",
        f"**2. Probable cause** {safe_md(report.probable_cause, 1200)}",
        "",
        "**3. Evidence**",
    ]
    if report.evidence:
        for item in report.evidence:
            why = f" — {safe_md(item.why, 300)}" if item.why else ""
            out += ["", f"EXHIBIT {item.exhibit} — line {item.line}{why}", fence(item.text, "text")]
    else:
        out.append("_No log line could be verified as evidence._")
    out += ["", "**4. Recommended actions**"]
    for action in report.recommended_actions:
        out.append(f"- **{action.id}** {safe_md(action.action, 400)}")
        if action.command:
            out.append(fence(action.command[:400], "bash"))
    out += ["", "**5. Verification**"]
    out += [f"- {safe_md(v, 400)}" for v in report.verification] or ["- Re-run the workflow."]
    if report.source != "llm":
        out += ["", "> This diagnosis was produced by deterministic rules, not by an LLM."]
    notes = [w for w in report.warnings if not w.startswith("dropped evidence")]
    if notes:
        out += ["", "<details><summary>Engine notes</summary>", ""]
        out += [f"- {safe_md(w, 300)}" for w in notes[:8]]
        out += ["", "</details>"]
    if report.run.html_url:
        out += ["", f"[Open the failed run]({report.run.html_url})"]
    out += ["", _footer(report)]
    return "\n".join(out)


def render_pr_section(report: DiagnosisReport, workflow_key: str) -> str:
    return wrap_section(workflow_key, render_body(report))


def render_summary(report: DiagnosisReport) -> str:
    return "## PipelineDoctor diagnosis\n\n" + render_body(report) + "\n"
