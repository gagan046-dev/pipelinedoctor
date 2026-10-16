from __future__ import annotations

import pytest

from pipelinedoctor.engine.parse import (
    clean_text,
    extract_json,
    to_llm_diagnosis,
    validate_evidence,
)
from pipelinedoctor.engine.preprocess import Excerpt
from pipelinedoctor.schemas import Category, LLMEvidence, coerce_category


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure! Here is the diagnosis:\n{"a": 1}\nHope this helps.',
        'prefix {not json} then {"a": 1, "b": "brace } in string"} tail',
    ],
)
def test_extract_json_variants(text: str) -> None:
    assert extract_json(text)["a"] == 1


def test_extract_json_raises_without_object() -> None:
    with pytest.raises(ValueError):
        extract_json("no json here [1, 2]")


def test_to_llm_diagnosis_coerces_sloppy_output() -> None:
    diagnosis = to_llm_diagnosis(
        '{"category": "Secret / Permission", "summary": "s", "probable_cause": "c",'
        ' "confidence": 85, "verification": "re-run", "evidence": [], "extra": true}'
    )
    assert diagnosis.category == Category.SECRET_PERMISSION
    assert diagnosis.confidence == 0.85
    assert diagnosis.verification == ["re-run"]


def test_to_llm_diagnosis_missing_field_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        to_llm_diagnosis('{"category": "test"}')


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("dependencies", "dependency"),
        ("weird", "unknown"),
        ("Infra", "infrastructure"),
        ("", "unknown"),
    ],
)
def test_coerce_category(raw: str, expected: str) -> None:
    assert coerce_category(raw).value == expected


EXCERPT = Excerpt(
    lines={
        10: "E       assert   106.0 == 107.0",
        11: "FAILED tests/test_totals.py::test_tax",
        12: "[REDACTED:assignment] used",
    },
    total_lines=20,
)


def test_validate_evidence_keeps_real_lines_only() -> None:
    check = validate_evidence(
        [
            LLMEvidence(line=10, quote="10| assert 106.0 == 107.0", why="assertion"),
            LLMEvidence(line=99, quote="anything", why="invented line"),
            LLMEvidence(line=11, quote="PASSED everything", why="wrong quote"),
            LLMEvidence(line=11, quote="", why="empty quote"),
            LLMEvidence(line=11, quote="FAILED tests/test_totals.py...", why="truncated"),
            LLMEvidence(line=10, quote="assert 106.0", why="duplicate line"),
        ],
        EXCERPT,
    )
    assert check.cited == 6
    assert check.valid == 3
    assert [(e.exhibit, e.line) for e in check.items] == [("A", 10), ("B", 11)]
    assert check.items[0].text == "E       assert   106.0 == 107.0"  # the real line, not the quote
    assert len(check.warnings) == 3


def test_validate_evidence_caps_exhibits() -> None:
    excerpt = Excerpt(lines={n: f"error line {n}" for n in range(1, 9)}, total_lines=8)
    check = validate_evidence(
        [LLMEvidence(line=n, quote=f"error line {n}") for n in range(1, 9)], excerpt
    )
    assert len(check.items) == 5 and check.valid == 8


def test_clean_text_strips_html_and_limits() -> None:
    assert clean_text("<b>bold</b> <!-- hidden --> text", 100) == "bold  text"
    assert clean_text("x" * 50, 10) == "x" * 9 + "…"
