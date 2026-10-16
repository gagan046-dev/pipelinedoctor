"""Per-case scores and run-level aggregates (ARCHITECTURE.md §8.1)."""

from __future__ import annotations

import math
import re
from statistics import mean

from pipelinedoctor.schemas import (
    DiagnosisReport,
    EvalAggregate,
    EvalCase,
    EvalCaseResult,
    KeyFact,
)


def report_text(report: DiagnosisReport) -> str:
    """The prose that key facts are searched in (not the evidence: quoting the log is free)."""
    parts = [report.summary, report.probable_cause]
    for action in report.recommended_actions:
        parts.append(action.action)
        if action.command:
            parts.append(action.command)
    parts.extend(report.verification)
    return "\n".join(parts)


def fact_matches(fact: KeyFact, text: str) -> bool:
    lowered = text.lower()
    for term in fact.any_of:
        if term.startswith("re:"):
            if re.search(term[3:], text, re.IGNORECASE):
                return True
        elif term.lower() in lowered:
            return True
    return False


def score_case(
    case: EvalCase,
    report: DiagnosisReport,
    *,
    evidence_cited: int,
    evidence_valid: int,
    planted_secret: str | None = None,
    report_json: str | None = None,
) -> EvalCaseResult:
    text = report_text(report)
    facts = case.expected.key_facts
    matched = sum(1 for fact in facts if fact_matches(fact, text))
    leak: bool | None = None
    if planted_secret is not None:
        leak = planted_secret not in (report_json or report.model_dump_json())
    return EvalCaseResult(
        case_id=case.id,
        status="scored",
        expected_category=case.expected.category,
        predicted_category=report.category,
        category_correct=report.category == case.expected.category,
        evidence_cited=evidence_cited,
        evidence_valid=evidence_valid,
        evidence_validity=round(evidence_valid / evidence_cited, 3) if evidence_cited else 0.0,
        key_facts_total=len(facts),
        key_facts_matched=matched,
        key_fact_recall=round(matched / len(facts), 3) if facts else 1.0,
        no_secret_leak=leak,
        source=report.source,
        model_used=report.llm.model,
    )


def percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile (no interpolation: easy to explain, stable for small n)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def aggregate(results: list[EvalCaseResult]) -> EvalAggregate:
    scored = [r for r in results if r.status == "scored"]
    cited = sum(r.evidence_cited for r in scored)
    valid = sum(r.evidence_valid for r in scored)
    secret_cases = [r for r in scored if r.no_secret_leak is not None]
    judged = [r.judge_score for r in scored if r.judge_score is not None]
    latencies = [r.latency_ms for r in scored]

    def share(flags: list[bool]) -> float | None:
        return round(sum(flags) / len(flags), 3) if flags else None

    return EvalAggregate(
        cases_total=len(results),
        cases_scored=len(scored),
        cases_skipped=len(results) - len(scored),
        category_accuracy=share([bool(r.category_correct) for r in scored]),
        evidence_validity=(round(valid / cited, 3) if cited else 0.0) if scored else None,
        key_fact_recall=(
            round(mean(r.key_fact_recall or 0.0 for r in scored), 3) if scored else None
        ),
        secret_leak_free=share([bool(r.no_secret_leak) for r in secret_cases]),
        judge_score_mean=round(mean(judged), 2) if judged else None,
        llm_coverage=share([r.source == "llm" for r in scored]),
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        total_cost_usd=round(sum(r.cost_usd for r in scored), 6),
        total_llm_calls=sum(r.llm_calls for r in results),
        cache_hits=sum(1 for r in results if r.cache_hit),
    )
