"""Doctor workflow runs + their `pipelinedoctor-diagnosis` artifacts -> register rows, reports."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from pipelinedoctor_console.github import GitHubError, GitHubReader
from pipelinedoctor_console.models import (
    DiagnosisReport,
    OccurrenceDetail,
    OccurrenceList,
    OccurrenceRow,
    Status,
)

log = logging.getLogger(__name__)

ARTIFACT_NAME = "pipelinedoctor-diagnosis"
REPORT_FILE = "diagnosis.json"
EXCERPT_FILE = "excerpt.txt"
PENDING_STATES = {"queued", "in_progress", "waiting", "requested", "pending"}
OCCURRENCE_ID = re.compile(r"^PD-(\d{4})-(\d{1,9})$")
EXCERPT_LINE = re.compile(r"^\s*(\d{1,7})(?:\s*[:|]\s?|\t| )(.*)$")
EXCERPT_CONTEXT = 4
UNAUTH_NOTICE = "Running without a GitHub token — data may be stale."
NO_TOKEN_REPORTS = "Reports are stored as workflow artifacts, which GitHub only serves to a token."


class OccurrenceNotFound(Exception):
    pass


@dataclass
class _Resolved:
    status: Status
    report: DiagnosisReport | None = None
    excerpt: dict[int, str] | None = None
    error: str | None = None


def occurrence_id_for(run: dict[str, Any]) -> str:
    created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
    return f"PD-{created.year}-{int(run['run_number']):04d}"


def workflow_from_title(title: str) -> str | None:
    """doctor.yml sets run-name "Doctor: <workflow> #<n>"; recover the workflow for pending rows."""
    match = re.match(r"^Doctor: (.+?)(?: #\d+)?$", title or "")
    return match.group(1) if match else None


def parse_excerpt(text: str, wanted: set[int]) -> dict[int, str]:
    """Keep only the excerpt lines near cited evidence. The excerpt format is lenient on purpose:
    `812: text`, `812 | text` and `812<TAB>text` all parse."""
    near = {n + d for n in wanted for d in range(-EXCERPT_CONTEXT, EXCERPT_CONTEXT + 1)}
    lines: dict[int, str] = {}
    for raw in text.splitlines():
        match = EXCERPT_LINE.match(raw)
        if match and int(match.group(1)) in near:
            lines[int(match.group(1))] = match.group(2)[:2000]
    return lines


class OccurrenceService:
    def __init__(self, gh: GitHubReader) -> None:
        self.gh = gh
        self._resolved: dict[int, _Resolved] = {}

    def _runs(self, notices: list[str]) -> list[dict[str, Any]]:
        # Doctor fires on EVERY completed run of ten workflows; when the watched run succeeded
        # its job is skipped (conclusion "skipped"). Those are not occurrences, and they would
        # fill a plain "last 100 runs" page within days. So ask the API for the interesting
        # states only: diagnosed (success), Doctor itself failed (failure), still running.
        workflow = self.gh.settings.doctor_workflow
        path = f"{self.gh.repo_path}/actions/workflows/{workflow}/runs"
        by_id: dict[int, dict[str, Any]] = {}
        stale: set[str] = set()
        for status, per_page in (("success", 100), ("failure", 30), ("in_progress", 20)):
            fetched = self.gh.get_json(path, {"status": status, "per_page": per_page})
            if fetched.stale_because:
                stale.add(f"{fetched.stale_because.notice()} Showing cached data.")
            data = fetched.data if isinstance(fetched.data, dict) else {}
            for r in data.get("workflow_runs", []):
                by_id[int(r["id"])] = r
        notices.extend(sorted(stale))
        runs = [r for r in by_id.values() if r.get("conclusion") != "skipped"]
        return sorted(runs, key=lambda r: str(r.get("created_at", "")), reverse=True)

    def _resolve(self, run: dict[str, Any]) -> _Resolved:
        if run.get("status") in PENDING_STATES:
            return _Resolved("pending")
        run_id = int(run["id"])
        if run_id in self._resolved:
            return self._resolved[run_id]
        if not self.gh.settings.authenticated:
            # Without a token we cannot download artifacts; the run conclusion is the best signal.
            status: Status = "diagnosed" if run.get("conclusion") == "success" else "skipped"
            return _Resolved(status, error=NO_TOKEN_REPORTS)
        try:
            resolved = self._download(run_id)
        except GitHubError as exc:
            # Transient: do not memoise, try again on the next request.
            return _Resolved("skipped", error=exc.notice())
        self._resolved[run_id] = resolved
        return resolved

    def _download(self, run_id: int) -> _Resolved:
        listing = self.gh.get_json(
            f"{self.gh.repo_path}/actions/runs/{run_id}/artifacts", {"name": ARTIFACT_NAME}
        ).data
        artifacts = [
            a
            for a in (listing.get("artifacts", []) if isinstance(listing, dict) else [])
            if a.get("name") == ARTIFACT_NAME and not a.get("expired")
        ]
        if not artifacts:
            return _Resolved("skipped")
        files = self.gh.read_artifact_files(int(artifacts[0]["id"]), (REPORT_FILE, EXCERPT_FILE))
        try:
            report = DiagnosisReport.model_validate(json.loads(files.get(REPORT_FILE, "")))
        except (ValueError, ValidationError):
            log.warning("artifact of run %s has no valid %s", run_id, REPORT_FILE)
            return _Resolved("skipped", error="The report artifact could not be read.")
        excerpt = parse_excerpt(files.get(EXCERPT_FILE, ""), {e.line for e in report.evidence})
        return _Resolved("diagnosed", report, excerpt)

    def list(self, limit: int = 50) -> OccurrenceList:
        notices: list[str] = []
        if not self.gh.settings.authenticated:
            notices.append(UNAUTH_NOTICE)
        try:
            runs = self._runs(notices)[: max(1, min(limit, 100))]
        except GitHubError as exc:
            if exc.kind == "not_found":
                # Normal before stage 08: the Doctor workflow is not on the default branch yet.
                notices.append(
                    f"The {self.gh.settings.doctor_workflow} workflow was not found in "
                    f"{self.gh.settings.repo}; no investigations have run yet."
                )
            else:
                notices.append(exc.notice())
            return OccurrenceList(
                items=[], fetched_at=self.gh.settings.now(), notice=" ".join(notices)
            )
        with ThreadPoolExecutor(max_workers=8) as pool:
            resolved = list(pool.map(self._resolve, runs))
        items = [self._row(run, res) for run, res in zip(runs, resolved, strict=True)]
        failed = sum(1 for r in resolved if r.error and r.error != NO_TOKEN_REPORTS)
        if failed:
            notices.append(f"{failed} report(s) could not be retrieved from GitHub.")
        if any(r.error == NO_TOKEN_REPORTS for r in resolved):
            notices.append(NO_TOKEN_REPORTS)
        return OccurrenceList(
            items=items, fetched_at=self.gh.settings.now(), notice=" ".join(notices) or None
        )

    def _row(self, run: dict[str, Any], res: _Resolved) -> OccurrenceRow:
        report = res.report
        if report is None:
            return OccurrenceRow(
                occurrence_id=occurrence_id_for(run),
                created_at=run["created_at"],
                repo=self.gh.settings.repo,
                workflow=workflow_from_title(str(run.get("display_title", ""))),
                job=None,
                failed_step=None,
                category=None,
                confidence=None,
                confidence_grade=None,
                status=res.status,
                run_url=None,
                pr_number=None,
                doctor_run_id=int(run["id"]),
            )
        return OccurrenceRow(
            occurrence_id=report.occurrence_id,
            created_at=report.created_at,
            repo=report.repo,
            workflow=report.run.workflow_name or None,
            # The engine writes failed_job {id: 0, name: "(no jobs)"} when the run never started.
            job=report.failed_job.name if report.failed_job and report.failed_job.id else None,
            failed_step=report.failed_step.name if report.failed_step else None,
            category=report.category,
            confidence=report.confidence,
            confidence_grade=report.confidence_grade,
            status=res.status,
            run_url=report.run.html_url,
            pr_number=report.pull_request.number if report.pull_request else None,
            doctor_run_id=int(run["id"]),
        )

    def get(self, occurrence_id: str) -> OccurrenceDetail:
        match = OCCURRENCE_ID.match(occurrence_id)
        if not match:
            raise OccurrenceNotFound(occurrence_id)
        year, number = int(match.group(1)), int(match.group(2))
        notices: list[str] = []
        runs = self._runs(notices)
        run = next(
            (
                r
                for r in runs
                if int(r.get("run_number", -1)) == number and str(r["created_at"])[:4] == str(year)
            ),
            None,
        )
        if run is None:
            raise OccurrenceNotFound(occurrence_id)
        res = self._resolve(run)
        if res.error:
            notices.append(res.error)
        return OccurrenceDetail(
            status=res.status,
            report=res.report,
            doctor_run_url=run.get("html_url"),
            excerpt=res.excerpt or {},
            notice=" ".join(notices) or None,
        )
