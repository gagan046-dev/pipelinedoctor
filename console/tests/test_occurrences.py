from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from pipelinedoctor_console.github import GitHubReader
from pipelinedoctor_console.occurrences import (
    NO_TOKEN_REPORTS,
    OccurrenceNotFound,
    OccurrenceService,
    occurrence_id_for,
    parse_excerpt,
    workflow_from_title,
)
from pipelinedoctor_console.settings import BUILTIN_FIXTURES, Settings

from .conftest import API, REPO

RUNS = f"{API}/repos/{REPO}/actions/workflows/doctor.yml/runs"


def run(
    run_id: int,
    number: int,
    status: str = "completed",
    conclusion: str | None = "success",
    title: str = "Doctor: CI #9",
) -> dict[str, object]:
    return {
        "id": run_id,
        "run_number": number,
        "status": status,
        "conclusion": conclusion,
        "display_title": title,
        "created_at": "2026-10-10T10:00:00Z",
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def test_helpers() -> None:
    assert (
        occurrence_id_for({"created_at": "2026-01-02T00:00:00Z", "run_number": 7}) == "PD-2026-0007"
    )
    assert workflow_from_title("Doctor: Demo CI #12") == "Demo CI"
    assert workflow_from_title("Doctor: Nightly Evals") == "Nightly Evals"
    assert workflow_from_title("something else") is None
    text = "  10 | ten\n11: eleven\n12\ttwelve\nnot a line\n  40 | far away\n"
    assert parse_excerpt(text, {11}) == {10: "ten", 11: "eleven", 12: "twelve"}


def test_fixture_register(fixture_gh: GitHubReader) -> None:
    result = OccurrenceService(fixture_gh).list()
    ids = [i.occurrence_id for i in result.items]
    assert ids[0] == "PD-2026-0052"
    assert "PD-2026-0036" not in ids  # conclusion "skipped": nothing failed, not an occurrence
    by_id = {i.occurrence_id: i for i in result.items}
    assert by_id["PD-2026-0052"].status == "pending"
    assert by_id["PD-2026-0052"].workflow == "Demo CI"
    assert by_id["PD-2026-0048"].status == "skipped"
    diagnosed = by_id["PD-2026-0051"]
    assert diagnosed.status == "diagnosed"
    assert diagnosed.category == "test"
    assert diagnosed.pr_number == 31
    assert diagnosed.failed_step == "Run tests"
    startup = by_id["PD-2026-0045"]
    assert startup.category == "config" and startup.job is None  # failed_job id 0 = no jobs
    assert result.notice is None


def test_fixture_report_with_excerpt(fixture_gh: GitHubReader) -> None:
    detail = OccurrenceService(fixture_gh).get("PD-2026-0051")
    assert detail.status == "diagnosed"
    assert detail.report is not None and detail.report.evidence[0].line == 812
    assert detail.excerpt[812].startswith("E       AssertionError")
    assert 808 in detail.excerpt and 700 not in detail.excerpt


def test_pending_and_unknown(fixture_gh: GitHubReader) -> None:
    service = OccurrenceService(fixture_gh)
    pending = service.get("PD-2026-0052")
    assert pending.status == "pending" and pending.report is None
    for bad in ("PD-2026-9999", "PD-2025-0051", "nonsense", "PD-2026-0036"):
        with pytest.raises(OccurrenceNotFound):
            service.get(bad)


def test_limit(fixture_gh: GitHubReader) -> None:
    assert len(OccurrenceService(fixture_gh).list(limit=3).items) == 3


@respx.mock
def test_without_token_uses_conclusions(cache_dir: Path) -> None:
    respx.get(RUNS).respond(
        200,
        json={"workflow_runs": [run(1, 2), run(2, 1, conclusion="failure")]},
    )
    gh = GitHubReader(Settings(cache_dir=cache_dir))
    result = OccurrenceService(gh).list()
    assert [i.status for i in result.items] == ["diagnosed", "skipped"]
    assert result.notice is not None
    assert "without a GitHub token" in result.notice and NO_TOKEN_REPORTS in result.notice


@respx.mock
def test_register_asks_github_for_interesting_states_only(cache_dir: Path) -> None:
    runs = {
        "success": [dict(run(3, 3), created_at="2026-10-10T10:03:00Z")],
        "failure": [dict(run(2, 2, conclusion="failure"), created_at="2026-10-10T10:02:00Z")],
        "in_progress": [
            dict(
                run(4, 4, status="in_progress", conclusion=None), created_at="2026-10-10T10:04:00Z"
            )
        ],
    }
    route = respx.get(RUNS).mock(
        side_effect=lambda request: httpx.Response(
            200, json={"workflow_runs": runs[request.url.params["status"]]}
        )
    )
    result = OccurrenceService(GitHubReader(Settings(cache_dir=cache_dir))).list()
    assert sorted(c.request.url.params["status"] for c in route.calls) == [
        "failure",
        "in_progress",
        "success",
    ]
    assert [i.occurrence_id for i in result.items] == [
        "PD-2026-0004",
        "PD-2026-0003",
        "PD-2026-0002",
    ]


@respx.mock
def test_missing_artifact_and_bad_report(token_settings: Settings) -> None:
    respx.get(RUNS).respond(200, json={"workflow_runs": [run(10, 2), run(11, 1)]})
    respx.get(f"{API}/repos/{REPO}/actions/runs/10/artifacts").respond(200, json={"artifacts": []})
    respx.get(f"{API}/repos/{REPO}/actions/runs/11/artifacts").respond(
        200, json={"artifacts": [{"id": 99, "name": "pipelinedoctor-diagnosis", "expired": False}]}
    )
    respx.get(f"{API}/repos/{REPO}/actions/artifacts/99/zip").respond(
        302, headers={"location": "https://blob.example.net/99"}
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("diagnosis.json", json.dumps({"not": "a report"}))
    respx.get("https://blob.example.net/99").respond(200, content=buffer.getvalue())
    result = OccurrenceService(GitHubReader(token_settings)).list()
    assert [i.status for i in result.items] == ["skipped", "skipped"]
    assert result.notice == "1 report(s) could not be retrieved from GitHub."


@respx.mock
def test_artifact_listing_failure_is_transient(token_settings: Settings) -> None:
    respx.get(RUNS).respond(200, json={"workflow_runs": [run(10, 2)]})
    respx.get(f"{API}/repos/{REPO}/actions/runs/10/artifacts").respond(500)
    detail = OccurrenceService(GitHubReader(token_settings)).get("PD-2026-0002")
    assert detail.status == "skipped"
    assert detail.notice and "GitHub API error" in detail.notice


@respx.mock
def test_doctor_workflow_missing(token_settings: Settings) -> None:
    respx.get(RUNS).respond(404, json={"message": "Not Found"})
    result = OccurrenceService(GitHubReader(token_settings)).list()
    assert result.items == []
    assert result.notice and "doctor.yml workflow was not found" in result.notice


@respx.mock
def test_rate_limited_without_cache(token_settings: Settings) -> None:
    respx.get(RUNS).respond(403, headers={"x-ratelimit-remaining": "0"})
    result = OccurrenceService(GitHubReader(token_settings)).list()
    assert result.items == [] and result.notice and "rate limit" in result.notice


def test_fixture_files_are_lf_and_valid() -> None:
    for path in BUILTIN_FIXTURES.rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path
