"""Run the eval dataset through the real diagnosis pipeline and score it."""

from __future__ import annotations

import logging
import os
import secrets
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from pipelinedoctor import __version__
from pipelinedoctor.config import DEFAULT_EVAL_REQUEST_BUDGET, Settings
from pipelinedoctor.engine.diagnose import (
    Analysis,
    JobContext,
    analyze_log,
    build_report,
    make_llm_client,
    utc_now,
)
from pipelinedoctor.engine.fingerprint import engine_fingerprint
from pipelinedoctor.engine.llm import LLMClient, RequestBudget
from pipelinedoctor.engine.prompting import Prompt, load_prompt
from pipelinedoctor.evals.cache import ResultCache, case_key
from pipelinedoctor.evals.dataset import LoadedCase, load_cases
from pipelinedoctor.evals.judge import judge_case
from pipelinedoctor.evals.scoring import aggregate, score_case
from pipelinedoctor.schemas import (
    DiagnosisReport,
    EvalCaseResult,
    EvalResult,
    EvalRunInfo,
    JobRef,
    PlantSecret,
    RunInfo,
    StepRef,
    TimelineStep,
)

log = logging.getLogger(__name__)

LLMMode = Literal["live", "cache-only", "rules"]


@dataclass
class EvalOptions:
    subset: Literal["pr", "all"] = "pr"
    cases_dir: Path = Path("evals/cases")
    cache_dir: Path = Path(".eval-cache")
    cache_read: bool = True
    llm_mode: LLMMode = "live"
    judge: bool = False
    judge_sample: int = 8
    max_requests: int | None = None


def make_fake_secret(spec: PlantSecret) -> str:
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(max(0, spec.length - len(spec.prefix))))
    return spec.prefix + body


def case_context(loaded: LoadedCase) -> JobContext:
    case = loaded.case
    timeline = [
        TimelineStep(number=s.number, name=s.name, conclusion=s.conclusion) for s in case.steps
    ]
    failed = next((s for s in case.steps if s.conclusion == "failure"), None)
    return JobContext(
        workflow=case.workflow,
        job=case.job,
        failed_step=StepRef(number=failed.number, name=failed.name) if failed else None,
        timeline=timeline,
    )


def case_report(loaded: LoadedCase, analysis: Analysis, ctx: JobContext) -> DiagnosisReport:
    return build_report(
        analysis,
        occurrence=f"PD-EVAL-{loaded.id}",
        repo="evals/dataset",
        run=RunInfo(workflow_name=loaded.case.workflow),
        failed_job=JobRef(id=0, name=loaded.case.job),
        ctx=ctx,
    )


def preflight_openrouter(api_key: str) -> None:
    """Best effort: log the key's free-tier status and limits. Never fails the run."""
    try:
        response = httpx.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        data = response.json().get("data", {})
        log.info(
            "OpenRouter key: is_free_tier=%s limit=%s usage=%s",
            data.get("is_free_tier"),
            data.get("limit"),
            data.get("usage"),
        )
    except Exception as exc:  # informational only
        log.info("OpenRouter preflight skipped: %s", type(exc).__name__)


def run_evals(
    options: EvalOptions,
    settings: Settings,
    llm_client: LLMClient | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> EvalResult:
    started_at = utc_now()
    prompt = load_prompt("diagnose", settings.prompt_version)
    judge_prompt = load_prompt("judge", "v1") if options.judge else None
    cases = load_cases(options.cases_dir, options.subset)
    fingerprint = engine_fingerprint()
    models = list(settings.models[: settings.llm_max_calls])
    cache = ResultCache(options.cache_dir, read=options.cache_read)

    client: LLMClient | None = None
    if options.llm_mode == "live":
        client = llm_client or make_llm_client(settings)
        if llm_client is None and settings.openrouter_api_key:
            preflight_openrouter(settings.openrouter_api_key)
    max_requests = options.max_requests or settings.request_budget(
        DEFAULT_EVAL_REQUEST_BUDGET[options.subset]
    )
    budget = RequestBudget(max_requests, settings.min_request_interval_s, sleep=sleep)

    results: list[EvalCaseResult] = []
    reports: dict[str, tuple[str, DiagnosisReport]] = {}
    for loaded in cases:
        key = case_key(
            case_yaml=loaded.case_yaml,
            log_sha256=loaded.log_sha256,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            models=models,
            engine_fingerprint=fingerprint,
        )
        try:
            result, report = _run_case(
                loaded, key, options.llm_mode, settings, prompt, client, budget, cache
            )
        except Exception as exc:  # one broken case must not hide the others' scores
            log.exception("case %s failed", loaded.id)
            result = EvalCaseResult(
                case_id=loaded.id,
                status="error",
                expected_category=loaded.case.expected.category,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            report = None
        results.append(result)
        if report is not None:
            reports[loaded.id] = (key, report)
        log.info("%-40s %s %s", loaded.id, result.status, result.predicted_category or "")

    if judge_prompt is not None:
        _judge(results, reports, cases, judge_prompt, client, budget, cache, settings, options)

    statuses = {r.status for r in results}
    skipped_reason: Literal["no_api_key", "budget"] | None = None
    if "skipped_no_key" in statuses:
        skipped_reason = "no_api_key"
    elif "skipped_budget" in statuses:
        skipped_reason = "budget"

    return EvalResult(
        run=EvalRunInfo(
            started_at=started_at,
            finished_at=utc_now(),
            mode="pr" if options.subset == "pr" else "nightly",
            subset=options.subset,
            git_sha=os.environ.get("GITHUB_SHA") or None,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            models=models,
            engine_fingerprint=fingerprint,
            engine_version=__version__,
            llm_mode=options.llm_mode,
            judge=options.judge,
        ),
        cases=results,
        aggregate=aggregate(results),
        skipped_reason=skipped_reason,
    )


def _run_case(
    loaded: LoadedCase,
    key: str,
    llm_mode: LLMMode,
    settings: Settings,
    prompt: Prompt,
    client: LLMClient | None,
    budget: RequestBudget,
    cache: ResultCache,
) -> tuple[EvalCaseResult, DiagnosisReport | None]:
    case = loaded.case
    ctx = case_context(loaded)

    if llm_mode != "rules":
        cached = cache.get_diagnosis(key)
        if cached is not None:
            report = DiagnosisReport.model_validate(cached["report"])
            result = score_case(
                case,
                report,
                evidence_cited=int(cached["evidence_cited"]),
                evidence_valid=int(cached["evidence_valid"]),
            )
            result.no_secret_leak = cached.get("no_secret_leak")
            result.cache_hit = True
            result.latency_ms = int(cached.get("latency_ms", 0))
            result.cost_usd = float(cached.get("cost_usd", 0.0))
            return result, report
        skip_status = None
        if client is None:
            skip_status = "skipped_no_key"
        elif budget.remaining < 1:
            skip_status = "skipped_budget"
        if skip_status is not None:
            return (
                EvalCaseResult(
                    case_id=case.id,
                    status=skip_status,  # type: ignore[arg-type]
                    expected_category=case.expected.category,
                ),
                None,
            )

    planted = make_fake_secret(case.plant_secret) if case.plant_secret else None
    log_text = loaded.log_text
    if planted is not None and case.plant_secret is not None:
        # The fake secret is NOT given to redact() as a known value: the regexes must catch it.
        log_text = log_text.replace(case.plant_secret.placeholder, planted)

    started = time.monotonic()
    analysis = analyze_log(
        log_text,
        ctx,
        settings,
        llm_client=client,
        budget=budget,
        prompt=prompt,
        use_llm=llm_mode != "rules",
    )
    wall_ms = int((time.monotonic() - started) * 1000)
    report = case_report(loaded, analysis, ctx)
    report_json = report.model_dump_json()
    result = score_case(
        case,
        report,
        evidence_cited=analysis.evidence_cited
        if analysis.source == "llm"
        else len(report.evidence),
        evidence_valid=analysis.evidence_valid
        if analysis.source == "llm"
        else len(report.evidence),
        planted_secret=planted,
        report_json=report_json,
    )
    result.llm_calls = analysis.llm_calls
    result.latency_ms = analysis.llm.latency_ms or wall_ms
    result.cost_usd = analysis.llm.cost_usd
    # Only real LLM answers are cached: a fallback caused by a rate limit must be retried later.
    if analysis.source == "llm":
        cache.put_diagnosis(
            key,
            {
                "report": report.model_dump(mode="json"),
                "evidence_cited": result.evidence_cited,
                "evidence_valid": result.evidence_valid,
                "no_secret_leak": result.no_secret_leak,
                "latency_ms": result.latency_ms,
                "cost_usd": result.cost_usd,
            },
        )
    return result, report


def _judge(
    results: list[EvalCaseResult],
    reports: dict[str, tuple[str, DiagnosisReport]],
    cases: list[LoadedCase],
    judge_prompt: Prompt,
    client: LLMClient | None,
    budget: RequestBudget,
    cache: ResultCache,
    settings: Settings,
    options: EvalOptions,
) -> None:
    by_id = {c.id: c for c in cases}
    eligible = sorted(
        (r for r in results if r.status == "scored" and r.source == "llm"), key=lambda r: r.case_id
    )[: options.judge_sample]
    for result in eligible:
        key, report = reports[result.case_id]
        cached = cache.get_judge(key, judge_prompt.sha256)
        if cached is not None:
            result.judge_score = float(cached["score"])
            continue
        if client is None or budget.remaining < 1:
            continue
        outcome = judge_case(
            client,
            budget,
            list(settings.models[:1]),
            judge_prompt,
            by_id[result.case_id].case,
            report,
            settings.llm_timeout_s,
        )
        result.llm_calls += outcome.calls
        if outcome.verdict is not None:
            result.judge_score = outcome.verdict.score
            cache.put_judge(key, judge_prompt.sha256, outcome.verdict.model_dump())
