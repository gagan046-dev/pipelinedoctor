"""All JSON contracts as Pydantic v2 models (ARCHITECTURE.md §6).

These models are the source of truth: `pipelinedoctor schema --write schemas` exports them as
JSON Schema files, and the Console mirrors them independently.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(StrEnum):
    DEPENDENCY = "dependency"
    TEST = "test"
    LINT = "lint"
    BUILD = "build"
    DOCKER = "docker"
    CONFIG = "config"
    SECRET_PERMISSION = "secret_permission"
    FLAKY = "flaky"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


# Words a model might use instead of the exact enum value.
_CATEGORY_ALIASES = {
    "dependencies": Category.DEPENDENCY,
    "tests": Category.TEST,
    "type": Category.LINT,
    "typecheck": Category.LINT,
    "type_error": Category.LINT,
    "compile": Category.BUILD,
    "permission": Category.SECRET_PERMISSION,
    "permissions": Category.SECRET_PERMISSION,
    "secret": Category.SECRET_PERMISSION,
    "secrets": Category.SECRET_PERMISSION,
    "auth": Category.SECRET_PERMISSION,
    "infra": Category.INFRASTRUCTURE,
    "network": Category.INFRASTRUCTURE,
    "configuration": Category.CONFIG,
}


def coerce_category(value: object) -> Category:
    text = re.sub(r"[^a-z]+", "_", str(value or "").lower()).strip("_")
    if text.startswith("secret_perm"):
        return Category.SECRET_PERMISSION
    try:
        return Category(text)
    except ValueError:
        return _CATEGORY_ALIASES.get(text, Category.UNKNOWN)


Source = Literal["llm", "rules_fallback", "rules_only"]
AttemptOutcome = Literal[
    "ok", "timeout", "rate_limited", "invalid_json", "provider_error", "budget_exhausted"
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- diagnosis report


class RunInfo(_Model):
    id: int = 0
    run_number: int | None = None
    run_attempt: int | None = None
    workflow_name: str = ""
    workflow_path: str | None = None
    event: str | None = None
    head_branch: str | None = None
    head_sha: str | None = None
    html_url: str | None = None
    conclusion: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PullRequestRef(_Model):
    number: int
    html_url: str


class JobRef(_Model):
    id: int
    name: str
    html_url: str | None = None


class StepRef(_Model):
    number: int
    name: str


class TimelineStep(_Model):
    number: int
    name: str
    conclusion: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class EvidenceItem(_Model):
    exhibit: str
    line: int
    text: str
    why: str = ""


class RecommendedAction(_Model):
    id: str
    action: str
    command: str | None = None


class PreClassification(_Model):
    category: Category
    rule_id: str
    confidence: float = Field(ge=0, le=1)


class LLMAttempt(_Model):
    model: str
    outcome: AttemptOutcome
    latency_ms: int = 0


class LLMInfo(_Model):
    model: str | None = None
    prompt_id: str = "diagnose"
    prompt_version: str = "v1"
    prompt_sha256: str = ""
    attempts: list[LLMAttempt] = Field(default_factory=list)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LogWindow(_Model):
    total_lines: int
    excerpt_lines: int
    ranges: list[tuple[int, int]]
    redactions: int = 0


class DiagnosisReport(_Model):
    schema_version: Literal["1"] = "1"
    occurrence_id: str
    created_at: str
    engine_version: str
    repo: str
    run: RunInfo
    pull_request: PullRequestRef | None = None
    failed_job: JobRef
    other_failed_jobs: list[JobRef] = Field(default_factory=list)
    failed_step: StepRef | None = None
    timeline: list[TimelineStep] = Field(default_factory=list)
    category: Category
    summary: str
    probable_cause: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    confidence_grade: Literal["A", "B", "C"]
    source: Source
    pre_classification: PreClassification | None = None
    llm: LLMInfo
    log_window: LogWindow
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- what the LLM returns


class LLMEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    line: int
    quote: str = ""
    why: str = ""


class LLMAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str
    command: str | None = None


class LLMDiagnosis(BaseModel):
    """The only shape the model is asked to produce. Unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")
    category: Category
    failed_step: str | None = None
    summary: str
    probable_cause: str
    evidence: list[LLMEvidence] = Field(default_factory=list)
    recommended_actions: list[LLMAction] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: object) -> Category:
        return coerce_category(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        if number > 1:  # some models answer in percent
            number /= 100
        return max(0.0, min(number, 1.0))

    @field_validator("verification", mode="before")
    @classmethod
    def _verification(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value


# --------------------------------------------------------------------------- eval case


class EvalStep(_Model):
    number: int
    name: str
    conclusion: str | None = None


class KeyFact(_Model):
    id: str
    any_of: list[str] = Field(min_length=1)


class PlantSecret(_Model):
    placeholder: str = "{{PLANTED_SECRET}}"
    prefix: str
    length: int = Field(ge=8, le=200)


class ExpectedOutcome(_Model):
    category: Category
    failed_step: str | None = None
    key_facts: list[KeyFact] = Field(default_factory=list)
    evidence_lines: list[int] = Field(default_factory=list)


class EvalCase(_Model):
    schema_version: Literal["1"] = "1"
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str
    subsets: list[Literal["pr", "all"]]
    workflow: str
    job: str
    steps: list[EvalStep]
    expected: ExpectedOutcome
    reference_fix: str
    plant_secret: PlantSecret | None = None
    notes: str | None = None


# --------------------------------------------------------------------------- eval result

CaseStatus = Literal["scored", "skipped_budget", "skipped_no_key", "error"]


class EvalRunInfo(_Model):
    started_at: str
    finished_at: str
    mode: Literal["pr", "nightly"]
    subset: Literal["pr", "all"]
    git_sha: str | None = None
    prompt_version: str
    prompt_sha256: str
    models: list[str]
    engine_fingerprint: str
    engine_version: str
    llm_mode: Literal["live", "cache-only", "rules"]
    judge: bool = False


class EvalCaseResult(_Model):
    case_id: str
    status: CaseStatus
    cache_hit: bool = False
    expected_category: Category
    predicted_category: Category | None = None
    category_correct: bool | None = None
    evidence_cited: int = 0
    evidence_valid: int = 0
    evidence_validity: float | None = None
    key_facts_total: int = 0
    key_facts_matched: int = 0
    key_fact_recall: float | None = None
    no_secret_leak: bool | None = None
    judge_score: float | None = None
    source: Source | None = None
    model_used: str | None = None
    llm_calls: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class EvalAggregate(_Model):
    cases_total: int
    cases_scored: int
    cases_skipped: int
    category_accuracy: float | None = None
    evidence_validity: float | None = None
    key_fact_recall: float | None = None
    secret_leak_free: float | None = None
    judge_score_mean: float | None = None
    llm_coverage: float | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    total_cost_usd: float = 0.0
    total_llm_calls: int = 0
    cache_hits: int = 0


class EvalResult(_Model):
    schema_version: Literal["1"] = "1"
    run: EvalRunInfo
    cases: list[EvalCaseResult]
    aggregate: EvalAggregate
    skipped_reason: Literal["no_api_key", "budget"] | None = None


# --------------------------------------------------------------------------- ledger


class LedgerEntry(_Model):
    schema_version: Literal["1"] = "1"
    recorded_at: str
    git_sha: str | None = None
    mode: str
    subset: str
    prompt_version: str
    models: list[str]
    engine_version: str
    cases_scored: int
    cases_total: int
    category_accuracy: float | None = None
    evidence_validity: float | None = None
    key_fact_recall: float | None = None
    judge_score_mean: float | None = None
    llm_coverage: float | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    total_cost_usd: float = 0.0
    thresholds: dict[str, float]
    passed: bool
    failures: list[str] = Field(default_factory=list)
    run_url: str | None = None


# --------------------------------------------------------------------------- schema export

SCHEMA_FILES: dict[str, type[BaseModel]] = {
    "diagnosis-report.v1.schema.json": DiagnosisReport,
    "eval-case.v1.schema.json": EvalCase,
    "eval-result.v1.schema.json": EvalResult,
    "ledger-entry.v1.schema.json": LedgerEntry,
}


def json_schema(model: type[BaseModel], filename: str) -> dict[str, Any]:
    schema = model.model_json_schema()
    base = "https://github.com/gagan046-dev/pipelinedoctor/blob/main/schemas/"
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": base + filename,
        **schema,
    }


def write_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, model in SCHEMA_FILES.items():
        path = directory / filename
        text = json.dumps(json_schema(model, filename), indent=2, sort_keys=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
