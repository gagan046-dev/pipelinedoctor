"""Optional LLM-as-judge: rates the recommended fix 1-5 against the case's reference fix.

Every judge call is one more request against the same budget, which is why it only runs in the
nightly workflow and only on a sample of cases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from pipelinedoctor.engine.llm import LLMClient, RequestBudget, call_with_fallback
from pipelinedoctor.engine.parse import extract_json
from pipelinedoctor.engine.prompting import Prompt
from pipelinedoctor.schemas import DiagnosisReport, EvalCase


class JudgeVerdict(BaseModel):
    score: float = Field(ge=1, le=5)
    rationale: str = ""


@dataclass
class JudgeOutcome:
    verdict: JudgeVerdict | None
    calls: int
    latency_ms: int


def _parse(text: str) -> JudgeVerdict:
    return JudgeVerdict.model_validate(extract_json(text))


def judge_case(
    client: LLMClient,
    budget: RequestBudget,
    models: list[str],
    prompt: Prompt,
    case: EvalCase,
    report: DiagnosisReport,
    timeout_s: float,
) -> JudgeOutcome:
    diagnosis = {
        "category": report.category.value,
        "summary": report.summary,
        "probable_cause": report.probable_cause,
        "recommended_actions": [a.model_dump() for a in report.recommended_actions],
        "verification": report.verification,
    }
    system, user = prompt.render(
        expected_category=case.expected.category.value,
        key_facts=json.dumps([f.any_of for f in case.expected.key_facts]),
        reference_fix=case.reference_fix,
        diagnosis_json=json.dumps(diagnosis, indent=2),
    )
    result = call_with_fallback(client, models, system, user, budget, timeout_s, _parse)
    return JudgeOutcome(verdict=result.value, calls=result.calls, latency_ms=result.latency_ms)
