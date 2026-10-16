"""DORA metrics from GitHub Deployments (ARCHITECTURE.md section 13.4).

Everything above `DoraService` is pure: lists in, numbers out, so each rule is unit-testable.
The deploy scripts annotate every production deployment with a status whose description reads
`pd:kind=deploy;version=0.3.0;color=green;outcome=auto_rolled_back`; that status is the source of
truth for what actually happened, because GitHub's own success/failure state cannot tell
"failed before the switch" from "switched, failed smoke, rolled back".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from pipelinedoctor_console.github import GitHubError, GitHubReader
from pipelinedoctor_console.models import DoraMetrics

log = logging.getLogger(__name__)

FAILED_OUTCOMES = {"auto_rolled_back", "failed_before_switch"}
RESTORING_OUTCOMES = {"deployed", "manual_rollback"}
CommitDates = Callable[[str | None, str], list[datetime]]


@dataclass(frozen=True)
class Record:
    deployment_id: int
    sha: str
    kind: str
    outcome: str
    at: datetime
    version: str | None = None


def _ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_description(description: str | None) -> dict[str, str] | None:
    if not description or not description.startswith("pd:"):
        return None
    fields: dict[str, str] = {}
    for part in description[3:].split(";"):
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def record_from(deployment: dict[str, Any], statuses: Sequence[dict[str, Any]]) -> Record | None:
    """Turn one deployment + its statuses (GitHub returns newest first) into a Record.
    Returns None while the deployment is still queued or in progress."""
    ordered = sorted(statuses, key=lambda s: _ts(s["created_at"]), reverse=True)
    for status in ordered:
        fields = parse_description(status.get("description"))
        if fields and fields.get("outcome"):
            return Record(
                deployment_id=int(deployment["id"]),
                sha=str(deployment.get("sha", "")),
                kind=fields.get("kind", "deploy"),
                outcome=fields["outcome"],
                at=_ts(status["created_at"]),
                version=fields.get("version"),
            )
    # No annotation (e.g. the annotate step never ran): fall back to the latest terminal state.
    # "inactive" is skipped on purpose: GitHub marks older deployments inactive when a newer
    # one succeeds, which says nothing about how they went.
    for status in ordered:
        state = status.get("state")
        if state in {"success", "failure", "error"}:
            return Record(
                deployment_id=int(deployment["id"]),
                sha=str(deployment.get("sha", "")),
                kind="deploy",
                outcome="deployed" if state == "success" else "failed_before_switch",
                at=_ts(status["created_at"]),
            )
    return None


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def is_change_failure(record: Record, later: Sequence[Record]) -> bool:
    """A deploy failed if it rolled back, or a manual rollback came before the next deploy."""
    if record.kind != "deploy":
        return False
    if record.outcome in FAILED_OUTCOMES:
        return True
    for nxt in later:
        if nxt.kind == "manual_rollback":
            return True
        if nxt.kind == "deploy":
            return False
    return False


def deployment_frequency(deploys: Sequence[Record], days: int) -> float | None:
    if days <= 0:
        return None
    return len([r for r in deploys if r.outcome == "deployed"]) / (days / 7)


def lead_time_hours(
    in_window: Sequence[Record], history: Sequence[Record], commit_dates: CommitDates
) -> float | None:
    """Median hours from commit to the deploy that shipped it."""
    values: list[float] = []
    successful = [r for r in history if r.kind == "deploy" and r.outcome == "deployed"]
    for record in in_window:
        if record.kind != "deploy" or record.outcome != "deployed":
            continue
        previous = [r for r in successful if r.at < record.at]
        base = previous[-1].sha if previous else None
        if base == record.sha:
            continue  # a redeploy of the same commit ships no new changes
        for committed in commit_dates(base, record.sha):
            if committed <= record.at:
                values.append(_hours(record.at - committed))
    return median(values) if values else None


def restore_hours(failure: Record, later: Sequence[Record]) -> float | None:
    if failure.outcome == "failed_before_switch":
        return 0.0  # production never served the bad version
    for nxt in later:
        if nxt.outcome in RESTORING_OUTCOMES:
            return _hours(nxt.at - failure.at)
    return None


def compute(
    records: Sequence[Record],
    now: datetime,
    days: int,
    commit_dates: CommitDates,
    environment: str = "production",
) -> DoraMetrics:
    history = sorted(records, key=lambda r: r.at)
    start = now - timedelta(days=days)
    deploys = [r for r in history if r.kind == "deploy" and start <= r.at <= now]
    notes: list[str] = []

    failures = [r for r in deploys if is_change_failure(r, [x for x in history if x.at > r.at])]
    restore_values: list[float] = []
    unrestored = 0
    for failure in failures:
        value = restore_hours(failure, [x for x in history if x.at > failure.at])
        if value is None:
            unrestored += 1
        else:
            restore_values.append(value)

    if not deploys:
        notes.append(f"No {environment} deployments in the last {days} days.")
    if unrestored:
        notes.append(
            f"{unrestored} failed deployment(s) not yet restored, excluded from restore time."
        )
    if any(f.outcome == "failed_before_switch" for f in failures):
        notes.append("Failures caught before the traffic switch count with a restore time of 0 h.")

    return DoraMetrics(
        window_days=days,
        environment=environment,
        deployments=len(deploys),
        deployment_frequency_per_week=_round(
            deployment_frequency(deploys, days) if deploys else None
        ),
        lead_time_hours_median=_round(lead_time_hours(deploys, history, commit_dates)),
        change_failure_rate=_round(len(failures) / len(deploys)) if deploys else None,
        time_to_restore_hours_median=_round(median(restore_values)) if restore_values else None,
        computed_at=now,
        notes=notes,
    )


class DoraService:
    def __init__(self, gh: GitHubReader) -> None:
        self.gh = gh

    def _statuses(self, deployment: dict[str, Any]) -> list[dict[str, Any]]:
        data = self.gh.get_json(
            f"{self.gh.repo_path}/deployments/{deployment['id']}/statuses", {"per_page": 30}
        ).data
        return list(data) if isinstance(data, list) else []

    def _commit_dates(self, base: str | None, head: str) -> list[datetime]:
        try:
            if base is None:
                data = self.gh.get_json(f"{self.gh.repo_path}/commits/{head}", ttl=86400).data
                return [_ts(data["commit"]["committer"]["date"])]
            data = self.gh.get_json(f"{self.gh.repo_path}/compare/{base}...{head}", ttl=86400).data
            return [_ts(c["commit"]["committer"]["date"]) for c in data.get("commits", [])]
        except (GitHubError, KeyError, TypeError, ValueError):
            log.warning("could not read commits %s...%s", base, head)
            return []

    def metrics(self, days: int = 30) -> DoraMetrics:
        settings = self.gh.settings
        now = settings.now()
        environment = settings.prod_environment
        try:
            fetched = self.gh.get_json(
                f"{self.gh.repo_path}/deployments",
                {"environment": environment, "per_page": 100},
            )
            deployments = list(fetched.data) if isinstance(fetched.data, list) else []
            with ThreadPoolExecutor(max_workers=8) as pool:
                statuses = list(pool.map(self._statuses, deployments))
        except GitHubError as exc:
            empty = compute([], now, days, lambda _b, _h: [], environment)
            empty.notes = []
            empty.notice = exc.notice()
            return empty
        records = [
            r
            for d, s in zip(deployments, statuses, strict=True)
            if (r := record_from(d, s)) is not None
        ]
        result = compute(records, now, days, self._commit_dates, environment)
        if fetched.stale_because:
            result.notice = f"{fetched.stale_because.notice()} Showing cached data."
        elif not settings.authenticated:
            result.notice = "Running without a GitHub token — data may be stale."
        return result
