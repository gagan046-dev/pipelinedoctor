"""Idempotent PR comments: one comment per PR, found again through a hidden HTML marker.

Each failed workflow owns one section, so a CI failure and a Demo CI failure on the same PR share
the comment without overwriting each other. Two doctor runs can race; after writing we re-read
and retry until our section is really there.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

REPORT_MARKER = "<!-- pipelinedoctor:report v1 -->"
EVALS_MARKER = "<!-- pipelinedoctor:evals v1 -->"
SECTION_END = "<!-- pd:end -->"
REPORT_HEADER = "## PipelineDoctor: CI failure diagnosis"
BOT_LOGIN = "github-actions[bot]"
MAX_ATTEMPTS = 3

_SECTION_RE = re.compile(
    r"<!-- pd:section workflow=(?P<key>[^\s>]+) -->\n.*?\n<!-- pd:end -->", re.DOTALL
)

log = logging.getLogger(__name__)


class CommentAPI(Protocol):
    def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def get_comment(self, repo: str, comment_id: int) -> dict[str, Any]: ...
    def create_comment(self, repo: str, number: int, body: str) -> dict[str, Any]: ...
    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]: ...
    def delete_comment(self, repo: str, comment_id: int) -> None: ...
    def own_login(self) -> str | None: ...


def section_start(workflow_key: str) -> str:
    return f"<!-- pd:section workflow={workflow_key} -->"


def wrap_section(workflow_key: str, markdown: str) -> str:
    return f"{section_start(workflow_key)}\n{markdown.strip()}\n{SECTION_END}"


def parse_sections(body: str) -> dict[str, str]:
    """Ordered mapping workflow key -> full section text (markers included)."""
    return {m.group("key"): m.group(0) for m in _SECTION_RE.finditer(body)}


def build_body(sections: dict[str, str]) -> str:
    return "\n\n".join([REPORT_MARKER, REPORT_HEADER, *sections.values()]) + "\n"


def merge_section(body: str | None, workflow_key: str, section: str) -> str:
    sections = parse_sections(body or "")
    sections[workflow_key] = section  # replaces in place, or appends at the end
    return build_body(sections)


def _is_candidate(comment: dict[str, Any], marker: str, own_login: str | None) -> bool:
    """Only our own comments count. Anyone can paste the marker into a comment of their own."""
    user = comment.get("user") or {}
    login = user.get("login")
    ours = (user.get("type") == "Bot" and login == BOT_LOGIN) or (
        own_login is not None and login == own_login
    )
    return ours and str(comment.get("body", "")).startswith(marker)


def find_marker_comments(
    api: CommentAPI, repo: str, number: int, marker: str
) -> list[dict[str, Any]]:
    own = api.own_login()
    found = [c for c in api.list_issue_comments(repo, number) if _is_candidate(c, marker, own)]
    return sorted(found, key=lambda c: int(c["id"]))


def upsert_report_section(
    api: CommentAPI,
    repo: str,
    number: int,
    workflow_key: str,
    section_markdown: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Create or update this workflow's section. Returns the comment URL."""
    section = (
        section_markdown
        if section_markdown.startswith(section_start(workflow_key))
        else wrap_section(workflow_key, section_markdown)
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        existing = find_marker_comments(api, repo, number, REPORT_MARKER)
        if existing:
            target = existing[0]
            body = merge_section(str(target.get("body", "")), workflow_key, section)
            api.update_comment(repo, int(target["id"]), body)
        else:
            target = api.create_comment(repo, number, merge_section(None, workflow_key, section))
            _collapse_duplicates(api, repo, number, workflow_key, section, int(target["id"]))
            existing = find_marker_comments(api, repo, number, REPORT_MARKER)
            target = existing[0] if existing else target

        current = api.get_comment(repo, int(target["id"]))
        if parse_sections(str(current.get("body", ""))).get(workflow_key) == section:
            url = current.get("html_url") or target.get("html_url")
            return str(url) if url else None
        log.info("comment section missing after write (attempt %d), retrying", attempt)
        sleep(random.uniform(0.5, 2.0) * attempt)
    log.warning("could not verify PR comment section after %d attempts", MAX_ATTEMPTS)
    return None


def _collapse_duplicates(
    api: CommentAPI, repo: str, number: int, workflow_key: str, section: str, created_id: int
) -> None:
    """Two runs that both saw "no comment" both POST. Keep the oldest, move our section there."""
    comments = find_marker_comments(api, repo, number, REPORT_MARKER)
    if len(comments) < 2 or int(comments[0]["id"]) == created_id:
        return
    oldest = comments[0]
    api.update_comment(
        repo, int(oldest["id"]), merge_section(str(oldest.get("body", "")), workflow_key, section)
    )
    api.delete_comment(repo, created_id)


def upsert_whole_comment(
    api: CommentAPI, repo: str, number: int, marker: str, body: str
) -> str | None:
    """Replace the whole body of the marker comment (used for the eval score table)."""
    full = body if body.startswith(marker) else f"{marker}\n{body}"
    existing = find_marker_comments(api, repo, number, marker)
    if existing:
        result = api.update_comment(repo, int(existing[0]["id"]), full)
    else:
        result = api.create_comment(repo, number, full)
    url = result.get("html_url")
    return str(url) if url else None
