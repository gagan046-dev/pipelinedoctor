"""Diagnosis pipeline.

`analyze_log()` is pure apart from the injected LLM client: raw log in, `Analysis` out. It is
shared by the doctor workflow, `diagnose-log` and the eval harness, so evals measure exactly
the code that runs in production. `diagnose_run()` adds the GitHub I/O around it.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pipelinedoctor import __version__
from pipelinedoctor.config import DEFAULT_DIAGNOSE_REQUEST_BUDGET, Settings
from pipelinedoctor.engine.llm import FallbackResult, LLMClient, RequestBudget, call_with_fallback
from pipelinedoctor.engine.parse import clean_text, to_llm_diagnosis, validate_evidence
from pipelinedoctor.engine.preprocess import ANCHOR_RE, Excerpt, build_excerpt, clean_log
from pipelinedoctor.engine.prompting import Prompt, load_prompt
from pipelinedoctor.engine.redact import redact_lines
from pipelinedoctor.engine.rules import RuleMatch, best_match
from pipelinedoctor.schemas import (
    Category,
    DiagnosisReport,
    EvidenceItem,
    JobRef,
    LLMDiagnosis,
    LLMInfo,
    LogWindow,
    PreClassification,
    PullRequestRef,
    RecommendedAction,
    RunInfo,
    Source,
    StepRef,
    TimelineStep,
)

log = logging.getLogger(__name__)

MAX_ACTIONS = 5
MAX_VERIFICATION = 5
CATEGORY_GUIDE = (
    "dependency (install/resolution/missing package/lock file), test (assertion or test error), "
    "lint (linters, formatters, mypy/type checkers), build (compile errors, merge-conflict "
    "markers, native wheel builds), docker (Dockerfile/image build), config (workflow YAML, "
    "runtime version mismatch), secret_permission (HTTP 401/403, missing secret, registry "
    "denied), flaky (timeouts/transient network in tests), infrastructure (OOM exit 137, disk "
    "full, HTTP 429 rate limit, service connection refused), unknown"
)
LLM_SCHEMA_EXAMPLE = {
    "category": "test",
    "failed_step": "Run tests",
    "summary": "One sentence: what failed.",
    "probable_cause": "Why it failed, grounded in the cited lines.",
    "evidence": [{"line": 812, "quote": "exact text copied from that line", "why": "..."}],
    "recommended_actions": [{"action": "What to change.", "command": "optional shell command"}],
    "verification": ["How to confirm the fix."],
    "confidence": 0.8,
}
_INJECTION_RE = re.compile(
    r"(?i)ignore (?:all |any |the )?(?:previous|prior|above|earlier) instructions"
    r"|disregard (?:the |your )?(?:system|previous) (?:prompt|instructions)"
    r"|(?:note|message|instruction)s? (?:to|for) (?:the )?(?:ai|llm|assistant|model)"
)


CommentMode = Literal["auto", "always", "never"]


@dataclass
class JobContext:
    workflow: str
    job: str
    failed_step: StepRef | None = None
    timeline: list[TimelineStep] = field(default_factory=list)


@dataclass
class Analysis:
    excerpt: Excerpt
    category: Category
    summary: str
    probable_cause: str
    evidence: list[EvidenceItem]
    recommended_actions: list[RecommendedAction]
    verification: list[str]
    confidence: float
    source: Source
    pre_classification: PreClassification | None
    llm: LLMInfo
    warnings: list[str] = field(default_factory=list)
    evidence_cited: int = 0
    evidence_valid: int = 0
    llm_calls: int = 0

    @property
    def confidence_grade(self) -> Literal["A", "B", "C"]:
        return grade(self.confidence)


def grade(confidence: float) -> Literal["A", "B", "C"]:
    if confidence >= 0.75:
        return "A"
    if confidence >= 0.5:
        return "B"
    return "C"


def calibrate(
    model_confidence: float, category: Category, pre: PreClassification | None, valid: int
) -> float:
    c = min(model_confidence, 0.95)
    if pre is not None and pre.confidence >= 0.7 and pre.category != category:
        c *= 0.85
    if valid == 0:
        c = min(c, 0.3)
    return round(c, 3)


def prepare_excerpt(raw_log: str, settings: Settings, extra_secrets: list[str]) -> Excerpt:
    lines, redactions = redact_lines(clean_log(raw_log), extra_secrets)
    excerpt = build_excerpt(lines, settings.log_window_lines, settings.max_excerpt_lines)
    excerpt.redactions = redactions
    return excerpt


# --------------------------------------------------------------------------- rule-based result


def _rule_evidence(match: RuleMatch | None, excerpt: Excerpt) -> list[EvidenceItem]:
    if match is not None:
        numbers = match.lines[-3:]
        why = f"Matches rule {match.rule.id}."
    else:
        numbers = [n for n in excerpt.numbers if ANCHOR_RE.search(excerpt.lines[n])][-3:]
        why = "Last error-looking line in the log."
    return [
        EvidenceItem(exhibit=chr(ord("A") + i), line=n, text=excerpt.lines[n], why=why)
        for i, n in enumerate(numbers)
    ]


def rules_analysis(
    excerpt: Excerpt,
    source: Source,
    llm: LLMInfo,
    warnings: list[str],
    llm_calls: int = 0,
) -> Analysis:
    match = best_match(excerpt)
    evidence = _rule_evidence(match, excerpt)
    if match is None:
        return Analysis(
            excerpt=excerpt,
            category=Category.UNKNOWN,
            summary="No known failure pattern matched; read the cited error lines.",
            probable_cause="The rule-based classifier did not recognise this failure.",
            evidence=evidence,
            recommended_actions=[
                RecommendedAction(
                    id="R1", action="Open the failed step log and read the last error."
                )
            ],
            verification=["Re-run the job after fixing the error."],
            confidence=0.1,
            source=source,
            pre_classification=None,
            llm=llm,
            warnings=warnings,
            llm_calls=llm_calls,
        )
    rule = match.rule
    key_line = clean_text(excerpt.lines[match.last_line].strip(), 240)
    return Analysis(
        excerpt=excerpt,
        category=rule.category,
        summary=f"{rule.title}. Key line {match.last_line}: {key_line}",
        probable_cause=rule.cause,
        evidence=evidence,
        recommended_actions=[RecommendedAction(id="R1", action=rule.fix, command=rule.command)],
        verification=[rule.verification],
        confidence=round(min(rule.confidence * 0.7, 0.6), 3),
        source=source,
        pre_classification=PreClassification(
            category=rule.category, rule_id=rule.id, confidence=rule.confidence
        ),
        llm=llm,
        warnings=warnings,
        llm_calls=llm_calls,
    )


# --------------------------------------------------------------------------- LLM result


def _fence_nonce(excerpt_text: str) -> tuple[str, str]:
    nonce = secrets.token_hex(4)
    # The log must not be able to "close" the fence, so any copy of the nonce is neutralised.
    return nonce, excerpt_text.replace(nonce, "[nonce]")


def _meta(value: str, nonce: str) -> str:
    """Workflow/job/step names come from a workflow file that a pull request can edit: one line,
    short, no copy of the fence nonce and no instruction-like phrases."""
    value = " ".join(value.split())[:100].replace(nonce, "[nonce]")
    return _INJECTION_RE.sub("[instruction removed]", value)


def _timeline_text(ctx: JobContext, nonce: str) -> str:
    if not ctx.timeline:
        return "(not available)"
    return "\n".join(
        f"{s.number}. {_meta(s.name, nonce)} -> {s.conclusion or 'unknown'}" for s in ctx.timeline
    )


def render_diagnose_prompt(
    prompt: Prompt, excerpt: Excerpt, ctx: JobContext, pre: PreClassification | None
) -> tuple[str, str]:
    nonce, excerpt_text = _fence_nonce(excerpt.render())
    preclass = (
        f"{pre.category.value} (rule {pre.rule_id}, confidence {pre.confidence:.2f})"
        if pre
        else "none"
    )
    return prompt.render(
        workflow=_meta(ctx.workflow, nonce),
        job=_meta(ctx.job, nonce),
        failed_step=_meta(ctx.failed_step.name, nonce) if ctx.failed_step else "(unknown)",
        timeline=_timeline_text(ctx, nonce),
        preclass=preclass,
        categories=CATEGORY_GUIDE,
        nonce=nonce,
        excerpt=excerpt_text,
        schema=json.dumps(LLM_SCHEMA_EXAMPLE, indent=2),
    )


def _llm_analysis(
    excerpt: Excerpt,
    diagnosis: LLMDiagnosis,
    result: FallbackResult[LLMDiagnosis],
    pre: PreClassification | None,
    llm: LLMInfo,
    warnings: list[str],
) -> Analysis:
    check = validate_evidence(diagnosis.evidence, excerpt)
    warnings = [*warnings, *check.warnings]
    actions = [
        RecommendedAction(
            id=f"R{i}",
            action=clean_text(a.action, 400),
            command=clean_text(a.command, 400) if a.command else None,
        )
        for i, a in enumerate(
            [a for a in diagnosis.recommended_actions if a.action.strip()][:MAX_ACTIONS], start=1
        )
    ]
    return Analysis(
        excerpt=excerpt,
        category=diagnosis.category,
        summary=clean_text(diagnosis.summary, 600),
        probable_cause=clean_text(diagnosis.probable_cause, 1200),
        evidence=check.items,
        recommended_actions=actions,
        verification=[clean_text(v, 400) for v in diagnosis.verification if v.strip()][
            :MAX_VERIFICATION
        ],
        confidence=calibrate(diagnosis.confidence, diagnosis.category, pre, check.valid),
        source="llm",
        pre_classification=pre,
        llm=llm,
        warnings=warnings,
        evidence_cited=check.cited,
        evidence_valid=check.valid,
        llm_calls=result.calls,
    )


def analyze_log(
    raw_log: str,
    ctx: JobContext,
    settings: Settings,
    *,
    llm_client: LLMClient | None,
    budget: RequestBudget,
    prompt: Prompt | None = None,
    use_llm: bool = True,
    extra_secrets: list[str] | None = None,
) -> Analysis:
    secrets_to_mask = settings.secret_values() + (extra_secrets or [])
    excerpt = prepare_excerpt(raw_log, settings, secrets_to_mask)
    return analyze_excerpt(
        excerpt, ctx, settings, llm_client=llm_client, budget=budget, prompt=prompt, use_llm=use_llm
    )


def analyze_excerpt(
    excerpt: Excerpt,
    ctx: JobContext,
    settings: Settings,
    *,
    llm_client: LLMClient | None,
    budget: RequestBudget,
    prompt: Prompt | None = None,
    use_llm: bool = True,
) -> Analysis:
    prompt = prompt or load_prompt("diagnose", settings.prompt_version)
    llm = LLMInfo(prompt_id=prompt.id, prompt_version=prompt.version, prompt_sha256=prompt.sha256)
    warnings: list[str] = []
    if any(_INJECTION_RE.search(line) for line in excerpt.lines.values()):
        warnings.append(
            "The log contains text that tries to instruct an AI; it was treated as data "
            "and ignored."
        )
    pre_match = best_match(excerpt)
    pre = (
        PreClassification(
            category=pre_match.rule.category,
            rule_id=pre_match.rule.id,
            confidence=pre_match.rule.confidence,
        )
        if pre_match
        else None
    )

    if not use_llm or settings.disable_llm:
        warnings.append("LLM disabled: rule-based diagnosis only.")
        return rules_analysis(excerpt, "rules_only", llm, warnings)
    if llm_client is None:
        warnings.append("OPENROUTER_API_KEY not set: rule-based diagnosis only.")
        return rules_analysis(excerpt, "rules_only", llm, warnings)

    system, user = render_diagnose_prompt(prompt, excerpt, ctx, pre)
    result = call_with_fallback(
        llm_client,
        list(settings.models[: settings.llm_max_calls]),
        system,
        user,
        budget,
        settings.llm_timeout_s,
        to_llm_diagnosis,
    )
    llm.attempts = result.attempts
    llm.latency_ms = result.latency_ms
    if result.value is None or result.response is None:
        warnings.append("All LLM attempts failed; this is a deterministic rule-based fallback.")
        warnings.extend(result.errors)
        return rules_analysis(excerpt, "rules_fallback", llm, warnings, llm_calls=result.calls)

    if len(result.attempts) > 1:
        warnings.append(f"Answered by fallback model after: {'; '.join(result.errors)}")
    response = result.response
    llm.model = response.model
    llm.input_tokens = response.input_tokens
    llm.output_tokens = response.output_tokens
    llm.cost_usd = response.cost_usd
    return _llm_analysis(excerpt, result.value, result, pre, llm, warnings)


# --------------------------------------------------------------------------- report assembly


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def occurrence_id(settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if settings.in_actions and settings.run_number is not None:
        return f"PD-{now.year}-{settings.run_number:04d}"
    return f"PD-LOCAL-{int(now.timestamp())}"


def build_report(
    analysis: Analysis,
    *,
    occurrence: str,
    repo: str,
    run: RunInfo,
    failed_job: JobRef,
    ctx: JobContext,
    pull_request: PullRequestRef | None = None,
    other_failed_jobs: list[JobRef] | None = None,
    extra_warnings: list[str] | None = None,
) -> DiagnosisReport:
    excerpt = analysis.excerpt
    return DiagnosisReport(
        occurrence_id=occurrence,
        created_at=utc_now(),
        engine_version=__version__,
        repo=repo,
        run=run,
        pull_request=pull_request,
        failed_job=failed_job,
        other_failed_jobs=other_failed_jobs or [],
        failed_step=ctx.failed_step,
        timeline=ctx.timeline,
        category=analysis.category,
        summary=analysis.summary,
        probable_cause=analysis.probable_cause,
        evidence=analysis.evidence,
        recommended_actions=analysis.recommended_actions,
        verification=analysis.verification,
        confidence=analysis.confidence,
        confidence_grade=analysis.confidence_grade,
        source=analysis.source,
        pre_classification=analysis.pre_classification,
        llm=analysis.llm,
        log_window=LogWindow(
            total_lines=excerpt.total_lines,
            excerpt_lines=len(excerpt.lines),
            ranges=excerpt.ranges,
            redactions=excerpt.redactions,
        ),
        warnings=[*analysis.warnings, *(extra_warnings or [])],
    )


def make_budget(
    settings: Settings, default: int = DEFAULT_DIAGNOSE_REQUEST_BUDGET
) -> RequestBudget:
    return RequestBudget(settings.request_budget(default), settings.min_request_interval_s)


def make_llm_client(settings: Settings) -> LLMClient | None:
    if not settings.openrouter_api_key:
        return None
    from pipelinedoctor.engine.llm import LiteLLMClient

    return LiteLLMClient(settings.openrouter_api_key)


# --------------------------------------------------------------------------- GitHub run I/O


@dataclass
class RunDiagnosis:
    report: DiagnosisReport
    excerpt: Excerpt
    comment_url: str | None = None


FAILED_CONCLUSIONS = ("failure", "timed_out")


def _step_refs(job: dict[str, Any]) -> tuple[StepRef | None, list[TimelineStep]]:
    steps = job.get("steps") or []
    timeline = [
        TimelineStep(
            number=int(s.get("number", 0)),
            name=str(s.get("name", "")),
            conclusion=s.get("conclusion"),
            started_at=s.get("started_at"),
            completed_at=s.get("completed_at"),
        )
        for s in steps
    ]
    failed = next((s for s in timeline if s.conclusion == "failure"), None)
    return (StepRef(number=failed.number, name=failed.name) if failed else None), timeline


def run_info(run: dict[str, Any]) -> RunInfo:
    return RunInfo(
        id=int(run.get("id", 0)),
        run_number=run.get("run_number"),
        run_attempt=run.get("run_attempt"),
        workflow_name=str(run.get("name") or ""),
        workflow_path=run.get("path"),
        event=run.get("event"),
        head_branch=run.get("head_branch"),
        head_sha=run.get("head_sha"),
        html_url=run.get("html_url"),
        conclusion=run.get("conclusion"),
        created_at=run.get("created_at"),
        updated_at=run.get("updated_at"),
    )


def select_failed_job(
    jobs: list[dict[str, Any]], job_id: int | None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    failed = [j for j in jobs if j.get("conclusion") in FAILED_CONCLUSIONS]
    failed.sort(key=lambda j: j.get("completed_at") or "")
    if job_id is not None:
        chosen = next((j for j in jobs if int(j.get("id", 0)) == job_id), None)
    else:
        chosen = failed[0] if failed else None
    others = [j for j in failed if j is not chosen]
    return chosen, others


def _job_ref(job: dict[str, Any]) -> JobRef:
    return JobRef(
        id=int(job.get("id", 0)), name=str(job.get("name", "")), html_url=job.get("html_url")
    )


def startup_failure_analysis(run: RunInfo, prompt: Prompt) -> Analysis:
    llm = LLMInfo(prompt_id=prompt.id, prompt_version=prompt.version, prompt_sha256=prompt.sha256)
    startup = run.conclusion == "startup_failure"
    return Analysis(
        excerpt=Excerpt(lines={}, total_lines=0),
        category=Category.CONFIG if startup else Category.UNKNOWN,
        summary=(
            "The workflow could not start (startup_failure), so no job produced a log."
            if startup
            else "The run failed without a failed job (cancelled or skipped jobs)."
        ),
        probable_cause=(
            "GitHub rejected the workflow file before any job ran: usually invalid YAML, an "
            "unknown key, or an invalid `uses:`/`permissions:` value. The exact parser message "
            "is shown on the run page."
            if startup
            else "No job concluded with failure; the run was probably cancelled."
        ),
        evidence=[],
        recommended_actions=[
            RecommendedAction(
                id="R1",
                action="Open the run page for the parser message and validate the file "
                "with actionlint.",
                command="actionlint",
            )
        ],
        verification=["The workflow starts and its jobs run."],
        confidence=0.6 if startup else 0.1,
        source="rules_only",
        pre_classification=None,
        llm=llm,
        warnings=["no job logs: startup failure" if startup else "no failed job found"],
    )


def diagnose_run(
    settings: Settings,
    gh: Any,
    repo: str,
    run_id: int,
    *,
    job_id: int | None = None,
    comment: CommentMode = "auto",
    use_llm: bool = True,
    llm_client: LLMClient | None = None,
) -> RunDiagnosis:
    """Fetch, analyse, report and (optionally) comment. `gh` is a GitHubClient (duck-typed)."""
    from pipelinedoctor.github.comments import upsert_report_section
    from pipelinedoctor.render.markdown import render_pr_section

    prompt = load_prompt("diagnose", settings.prompt_version)
    run = gh.get_run(repo, run_id)
    info = run_info(run)
    jobs = gh.list_jobs(repo, run_id)
    job, others = select_failed_job(jobs, job_id)
    occurrence = occurrence_id(settings)

    if job is None:
        analysis = startup_failure_analysis(info, prompt)
        ctx = JobContext(workflow=info.workflow_name, job="(no jobs)")
        failed_job = JobRef(id=0, name="(no jobs)", html_url=info.html_url)
    else:
        failed_step, timeline = _step_refs(job)
        ctx = JobContext(
            workflow=info.workflow_name,
            job=str(job.get("name", "")),
            failed_step=failed_step,
            timeline=timeline,
        )
        started = time.monotonic()
        raw_log = gh.download_job_log(repo, int(job["id"]))
        log.info("downloaded log for job %s in %.1fs", job["id"], time.monotonic() - started)
        analysis = analyze_log(
            raw_log,
            ctx,
            settings,
            llm_client=llm_client,
            budget=make_budget(settings),
            prompt=prompt,
            use_llm=use_llm,
        )
        if getattr(gh, "last_log_truncated", False):
            analysis.warnings.append(
                "The job log was over the size limit: its beginning was dropped and line "
                "numbers count from the first line that was kept."
            )
        failed_job = _job_ref(job)

    pr = gh.find_pull_request(repo, run) if comment != "never" else None
    report = build_report(
        analysis,
        occurrence=occurrence,
        repo=repo,
        run=info,
        failed_job=failed_job,
        ctx=ctx,
        pull_request=pr,
        other_failed_jobs=[_job_ref(j) for j in others],
    )
    outcome = RunDiagnosis(report=report, excerpt=analysis.excerpt)
    if pr is not None:
        workflow_key = (info.workflow_path or info.workflow_name or "workflow").rsplit("/", 1)[-1]
        section = render_pr_section(report, workflow_key)
        outcome.comment_url = upsert_report_section(gh, repo, pr.number, workflow_key, section)
    elif comment == "always":
        report.warnings.append("--comment always: no pull request found for this run")
    return outcome
