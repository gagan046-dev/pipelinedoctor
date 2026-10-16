"""Paths and fixture loaders shared by the tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "github"
CASES_DIR = ROOT / "evals" / "cases"
# A 5-case copy of the dataset keeps unit tests independent of evals/ (added in stage 06).
MINI_CASES = Path(__file__).parent / "fixtures" / "eval-cases"
THRESHOLDS = Path(__file__).parent / "fixtures" / "thresholds.yaml"
DATASET_PRESENT = CASES_DIR.is_dir()
requires_dataset = pytest.mark.skipif(not DATASET_PRESENT, reason="evals/cases not present yet")
REPO = "gagan046-dev/pipelinedoctor"
API = "https://api.github.com"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
