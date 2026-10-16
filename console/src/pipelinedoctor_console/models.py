"""Pydantic mirrors of the engine's JSON contracts (ARCHITECTURE.md section 6) and the API shapes.

The console deliberately does not import the `pipelinedoctor` engine package: the two ship as
separate images with separate lock files. Contract tests compare these models against
`schemas/examples/*.json` so drift is caught in CI.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    dependency = "dependency"
    test = "test"
    lint = "lint"
    build = "build"
    docker = "docker"
    config = "config"
    secret_permission = "secret_permission"
    flaky = "flaky"
    infrastructure = "infrastructure"
    unknown = "unknown"


class _Lenient(BaseModel):
    # Newer engine versions may add fields; the console must keep rendering older reports too.
    model_config = ConfigDict(extra="ignore")


class RunInfo(_Lenient):
    id: int
    run_number: int | None = None
    run_attempt: int | None = None
    workflow_name: str = ""
    workflow_path: str | None = None
    event: str | None = None
    head_branch: str | None = None
    head_sha: str | None = None
    html_url: str | None = None
    conclusion: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PullRequestRef(_Lenient):
    number: int
    html_url: str | None = None


class JobRef(_Lenient):
    id: int | None = None
    name: str
    html_url: str | None = None


class StepRef(_Lenient):
    number: int | None = None
    name: str


class TimelineStep(_Lenient):
    number: int | None = None
    name: str
    conclusion: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class Evidence(_Lenient):
    exhibit: str
    line: int
    text: str
    why: str | None = None


class Action(_Lenient):
    id: str
    action: str
    command: str | None = None


class PreClassification(_Lenient):
    category: str
    rule_id: str | None = None
    confidence: float | None = None


class Attempt(_Lenient):
    model: str
    outcome: str
    latency_ms: int | None = None


class LLMInfo(_Lenient):
    model: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_sha256: str | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class LogWindow(_Lenient):
    total_lines: int | None = None
    excerpt_lines: int | None = None
    ranges: list[list[int]] = Field(default_factory=list)
    redactions: int | None = None


class DiagnosisReport(_Lenient):
    schema_version: str = "1"
    occurrence_id: str
    created_at: datetime
    engine_version: str | None = None
    repo: str
    run: RunInfo
    pull_request: PullRequestRef | None = None
    failed_job: JobRef | None = None
    other_failed_jobs: list[JobRef] = Field(default_factory=list)
    failed_step: StepRef | None = None
    timeline: list[TimelineStep] = Field(default_factory=list)
    category: str = Category.unknown.value
    summary: str = ""
    probable_cause: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_actions: list[Action] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_grade: str | None = None
    source: str = "llm"
    pre_classification: PreClassification | None = None
    llm: LLMInfo | None = None
    log_window: LogWindow | None = None
    warnings: list[str] = Field(default_factory=list)


class LedgerEntry(_Lenient):
    schema_version: str = "1"
    recorded_at: datetime
    git_sha: str
    mode: str = "nightly"
    subset: str | None = None
    prompt_version: str | None = None
    models: list[str] = Field(default_factory=list)
    engine_version: str | None = None
    cases_scored: int | None = None
    cases_total: int | None = None
    category_accuracy: float | None = None
    evidence_validity: float | None = None
    key_fact_recall: float | None = None
    judge_score_mean: float | None = None
    llm_coverage: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    total_cost_usd: float | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)
    passed: bool
    failures: list[str] = Field(default_factory=list)
    run_url: str | None = None


# --- API responses ------------------------------------------------------------------------

Status = Literal["diagnosed", "pending", "skipped"]


class OccurrenceRow(BaseModel):
    occurrence_id: str
    created_at: datetime
    repo: str
    workflow: str | None
    job: str | None
    failed_step: str | None
    category: str | None
    confidence: float | None
    confidence_grade: str | None
    status: Status
    run_url: str | None
    pr_number: int | None
    doctor_run_id: int


class OccurrenceList(BaseModel):
    items: list[OccurrenceRow]
    fetched_at: datetime
    notice: str | None = None


class OccurrenceDetail(BaseModel):
    status: Status
    report: DiagnosisReport | None
    doctor_run_url: str | None
    excerpt: dict[int, str] = Field(default_factory=dict)
    notice: str | None = None


class EvalList(BaseModel):
    entries: list[LedgerEntry]
    fetched_at: datetime
    notice: str | None = None


class DoraMetrics(BaseModel):
    window_days: int
    environment: str
    deployments: int
    deployment_frequency_per_week: float | None
    lead_time_hours_median: float | None
    change_failure_rate: float | None
    time_to_restore_hours_median: float | None
    computed_at: datetime
    notes: list[str] = Field(default_factory=list)
    notice: str | None = None
