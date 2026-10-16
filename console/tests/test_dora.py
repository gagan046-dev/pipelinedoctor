from __future__ import annotations

from datetime import UTC, datetime, timedelta

import respx

from pipelinedoctor_console.dora import (
    DoraService,
    Record,
    compute,
    is_change_failure,
    parse_description,
    record_from,
)
from pipelinedoctor_console.github import GitHubReader
from pipelinedoctor_console.settings import Settings

from .conftest import API, REPO

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def at(hours_ago: float) -> datetime:
    return NOW - timedelta(hours=hours_ago)


def rec(n: int, hours_ago: float, outcome: str = "deployed", kind: str = "deploy") -> Record:
    return Record(deployment_id=n, sha=f"sha{n}", kind=kind, outcome=outcome, at=at(hours_ago))


def no_commits(base: str | None, head: str) -> list[datetime]:
    return []


def status(state: str, hours_ago: float, description: str = "") -> dict[str, object]:
    return {
        "state": state,
        "description": description,
        "created_at": at(hours_ago).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def test_parse_description() -> None:
    assert parse_description("pd:kind=deploy;version=1.0.0;color=blue;outcome=deployed") == {
        "kind": "deploy",
        "version": "1.0.0",
        "color": "blue",
        "outcome": "deployed",
    }
    assert parse_description("Deployment finished") is None
    assert parse_description(None) is None


def test_record_from_prefers_annotation() -> None:
    statuses = [
        status("inactive", 1),
        status("failure", 5, "pd:kind=deploy;version=2;color=green;outcome=auto_rolled_back"),
        status("failure", 5.1),
        status("in_progress", 6),
    ]
    r = record_from({"id": 1, "sha": "abc"}, statuses)
    assert r is not None
    assert (r.kind, r.outcome, r.version, r.sha) == ("deploy", "auto_rolled_back", "2", "abc")
    assert r.at == at(5)


def test_record_from_fallbacks() -> None:
    ok = record_from({"id": 1, "sha": "a"}, [status("inactive", 1), status("success", 3)])
    assert ok is not None and ok.outcome == "deployed" and ok.at == at(3)
    err = record_from({"id": 2, "sha": "b"}, [status("error", 2)])
    assert err is not None and err.outcome == "failed_before_switch"
    assert record_from({"id": 3, "sha": "c"}, [status("in_progress", 1)]) is None
    assert record_from({"id": 4, "sha": "d"}, []) is None


def test_change_failure_rules() -> None:
    deploy = rec(1, 10)
    assert not is_change_failure(deploy, [rec(2, 5)])
    assert is_change_failure(deploy, [rec(2, 5, "manual_rollback", "manual_rollback")])
    assert not is_change_failure(
        deploy, [rec(2, 6), rec(3, 5, "manual_rollback", "manual_rollback")]
    )
    assert is_change_failure(rec(4, 3, "failed_before_switch"), [])
    assert not is_change_failure(rec(5, 3, "manual_rollback", "manual_rollback"), [])


def test_compute_all_metrics() -> None:
    records = [
        rec(1, 24 * 40),  # before the window: only the base for lead time
        rec(2, 24 * 20),
        rec(3, 24 * 10, "auto_rolled_back"),
        rec(4, 24 * 10 - 2),  # restores 2 h after the failure
        rec(5, 24 * 5),
        rec(6, 24 * 5 - 6, "manual_rollback", "manual_rollback"),  # deploy 5 was bad
        rec(7, 24 * 2, "failed_before_switch"),
        rec(8, 24 * 1),
    ]
    commits = {
        "sha1": [],
        "sha2": [at(24 * 20 + 4)],
        "sha4": [at(24 * 10 - 2 + 1)],
        "sha5": [at(24 * 5 + 10), at(24 * 5 + 2)],
        "sha8": [at(24 * 1 + 8)],
    }

    def dates(base: str | None, head: str) -> list[datetime]:
        return commits.get(head, [])

    m = compute(records, NOW, 30, dates)
    assert m.deployments == 6
    assert m.deployment_frequency_per_week == round(4 / (30 / 7), 2)
    assert m.lead_time_hours_median == 4.0  # values 4, 1, 10, 2, 8
    assert m.change_failure_rate == 0.5  # 3 of 6
    # restore times: auto rollback 2 h, manual rollback 6 h, failed_before_switch 0 h
    assert m.time_to_restore_hours_median == 2.0
    assert any("0 h" in note for note in m.notes)


def test_compute_empty_and_unrestored() -> None:
    empty = compute([], NOW, 30, no_commits)
    assert empty.deployments == 0
    assert empty.deployment_frequency_per_week is None
    assert empty.change_failure_rate is None
    assert empty.lead_time_hours_median is None
    assert empty.notes == ["No production deployments in the last 30 days."]

    unrestored = compute([rec(1, 5, "auto_rolled_back")], NOW, 30, no_commits)
    assert unrestored.change_failure_rate == 1.0
    assert unrestored.time_to_restore_hours_median is None
    assert "not yet restored" in unrestored.notes[0]


def test_redeploy_of_same_sha_has_no_lead_time() -> None:
    a = Record(1, "same", "deploy", "deployed", at(10))
    b = Record(2, "same", "deploy", "deployed", at(5))
    calls: list[tuple[str | None, str]] = []

    def dates(base: str | None, head: str) -> list[datetime]:
        calls.append((base, head))
        return [at(12)]

    m = compute([a, b], NOW, 30, dates)
    assert calls == [(None, "same")]
    assert m.lead_time_hours_median == 2.0


def test_fixture_service(fixture_gh: GitHubReader) -> None:
    m = DoraService(fixture_gh).metrics(30)
    assert m.deployments == 10
    assert m.deployment_frequency_per_week == 1.87
    assert m.change_failure_rate == 0.3
    assert m.time_to_restore_hours_median is not None
    assert m.lead_time_hours_median is not None
    assert m.computed_at == NOW


@respx.mock
def test_service_errors(token_settings: Settings) -> None:
    respx.get(f"{API}/repos/{REPO}/deployments").respond(
        403, headers={"x-ratelimit-remaining": "0"}
    )
    m = DoraService(GitHubReader(token_settings)).metrics()
    assert m.deployments == 0 and m.notice and "rate limit" in m.notice


@respx.mock
def test_service_commit_lookup_failure_is_tolerated(token_settings: Settings) -> None:
    respx.get(f"{API}/repos/{REPO}/deployments").respond(200, json=[{"id": 1, "sha": "abc"}])
    respx.get(f"{API}/repos/{REPO}/deployments/1/statuses").respond(
        200, json=[{"state": "success", "description": "", "created_at": "2026-10-15T10:00:00Z"}]
    )
    respx.get(f"{API}/repos/{REPO}/commits/abc").respond(500)
    settings = Settings(token="t", cache_dir=token_settings.cache_dir)
    m = DoraService(GitHubReader(settings)).metrics(3650)
    assert m.deployments == 1
    assert m.lead_time_hours_median is None


@respx.mock
def test_service_unauthenticated_notice(cache_dir: object) -> None:
    respx.get(f"{API}/repos/{REPO}/deployments").respond(200, json=[])
    m = DoraService(GitHubReader(Settings())).metrics()
    assert m.notice and "without a GitHub token" in m.notice
