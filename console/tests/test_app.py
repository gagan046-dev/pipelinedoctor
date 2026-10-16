from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipelinedoctor_console.app import SECURITY_HEADERS, create_app
from pipelinedoctor_console.settings import Settings


def test_pages_render(client: TestClient) -> None:
    for path, heading in [
        ("/", "Occurrence register"),
        ("/evals", "Eval ledger"),
        ("/occurrences/PD-2026-0051", "Retrieving the report"),
    ]:
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert heading in r.text
        assert "Fixture mode" in r.text
        assert "/static/css/console.css" in r.text


def test_security_headers_everywhere(client: TestClient) -> None:
    for path in ["/", "/api/occurrences", "/healthz", "/static/css/console.css", "/nope"]:
        r = client.get(path)
        for name, value in SECURITY_HEADERS.items():
            assert r.headers[name] == value, (path, name)
    assert "frame-ancestors 'none'" in SECURITY_HEADERS["Content-Security-Policy"]


def test_static_caching(client: TestClient) -> None:
    css = client.get("/static/css/console.css")
    assert css.headers["cache-control"] == "public, max-age=3600"
    assert client.get("/api/evals").headers["cache-control"] == "no-store"


def test_occurrence_id_is_not_reflected(client: TestClient) -> None:
    for payload, status in [
        ("<img src=x onerror=alert(1)>", 200),
        ("<script>alert(1)</script>", 404),
    ]:
        r = client.get("/occurrences/" + payload)
        assert r.status_code == status
        assert "alert(1)" not in r.text


def test_api_shapes(client: TestClient) -> None:
    occ = client.get("/api/occurrences?limit=5").json()
    assert set(occ) == {"items", "fetched_at", "notice"}
    assert set(occ["items"][0]) == {
        "occurrence_id",
        "created_at",
        "repo",
        "workflow",
        "job",
        "failed_step",
        "category",
        "confidence",
        "confidence_grade",
        "status",
        "run_url",
        "pr_number",
        "doctor_run_id",
    }
    detail = client.get("/api/occurrences/PD-2026-0051").json()
    assert detail["status"] == "diagnosed"
    assert detail["report"]["occurrence_id"] == "PD-2026-0051"
    assert detail["doctor_run_url"].startswith("https://github.com/")
    assert client.get("/api/occurrences/PD-2026-0999").status_code == 404
    evals = client.get("/api/evals").json()
    assert len(evals["entries"]) == 14
    dora = client.get("/api/dora?days=30").json()
    assert dora["window_days"] == 30 and "deployment_frequency_per_week" in dora
    assert client.get("/api/dora?days=0").status_code == 422
    assert client.get("/api/occurrences?limit=1000").status_code == 422


def test_timestamps_are_utc_z(client: TestClient) -> None:
    dora = client.get("/api/dora").json()
    assert dora["computed_at"].endswith("Z")


def test_health_version_metrics(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    v = client.get("/version").json()
    assert v["color"] == "blue" and v["version"] and v["started_at"].endswith("Z")
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["status"] == "ok"
    client.get("/api/occurrences")
    metrics = client.get("/metrics")
    assert metrics.headers["content-type"].startswith("text/plain")
    assert 'pd_console_info{version="' in metrics.text
    assert "pd_console_github_requests_total" in metrics.text


def test_readyz_reports_bad_settings(tmp_path: Path) -> None:
    settings = Settings(repo="not-a-repo", fixtures_dir=tmp_path, cache_dir=tmp_path)
    with TestClient(create_app(settings)) as c:
        r = c.get("/readyz")
        assert r.status_code == 503
        assert "PD_CONSOLE_REPO" in r.json()["reason"]


def test_readyz_probe_failure(tmp_path: Path) -> None:
    empty = tmp_path / "fixtures"
    empty.mkdir()
    settings = Settings(repo="a/b", fixtures_dir=empty, cache_dir=tmp_path)
    # The fixture transport answers /repos/a/b, so point the probe at a repo path it rejects.
    with TestClient(create_app(settings)) as c:
        c.app.state.gh.settings = Settings(repo="a/b/c", fixtures_dir=empty)  # type: ignore[attr-defined]
        r = c.get("/readyz")
        assert r.status_code == 503
        assert "github probe failed" in r.json()["reason"]


def test_not_found_page(client: TestClient) -> None:
    r = client.get("/no/such/page")
    assert r.status_code == 404
    assert "Not on the register" in r.text
    assert client.get("/api/nothing").json() == {"detail": "Not Found"}


def test_no_docs_endpoints(client: TestClient) -> None:
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_readyz_probes_on_a_freshly_booted_host(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # time.monotonic() counts from boot: a CI VM can be younger than the probe TTL.
    monkeypatch.setattr("pipelinedoctor_console.app.time.monotonic", lambda: 5.0)
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
