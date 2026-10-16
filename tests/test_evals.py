from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeLLM, llm_answer
from helpers import CASES_DIR, MINI_CASES, ROOT, THRESHOLDS, requires_dataset
from pipelinedoctor.config import Settings
from pipelinedoctor.engine.llm import LLMError
from pipelinedoctor.evals.cache import ResultCache, case_key
from pipelinedoctor.evals.dataset import DatasetError, load_case, load_cases
from pipelinedoctor.evals.gate import (
    LOW_COVERAGE_MESSAGE,
    NO_KEY_MESSAGE,
    evaluate_gate,
    ledger_entry,
    load_thresholds,
    render_gate_markdown,
)
from pipelinedoctor.evals.runner import EvalOptions, make_fake_secret, run_evals
from pipelinedoctor.evals.scoring import aggregate, fact_matches, percentile
from pipelinedoctor.schemas import (
    Category,
    EvalCaseResult,
    EvalResult,
    KeyFact,
    PlantSecret,
)

# --------------------------------------------------------------------------- dataset


@requires_dataset
def test_dataset_shape() -> None:
    cases = load_cases(CASES_DIR, "all")
    assert len(cases) == 30
    assert len(load_cases(CASES_DIR, "pr")) == 10
    assert {c.case.expected.category for c in cases} == set(Category) - {Category.UNKNOWN}
    assert sum(1 for c in cases if c.case.plant_secret) >= 1
    for loaded in cases:
        lines = loaded.log_text.splitlines()
        assert 80 <= len(lines) <= 1500, loaded.id
        assert all(1 <= n <= len(lines) for n in loaded.case.expected.evidence_lines)
        failed = [s for s in loaded.case.steps if s.conclusion == "failure"]
        assert failed[0].name == loaded.case.expected.failed_step


def test_dataset_errors(tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        load_cases(tmp_path / "missing")
    source = MINI_CASES / "oom-exit-137"
    wrong = tmp_path / "renamed"
    wrong.mkdir()
    for name in ("case.yaml", "log.txt"):
        (wrong / name).write_bytes((source / name).read_bytes())
    with pytest.raises(DatasetError):
        load_case(wrong)


# --------------------------------------------------------------------------- cache key


def _key(case_yaml: bytes, **overrides: object) -> str:
    params: dict[str, object] = {
        "case_yaml": case_yaml,
        "log_sha256": "a" * 64,
        "prompt_id": "diagnose",
        "prompt_version": "v1",
        "prompt_sha256": "b" * 64,
        "models": ["m1", "m2"],
        "engine_fingerprint": "c" * 16,
    }
    params.update(overrides)
    return case_key(**params)  # type: ignore[arg-type]


def test_cache_key_ignores_line_endings_but_not_content() -> None:
    lf = b"id: x\ntitle: y\n"
    assert _key(lf) == _key(lf.replace(b"\n", b"\r\n"))
    assert _key(lf) != _key(lf, prompt_sha256="d" * 64)
    assert _key(lf) != _key(lf, models=["m2", "m1"])
    assert _key(lf) != _key(lf, engine_fingerprint="e" * 16)


def test_result_cache_roundtrip(tmp_path: Path) -> None:
    cache = ResultCache(tmp_path)
    assert cache.get_diagnosis("k") is None
    cache.put_diagnosis("k", {"a": 1})
    assert cache.get_diagnosis("k") == {"a": 1}
    assert (tmp_path / "v1" / "diagnose" / "k.json").is_file()
    cache.put_judge("k", "f" * 64, {"score": 4})
    assert cache.get_judge("k", "f" * 64) == {"score": 4}
    assert ResultCache(tmp_path, read=False).get_diagnosis("k") is None
    (tmp_path / "v1" / "diagnose" / "bad.json").write_text("{nope", encoding="utf-8")
    assert cache.get_diagnosis("bad") is None


# --------------------------------------------------------------------------- scoring


def test_fact_matching() -> None:
    assert fact_matches(KeyFact(id="a", any_of=["ERESOLVE"]), "npm eresolve error")
    assert fact_matches(KeyFact(id="b", any_of=["re:exit (code )?137"]), "Exit code 137")
    assert not fact_matches(KeyFact(id="c", any_of=["foo", "re:ba[rz]$"]), "bar baz qux")


def test_percentile() -> None:
    assert percentile([], 95) is None
    assert percentile([5], 95) == 5
    assert percentile(list(range(1, 21)), 50) == 10
    assert percentile(list(range(1, 21)), 95) == 19


def _result(**kw: object) -> EvalCaseResult:
    base: dict[str, object] = {
        "case_id": "c",
        "status": "scored",
        "expected_category": "test",
        "predicted_category": "test",
        "category_correct": True,
        "evidence_cited": 2,
        "evidence_valid": 2,
        "key_fact_recall": 1.0,
        "source": "llm",
        "latency_ms": 1000,
    }
    base.update(kw)
    return EvalCaseResult.model_validate(base)


def test_aggregate() -> None:
    agg = aggregate(
        [
            _result(),
            _result(
                category_correct=False,
                evidence_valid=1,
                key_fact_recall=0.5,
                source="rules_fallback",
            ),
            _result(status="skipped_budget", source=None),
            _result(no_secret_leak=True, judge_score=4.0, cache_hit=True, llm_calls=0),
        ]
    )
    assert (agg.cases_total, agg.cases_scored, agg.cases_skipped) == (4, 3, 1)
    assert agg.category_accuracy == pytest.approx(0.667, abs=1e-3)
    assert agg.evidence_validity == pytest.approx(5 / 6, abs=1e-3)
    assert agg.key_fact_recall == pytest.approx(0.833, abs=1e-3)
    assert agg.llm_coverage == pytest.approx(0.667, abs=1e-3)
    assert agg.secret_leak_free == 1.0 and agg.judge_score_mean == 4.0
    assert agg.cache_hits == 1


# --------------------------------------------------------------------------- runner


def answer_for(case_id: str, category: str | None = None) -> dict[str, object]:
    loaded = next(c for c in load_cases(MINI_CASES, "all") if c.id == case_id)
    expected = loaded.case.expected
    from pipelinedoctor.engine.preprocess import clean_log

    lines = clean_log(loaded.log_text)
    line = expected.evidence_lines[0]
    facts = " ".join(f.any_of[0] for f in expected.key_facts)
    return llm_answer(
        category or expected.category.value,
        line=line,
        quote=lines[line - 1].strip()[:60],
        summary=f"Diagnosis mentions {facts}",
    )


def pr_answers() -> list[dict[str, object]]:
    return [answer_for(c.id) for c in load_cases(MINI_CASES, "pr")]


def test_live_run_scores_and_caches(tmp_path: Path) -> None:
    settings = Settings(min_request_interval_s=0)
    options = EvalOptions(subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path)
    llm = FakeLLM(*pr_answers())
    result = run_evals(options, settings, llm)
    agg = result.aggregate
    assert agg.cases_scored == 3 and agg.category_accuracy == 1.0
    assert agg.evidence_validity == 1.0 and agg.key_fact_recall == 1.0
    assert agg.llm_coverage == 1.0 and agg.total_llm_calls == 3 and agg.cache_hits == 0
    assert result.skipped_reason is None
    assert evaluate_gate(result, load_thresholds(THRESHOLDS, "pr")).status == "pass"

    second = run_evals(options, settings, FakeLLM())
    assert second.aggregate.cache_hits == 3 and second.aggregate.total_llm_calls == 0
    assert second.aggregate.category_accuracy == 1.0

    no_read = run_evals(
        EvalOptions(subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path, cache_read=False),
        settings,
        FakeLLM(*pr_answers()),
    )
    assert no_read.aggregate.cache_hits == 0 and no_read.aggregate.total_llm_calls == 3


def test_no_key_is_neutral(tmp_path: Path) -> None:
    options = EvalOptions(
        subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path, llm_mode="cache-only"
    )
    result = run_evals(options, Settings())
    assert result.skipped_reason == "no_api_key"
    assert {c.status for c in result.cases} == {"skipped_no_key"}
    outcome = evaluate_gate(result, load_thresholds(THRESHOLDS, "pr"))
    assert outcome.status == "neutral" and outcome.messages == [NO_KEY_MESSAGE]
    assert "NEUTRAL" in render_gate_markdown(result, outcome, "pr")


def test_budget_exhaustion_skips_remaining_cases(tmp_path: Path) -> None:
    options = EvalOptions(subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path, max_requests=1)
    result = run_evals(options, Settings(min_request_interval_s=0), FakeLLM(*pr_answers()))
    statuses = [c.status for c in result.cases]
    assert statuses.count("scored") == 1 and statuses.count("skipped_budget") == 2
    assert result.skipped_reason == "budget"
    outcome = evaluate_gate(result, load_thresholds(THRESHOLDS, "pr"))
    assert outcome.status == "fail" and "min_scored_ratio" in outcome.failures


def test_provider_outage_fails_honestly_and_is_not_cached(tmp_path: Path) -> None:
    errors = [LLMError("rate_limited", "429")] * 20
    options = EvalOptions(subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path, max_requests=20)
    result = run_evals(options, Settings(min_request_interval_s=0), FakeLLM(*errors))
    assert all(c.source == "rules_fallback" for c in result.cases)
    outcome = evaluate_gate(result, load_thresholds(THRESHOLDS, "pr"))
    assert outcome.status == "fail" and LOW_COVERAGE_MESSAGE in outcome.messages
    assert not (tmp_path / "v1" / "diagnose").exists()


def test_rules_mode_runs_offline(tmp_path: Path) -> None:
    options = EvalOptions(subset="all", cases_dir=MINI_CASES, cache_dir=tmp_path, llm_mode="rules")
    result = run_evals(options, Settings())
    assert result.aggregate.cases_scored == 5
    assert result.aggregate.category_accuracy == 1.0
    assert result.aggregate.secret_leak_free == 1.0
    assert result.aggregate.llm_coverage == 0.0


def test_planted_secret_never_reaches_the_report(tmp_path: Path) -> None:
    secret_case = next(c for c in load_cases(MINI_CASES, "all") if c.case.plant_secret)
    assert "{{PLANTED_SECRET}}" in secret_case.log_text
    leaking = llm_answer(
        "secret_permission",
        line=secret_case.case.expected.evidence_lines[0],
        quote="npm error code E401",
        summary="The token in .npmrc was rejected",
    )
    options = EvalOptions(subset="all", cases_dir=MINI_CASES, cache_dir=tmp_path)
    llm = FakeLLM(*([leaking] * 5))
    result = run_evals(options, Settings(min_request_interval_s=0, max_llm_requests=40), llm)
    row = next(c for c in result.cases if c.case_id == secret_case.id)
    assert row.no_secret_leak is True
    prompts = "\n".join(call.user for call in llm.calls)
    # Three placements (after _authToken=, after Bearer, bare on a comment line): all removed.
    assert prompts.count("[REDACTED:npm_token]") >= 3


def test_make_fake_secret() -> None:
    secret = make_fake_secret(PlantSecret(prefix="npm_", length=40))
    assert secret.startswith("npm_") and len(secret) == 40


def test_judge_runs_on_a_sample_and_is_cached(tmp_path: Path) -> None:
    settings = Settings(min_request_interval_s=0)
    options = EvalOptions(
        subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path, judge=True, judge_sample=2
    )
    verdicts: list[Any] = [{"score": 4, "rationale": "good"}, '{"score": 5, "rationale": "great"}']
    llm = FakeLLM(*pr_answers(), *verdicts)
    result = run_evals(options, settings, llm)
    judged = [c for c in result.cases if c.judge_score is not None]
    assert len(judged) == 2 and result.aggregate.judge_score_mean == 4.5
    assert result.aggregate.total_llm_calls == 5
    again = run_evals(options, settings, FakeLLM())
    assert again.aggregate.judge_score_mean == 4.5 and again.aggregate.total_llm_calls == 0


def test_case_errors_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pipelinedoctor.evals import runner

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(runner, "analyze_log", boom)
    options = EvalOptions(subset="pr", cases_dir=MINI_CASES, cache_dir=tmp_path, llm_mode="rules")
    result = run_evals(options, Settings())
    assert {c.status for c in result.cases} == {"error"}
    assert result.cases[0].error == "RuntimeError: kaputt"


# --------------------------------------------------------------------------- gate


def _eval_result(**aggregate_overrides: object) -> EvalResult:
    path = ROOT / "schemas" / "examples" / "eval-result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["aggregate"].update(aggregate_overrides)
    return EvalResult.model_validate(data)


def test_gate_thresholds() -> None:
    limits = load_thresholds(THRESHOLDS, "pr")
    assert evaluate_gate(_eval_result(), limits).status == "pass"
    failing = evaluate_gate(_eval_result(category_accuracy=0.5, latency_p95_ms=999_999), limits)
    assert failing.status == "fail"
    assert failing.failures == ["category_accuracy", "latency_p95_ms"]
    leak = evaluate_gate(_eval_result(secret_leak_free=0.0), limits)
    assert leak.failures == ["secret_leak_free"]


def test_nightly_gate_includes_judge() -> None:
    limits = load_thresholds(THRESHOLDS, "nightly")
    outcome = evaluate_gate(_eval_result(judge_score_mean=2.0), limits)
    assert "judge_score_mean" in outcome.failures


def test_thresholds_file_errors(tmp_path: Path) -> None:
    bad = tmp_path / "t.yaml"
    bad.write_text("schema_version: '2'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_thresholds(bad, "pr")
    bad.write_text("schema_version: '1'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_thresholds(bad, "pr")


def test_ledger_entry_and_markdown() -> None:
    limits = load_thresholds(THRESHOLDS, "nightly")
    result = _eval_result(judge_score_mean=4.1)
    outcome = evaluate_gate(result, limits)
    entry = ledger_entry(result, outcome, limits, "https://x/runs/1", "nightly")
    assert entry.passed is True and entry.thresholds["judge_score_mean"] == 3.5
    markdown = render_gate_markdown(result, outcome, "nightly")
    assert "| category_accuracy |" in markdown and "Per-case results" in markdown
