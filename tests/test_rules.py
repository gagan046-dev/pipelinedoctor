from __future__ import annotations

import pytest

from helpers import CASES_DIR, DATASET_PRESENT, MINI_CASES
from pipelinedoctor.config import Settings
from pipelinedoctor.engine.diagnose import prepare_excerpt
from pipelinedoctor.engine.preprocess import Excerpt
from pipelinedoctor.engine.rules import RULES, RULES_BY_ID, preclassify
from pipelinedoctor.evals.dataset import LoadedCase, load_cases
from pipelinedoctor.schemas import Category

CASES = load_cases(CASES_DIR if DATASET_PRESENT else MINI_CASES, "all")


def test_rule_table_shape() -> None:
    assert len(RULES) >= 20
    assert len(RULES_BY_ID) == len(RULES), "rule ids must be unique"
    covered = {rule.category for rule in RULES}
    assert covered == set(Category) - {Category.UNKNOWN}


@pytest.mark.parametrize("loaded", CASES, ids=[c.id for c in CASES])
def test_rules_classify_every_dataset_case(loaded: LoadedCase) -> None:
    excerpt = prepare_excerpt(loaded.log_text, Settings(), [])
    result = preclassify(excerpt)
    assert result is not None
    assert result.category == loaded.case.expected.category


def test_no_match_returns_none() -> None:
    excerpt = Excerpt(lines={1: "all good", 2: "done"}, total_lines=2)
    assert preclassify(excerpt) is None


def test_higher_confidence_wins_over_generic_test_failure() -> None:
    excerpt = Excerpt(
        lines={
            1: "FAILED tests/test_db.py::test_save - psycopg.OperationalError",
            2: "E   connection to server at '127.0.0.1', port 5433 failed: Connection refused",
        },
        total_lines=2,
    )
    result = preclassify(excerpt)
    assert result is not None and result.category == Category.INFRASTRUCTURE


def test_artifact_digest_line_is_not_a_ruff_finding() -> None:
    # Seen live on the first demo PR: upload-artifact prints "SHA256 digest of ...", which an
    # over-broad ruff pattern ("letters+digits, space") classified as a lint failure.
    excerpt = Excerpt(
        lines={
            1: "E       AssertionError: assert Decimal('4.19') == Decimal('4.20')",
            2: "FAILED tests/test_totals.py::test_total_with_tax - AssertionError",
            3: "SHA256 digest of uploaded artifact is 4d13c16d4973e131b3cc75ca762a0939",
        },
        total_lines=3,
    )
    result = preclassify(excerpt)
    assert result is not None and result.category == Category.TEST
