"""Eval ledger: `ledger.jsonl` on the `eval-history` branch, one LedgerEntry per nightly run."""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from pipelinedoctor_console.github import GitHubError, GitHubReader
from pipelinedoctor_console.models import EvalList, LedgerEntry

log = logging.getLogger(__name__)

LEDGER_FILE = "ledger.jsonl"
EMPTY_NOTICE = "No eval runs recorded yet."


def parse_ledger(text: str) -> tuple[list[LedgerEntry], int]:
    """Return entries (oldest first) and the number of unreadable lines."""
    entries: list[LedgerEntry] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(LedgerEntry.model_validate(json.loads(line)))
        except (ValueError, ValidationError):
            bad += 1
    entries.sort(key=lambda e: e.recorded_at)
    return entries, bad


class EvalService:
    def __init__(self, gh: GitHubReader) -> None:
        self.gh = gh

    def list(self) -> EvalList:
        now = self.gh.settings.now()
        notices: list[str] = []
        try:
            fetched = self.gh.get_text(
                f"{self.gh.repo_path}/contents/{LEDGER_FILE}",
                {"ref": self.gh.settings.eval_history_branch},
            )
        except GitHubError as exc:
            if exc.kind == "not_found":
                return EvalList(entries=[], fetched_at=now, notice=EMPTY_NOTICE)
            return EvalList(entries=[], fetched_at=now, notice=exc.notice())
        if fetched.stale_because:
            notices.append(f"{fetched.stale_because.notice()} Showing cached data.")
        entries, bad = parse_ledger(str(fetched.data))
        if bad:
            log.warning("%d unreadable ledger lines", bad)
            notices.append(f"{bad} ledger line(s) could not be read and are not shown.")
        if not entries:
            notices.append(EMPTY_NOTICE)
        return EvalList(entries=entries, fetched_at=now, notice=" ".join(notices) or None)
