from __future__ import annotations

import json

import respx

from pipelinedoctor_console.evals import EMPTY_NOTICE, EvalService, parse_ledger
from pipelinedoctor_console.github import GitHubReader
from pipelinedoctor_console.settings import Settings

from .conftest import API, REPO

LEDGER = f"{API}/repos/{REPO}/contents/ledger.jsonl"


def entry(day: int, passed: bool = True) -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "recorded_at": f"2026-10-{day:02d}T03:31:12Z",
            "git_sha": "a" * 40,
            "category_accuracy": 0.9,
            "passed": passed,
            "future_field": "ignored",
        }
    )


def test_parse_ledger_sorts_and_counts_bad_lines() -> None:
    text = "\n".join([entry(5), "{broken", entry(3), "", json.dumps({"passed": True})])
    entries, bad = parse_ledger(text)
    assert [e.recorded_at.day for e in entries] == [3, 5]
    assert bad == 2


def test_fixture_ledger(fixture_gh: GitHubReader) -> None:
    result = EvalService(fixture_gh).list()
    assert len(result.entries) == 14
    assert sum(not e.passed for e in result.entries) == 1
    assert result.notice is None


@respx.mock
def test_branch_missing(token_settings: Settings) -> None:
    route = respx.get(LEDGER).respond(404, json={"message": "Not Found"})
    result = EvalService(GitHubReader(token_settings)).list()
    assert result.entries == [] and result.notice == EMPTY_NOTICE
    assert route.calls[0].request.url.params["ref"] == "eval-history"


@respx.mock
def test_errors_and_partial_lines(token_settings: Settings) -> None:
    respx.get(LEDGER).respond(401)
    assert "401" in (EvalService(GitHubReader(token_settings)).list().notice or "")


@respx.mock
def test_bad_lines_reported(token_settings: Settings) -> None:
    respx.get(LEDGER).respond(200, text=entry(1) + "\nnot json\n")
    result = EvalService(GitHubReader(token_settings)).list()
    assert len(result.entries) == 1
    assert result.notice == "1 ledger line(s) could not be read and are not shown."


@respx.mock
def test_empty_file(token_settings: Settings) -> None:
    respx.get(LEDGER).respond(200, text="")
    assert EvalService(GitHubReader(token_settings)).list().notice == EMPTY_NOTICE
