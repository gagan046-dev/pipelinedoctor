"""Contract tests: the console's models must accept what the engine writes (ARCHITECTURE.md §6).

The engine's examples live in ../schemas/examples (owned by the engine). Builders work in
parallel, so these tests skip when the examples are not there yet; the bundled fixtures are
always checked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipelinedoctor_console.models import Category, DiagnosisReport, LedgerEntry
from pipelinedoctor_console.settings import BUILTIN_FIXTURES

EXAMPLES = Path(__file__).resolve().parents[2] / "schemas" / "examples"
REPORT_EXAMPLES = sorted(EXAMPLES.glob("diagnosis-report.*.json")) if EXAMPLES.is_dir() else []
CATEGORIES = {c.value for c in Category}


@pytest.mark.parametrize("path", REPORT_EXAMPLES, ids=lambda p: p.name)
def test_engine_report_examples(path: Path) -> None:
    report = DiagnosisReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert report.category in CATEGORIES
    assert report.source in {"llm", "rules_fallback", "rules_only"}


def test_engine_ledger_example() -> None:
    path = EXAMPLES / "ledger.jsonl"
    if not path.exists():
        pytest.skip("schemas/examples/ledger.jsonl not present yet")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            LedgerEntry.model_validate(json.loads(line))


def test_engine_report_schema_fields_known() -> None:
    schema = EXAMPLES.parent / "diagnosis-report.v1.schema.json"
    if not schema.exists():
        pytest.skip("schemas/diagnosis-report.v1.schema.json not present yet")
    engine_fields = set(json.loads(schema.read_text(encoding="utf-8"))["properties"])
    assert set(DiagnosisReport.model_fields) <= engine_fields


@pytest.mark.parametrize(
    "path", sorted((BUILTIN_FIXTURES / "diagnoses").glob("*.json")), ids=lambda p: p.name
)
def test_bundled_fixture_reports(path: Path) -> None:
    report = DiagnosisReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert report.category in CATEGORIES
    letters = [e.exhibit for e in report.evidence]
    assert letters == [chr(ord("A") + i) for i in range(len(letters))]
    assert [a.id for a in report.recommended_actions] == [
        f"R{i + 1}" for i in range(len(report.recommended_actions))
    ]
    if report.source != "llm":
        assert report.llm is None or report.llm.model is None


def test_bundled_fixtures_have_engine_required_keys() -> None:
    schema = EXAMPLES.parent / "diagnosis-report.v1.schema.json"
    if not schema.exists():
        pytest.skip("schemas/diagnosis-report.v1.schema.json not present yet")
    required = set(json.loads(schema.read_text(encoding="utf-8"))["required"])
    for path in (BUILTIN_FIXTURES / "diagnoses").glob("*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        assert required <= set(report), (path.name, required - set(report))
        assert isinstance(report["failed_job"], dict), path.name


def test_every_category_has_a_fixture_or_is_unknown() -> None:
    seen = {
        json.loads(p.read_text(encoding="utf-8"))["category"]
        for p in (BUILTIN_FIXTURES / "diagnoses").glob("*.json")
    }
    assert seen == CATEGORIES
