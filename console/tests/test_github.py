from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from pipelinedoctor_console import github as gh_module
from pipelinedoctor_console.github import GitHubError, GitHubReader
from pipelinedoctor_console.settings import Settings

from .conftest import API, REPO

RUNS = f"{API}/repos/{REPO}/actions/workflows/doctor.yml/runs"


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


@respx.mock
def test_sends_token_and_caches_within_ttl(token_settings: Settings) -> None:
    route = respx.get(RUNS).respond(200, json={"workflow_runs": []})
    gh = GitHubReader(token_settings)
    assert gh.get_json(f"/repos/{REPO}/actions/workflows/doctor.yml/runs").data == {
        "workflow_runs": []
    }
    gh.get_json(f"/repos/{REPO}/actions/workflows/doctor.yml/runs")
    assert route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer test-token-value"
    assert gh.stats["cache_hits"] == 1


@respx.mock
def test_no_auth_header_without_token(cache_dir: Path) -> None:
    route = respx.get(RUNS).respond(200, json={})
    GitHubReader(Settings(cache_dir=cache_dir)).get_json(
        f"/repos/{REPO}/actions/workflows/doctor.yml/runs"
    )
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
def test_etag_revalidation_uses_304(token_settings: Settings) -> None:
    route = respx.get(RUNS)
    route.side_effect = [
        httpx.Response(200, json={"n": 1}, headers={"etag": '"abc"'}),
        httpx.Response(304, headers={"etag": '"abc"'}),
    ]
    gh = GitHubReader(token_settings)
    path = f"/repos/{REPO}/actions/workflows/doctor.yml/runs"
    assert gh.get_json(path, ttl=0).data == {"n": 1}
    assert gh.get_json(path, ttl=0).data == {"n": 1}
    assert route.calls[1].request.headers["if-none-match"] == '"abc"'
    assert gh.stats["not_modified"] == 1


@respx.mock
@pytest.mark.parametrize(
    ("status", "headers", "kind"),
    [
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1792000000"}, "rate_limited"),
        (429, {"retry-after": "30"}, "rate_limited"),
        (403, {"retry-after": "60"}, "rate_limited"),
        (401, {}, "unauthorized"),
        (404, {}, "not_found"),
        (500, {}, "http"),
    ],
)
def test_error_kinds(
    token_settings: Settings, status: int, headers: dict[str, str], kind: str
) -> None:
    respx.get(RUNS).respond(status, headers=headers, json={"message": "x"})
    with pytest.raises(GitHubError) as info:
        GitHubReader(token_settings).get_json(f"/repos/{REPO}/actions/workflows/doctor.yml/runs")
    assert info.value.kind == kind
    assert info.value.notice()


def test_rate_limit_notice_mentions_reset() -> None:
    err = GitHubReader._error_for(
        httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"})
    )
    assert "00:00 UTC" in err.notice()


@respx.mock
def test_stale_cache_served_when_rate_limited(token_settings: Settings) -> None:
    route = respx.get(RUNS)
    route.side_effect = [
        httpx.Response(200, json={"n": 1}, headers={"x-ratelimit-remaining": "1"}),
        httpx.Response(403, headers={"x-ratelimit-remaining": "0"}),
    ]
    gh = GitHubReader(token_settings)
    path = f"/repos/{REPO}/actions/workflows/doctor.yml/runs"
    gh.get_json(path, ttl=0)
    stale = gh.get_json(path, ttl=0)
    assert stale.data == {"n": 1}
    assert stale.stale_because is not None and stale.stale_because.kind == "rate_limited"
    assert gh.rate_limit_remaining == 0


@respx.mock
def test_network_error(token_settings: Settings) -> None:
    respx.get(RUNS).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(GitHubError) as info:
        GitHubReader(token_settings).get_json(f"/repos/{REPO}/actions/workflows/doctor.yml/runs")
    assert info.value.kind == "network"


@respx.mock
def test_raw_text(token_settings: Settings) -> None:
    route = respx.get(f"{API}/repos/{REPO}/contents/ledger.jsonl").respond(200, text="a\nb\n")
    data = GitHubReader(token_settings).get_text(f"/repos/{REPO}/contents/ledger.jsonl").data
    assert data == "a\nb\n"
    assert route.calls[0].request.headers["accept"] == "application/vnd.github.raw+json"


@respx.mock
def test_artifact_redirect_drops_token_and_caches_on_disk(token_settings: Settings) -> None:
    blob = "https://blob.example.net/a.zip?sig=secret"
    api = respx.get(f"{API}/repos/{REPO}/actions/artifacts/7/zip").respond(
        302, headers={"location": blob}
    )
    content = make_zip({"diagnosis.json": '{"x": 1}', "other.txt": "ignored"})
    blob_route = respx.get(blob).respond(200, content=content)
    gh = GitHubReader(token_settings)
    files = gh.read_artifact_files(7, ("diagnosis.json", "excerpt.txt"))
    assert files == {"diagnosis.json": '{"x": 1}'}
    assert "authorization" not in blob_route.calls[0].request.headers
    # Second read comes from the on-disk cache: artifacts are immutable.
    fresh = GitHubReader(token_settings)
    assert fresh.read_artifact_files(7, ("diagnosis.json",)) == files
    assert api.call_count == 1
    assert json.loads((token_settings.cache_dir / "artifacts" / "7.json").read_text())


@respx.mock
def test_artifact_errors(token_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = GitHubReader(token_settings)
    respx.get(f"{API}/repos/{REPO}/actions/artifacts/1/zip").respond(302)
    with pytest.raises(GitHubError):
        gh.read_artifact_files(1, ("diagnosis.json",))

    respx.get(f"{API}/repos/{REPO}/actions/artifacts/2/zip").respond(
        302, headers={"location": "https://blob.example.net/2"}
    )
    respx.get("https://blob.example.net/2").respond(200, content=b"not a zip")
    with pytest.raises(GitHubError):
        gh.read_artifact_files(2, ("diagnosis.json",))

    respx.get(f"{API}/repos/{REPO}/actions/artifacts/3/zip").respond(
        302, headers={"location": "https://blob.example.net/3"}
    )
    respx.get("https://blob.example.net/3").respond(500)
    with pytest.raises(GitHubError):
        gh.read_artifact_files(3, ("diagnosis.json",))

    monkeypatch.setattr(gh_module, "MAX_MEMBER_BYTES", 4)
    respx.get(f"{API}/repos/{REPO}/actions/artifacts/4/zip").respond(
        302, headers={"location": "https://blob.example.net/4"}
    )
    respx.get("https://blob.example.net/4").respond(
        200, content=make_zip({"diagnosis.json": "much too long"})
    )
    assert gh.read_artifact_files(4, ("diagnosis.json",)) == {}


@respx.mock
def test_unwritable_cache_dir_is_not_fatal(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    settings = Settings(token="t", cache_dir=blocker)
    respx.get(f"{API}/repos/{REPO}/actions/artifacts/5/zip").respond(
        302, headers={"location": "https://blob.example.net/5"}
    )
    respx.get("https://blob.example.net/5").respond(200, content=make_zip({"diagnosis.json": "{}"}))
    gh = GitHubReader(settings)
    assert gh.read_artifact_files(5, ("diagnosis.json",)) == {"diagnosis.json": "{}"}
