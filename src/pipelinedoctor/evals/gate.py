"""Compare an eval result with evals/thresholds.yaml and render the score table."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from pipelinedoctor.schemas import EvalResult, LedgerEntry

GateStatus = Literal["pass", "fail", "neutral"]
NO_KEY_MESSAGE = (
    "Evals skipped: OPENROUTER_API_KEY not available — nightly run on main remains the safety net."
)
LOW_COVERAGE_MESSAGE = "LLM provider unavailable or rate-limited — re-run later."
RULES_MODE_MESSAGE = "Offline baseline (--llm rules): llm_coverage is 0 by design; not a CI result."


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_accuracy: float
    evidence_validity: float
    key_fact_recall: float
    secret_leak_free: float = 1.0
    llm_coverage: float
    max_latency_p95_ms: int
    min_scored_ratio: float
    judge_score_mean: float | None = None


def load_thresholds(path: Path, mode: Literal["pr", "nightly"]) -> Thresholds:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if str(data.get("schema_version")) != "1":
        raise ValueError(f"{path}: unsupported schema_version")
    if mode not in data:
        raise ValueError(f"{path}: no thresholds for mode {mode!r}")
    return Thresholds.model_validate(data[mode])


@dataclass
class Check:
    name: str
    value: float | None
    threshold: float
    op: Literal[">=", "<="]

    @property
    def passed(self) -> bool:
        if self.value is None:
            return False
        return self.value >= self.threshold if self.op == ">=" else self.value <= self.threshold


@dataclass
class GateOutcome:
    status: GateStatus
    checks: list[Check] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def evaluate_gate(result: EvalResult, thresholds: Thresholds) -> GateOutcome:
    agg = result.aggregate
    all_cached = agg.cases_total > 0 and agg.cache_hits == agg.cases_total
    if result.skipped_reason == "no_api_key" and not all_cached:
        return GateOutcome(status="neutral", messages=[NO_KEY_MESSAGE])

    scored_ratio = agg.cases_scored / agg.cases_total if agg.cases_total else 0.0
    checks = [
        Check("min_scored_ratio", round(scored_ratio, 3), thresholds.min_scored_ratio, ">="),
        Check("category_accuracy", agg.category_accuracy, thresholds.category_accuracy, ">="),
        Check("evidence_validity", agg.evidence_validity, thresholds.evidence_validity, ">="),
        Check("key_fact_recall", agg.key_fact_recall, thresholds.key_fact_recall, ">="),
        Check("llm_coverage", agg.llm_coverage, thresholds.llm_coverage, ">="),
        Check(
            "latency_p95_ms",
            agg.latency_p95_ms,
            float(thresholds.max_latency_p95_ms),
            "<=",
        ),
    ]
    if agg.secret_leak_free is not None:
        checks.append(
            Check("secret_leak_free", agg.secret_leak_free, thresholds.secret_leak_free, ">=")
        )
    if thresholds.judge_score_mean is not None and agg.judge_score_mean is not None:
        checks.append(
            Check("judge_score_mean", agg.judge_score_mean, thresholds.judge_score_mean, ">=")
        )

    failures = [c.name for c in checks if not c.passed]
    messages = []
    if result.run.llm_mode == "rules":
        messages.append(RULES_MODE_MESSAGE)
    elif "llm_coverage" in failures:
        messages.append(LOW_COVERAGE_MESSAGE)
    if result.skipped_reason == "budget":
        messages.append("Some cases were skipped because the LLM request budget ran out.")
    return GateOutcome(
        status="fail" if failures else "pass", checks=checks, failures=failures, messages=messages
    )


def _fmt(value: float | None, name: str) -> str:
    if value is None:
        return "—"
    if name.endswith("_ms"):
        return f"{value / 1000:.1f} s"
    return f"{value:.2f}"


def render_gate_markdown(result: EvalResult, outcome: GateOutcome, mode: str) -> str:
    agg = result.aggregate
    run = result.run
    icon = {"pass": "PASS", "fail": "FAIL", "neutral": "NEUTRAL"}[outcome.status]
    lines = [
        f"## PipelineDoctor evals ({mode}): **{icon}**",
        "",
        f"Prompt `diagnose.{run.prompt_version}` (`{run.prompt_sha256[:12]}`) · engine "
        f"`{run.engine_fingerprint}` · LLM mode `{run.llm_mode}` · "
        f"{agg.cases_scored}/{agg.cases_total} cases scored · {agg.cache_hits} from cache · "
        f"{agg.total_llm_calls} LLM calls · ${agg.total_cost_usd:.4f}",
        "",
    ]
    lines += [f"> {m}" for m in outcome.messages]
    if outcome.messages:
        lines.append("")
    if outcome.checks:
        lines += ["| Metric | Value | Threshold | |", "|---|---:|---:|:---:|"]
        for check in outcome.checks:
            mark = "ok" if check.passed else "**below**"
            lines.append(
                f"| {check.name} | {_fmt(check.value, check.name)} | {check.op} "
                f"{_fmt(check.threshold, check.name)} | {mark} |"
            )
        lines.append("")
    lines += [
        "<details><summary>Per-case results</summary>",
        "",
        "| Case | Status | Expected | Predicted | Evidence | Key facts | Source | Cache |",
        "|---|---|---|---|---:|---:|---|:---:|",
    ]
    for case in result.cases:
        evidence = f"{case.evidence_valid}/{case.evidence_cited}" if case.status == "scored" else ""
        facts = (
            f"{case.key_facts_matched}/{case.key_facts_total}" if case.status == "scored" else ""
        )
        predicted = case.predicted_category.value if case.predicted_category else ""
        mark = "" if case.category_correct in (None, True) else " ✗"
        lines.append(
            f"| {case.case_id} | {case.status} | {case.expected_category.value} | "
            f"{predicted}{mark} | {evidence} | {facts} | {case.source or ''} | "
            f"{'hit' if case.cache_hit else ''} |"
        )
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def ledger_entry(
    result: EvalResult, outcome: GateOutcome, thresholds: Thresholds, run_url: str | None, mode: str
) -> LedgerEntry:
    agg = result.aggregate
    gated = {
        "category_accuracy": thresholds.category_accuracy,
        "evidence_validity": thresholds.evidence_validity,
        "key_fact_recall": thresholds.key_fact_recall,
    }
    if thresholds.judge_score_mean is not None:
        gated["judge_score_mean"] = thresholds.judge_score_mean
    return LedgerEntry(
        recorded_at=result.run.finished_at,
        git_sha=result.run.git_sha,
        mode=mode,
        subset=result.run.subset,
        prompt_version=result.run.prompt_version,
        models=result.run.models,
        engine_version=result.run.engine_version,
        cases_scored=agg.cases_scored,
        cases_total=agg.cases_total,
        category_accuracy=agg.category_accuracy,
        evidence_validity=agg.evidence_validity,
        key_fact_recall=agg.key_fact_recall,
        judge_score_mean=agg.judge_score_mean,
        llm_coverage=agg.llm_coverage,
        latency_p50_ms=agg.latency_p50_ms,
        latency_p95_ms=agg.latency_p95_ms,
        total_cost_usd=agg.total_cost_usd,
        thresholds=gated,
        passed=outcome.status == "pass",
        failures=outcome.failures,
        run_url=run_url,
    )
