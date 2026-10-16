from __future__ import annotations

from typing import Any

from fakes import FakeCommentAPI
from helpers import load_fixture
from pipelinedoctor.github.comments import (
    EVALS_MARKER,
    REPORT_MARKER,
    merge_section,
    parse_sections,
    upsert_report_section,
    upsert_whole_comment,
    wrap_section,
)

REPO = "o/r"


def no_sleep(_: float) -> None:
    pass


def test_creates_comment_when_none_exists() -> None:
    api = FakeCommentAPI()
    url = upsert_report_section(api, REPO, 1, "ci.yml", "### TEST", sleep=no_sleep)
    assert len(api.comments) == 1
    body = api.comments[0]["body"]
    assert body.startswith(REPORT_MARKER)
    assert parse_sections(body)["ci.yml"] == wrap_section("ci.yml", "### TEST")
    assert url and url.endswith(str(api.comments[0]["id"]))


def test_updates_own_section_and_keeps_others() -> None:
    api = FakeCommentAPI(comments=load_fixture("comments.json"))
    upsert_report_section(api, REPO, 12, "demo-ci.yml", "### DEMO first", sleep=no_sleep)
    upsert_report_section(api, REPO, 12, "demo-ci.yml", "### DEMO second", sleep=no_sleep)
    bot_comment = next(c for c in api.comments if c["id"] == 3401000002)
    sections = parse_sections(bot_comment["body"])
    assert list(sections) == ["ci.yml", "demo-ci.yml"]
    assert "old ci section" in sections["ci.yml"]
    assert "DEMO second" in sections["demo-ci.yml"] and "DEMO first" not in bot_comment["body"]
    human = next(c for c in api.comments if c["id"] == 3401000001)
    assert "pd:section" not in human["body"], "a human comment with the marker is never edited"
    assert len(api.comments) == 3


def test_retries_when_a_racing_writer_overwrites_us() -> None:
    api = FakeCommentAPI(comments=load_fixture("comments.json"), drop_next_updates=1)
    sleeps: list[float] = []
    url = upsert_report_section(api, REPO, 12, "demo-ci.yml", "### DEMO", sleep=sleeps.append)
    assert url is not None and len(sleeps) == 1
    assert "demo-ci.yml" in parse_sections(api.comments[1]["body"])


def test_gives_up_after_three_attempts() -> None:
    api = FakeCommentAPI(comments=load_fixture("comments.json"), drop_next_updates=5)
    assert upsert_report_section(api, REPO, 12, "demo-ci.yml", "### X", sleep=no_sleep) is None


def test_duplicate_comments_from_a_create_race_are_collapsed() -> None:
    api = FakeCommentAPI()
    older = api.create_comment(REPO, 1, merge_section(None, "ci.yml", wrap_section("ci.yml", "A")))

    class RacingAPI(FakeCommentAPI):
        def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
            # The first listing does not yet see the other run's comment.
            self.calls = getattr(self, "calls", 0) + 1
            visible = self.comments if self.calls > 1 else []
            return [dict(c) for c in visible]

    racing = RacingAPI(comments=list(api.comments), next_id=api.next_id)
    upsert_report_section(racing, REPO, 1, "demo-ci.yml", "B", sleep=no_sleep)
    assert [c["id"] for c in racing.comments] == [older["id"]]
    assert racing.deleted == [older["id"] + 1]
    assert list(parse_sections(racing.comments[0]["body"])) == ["ci.yml", "demo-ci.yml"]


def test_user_token_owner_comments_are_candidates() -> None:
    api = FakeCommentAPI(login="gagan046-dev")
    api.comments.append(
        {"id": 1, "body": REPORT_MARKER + "\n", "user": {"login": "gagan046-dev", "type": "User"}}
    )
    upsert_report_section(api, REPO, 1, "ci.yml", "X", sleep=no_sleep)
    assert len(api.comments) == 1 and "ci.yml" in api.comments[0]["body"]


def test_whole_comment_upsert() -> None:
    api = FakeCommentAPI(comments=load_fixture("comments.json"))
    upsert_whole_comment(api, REPO, 12, EVALS_MARKER, "## new table")
    evals = next(c for c in api.comments if c["id"] == 3401000003)
    assert evals["body"] == f"{EVALS_MARKER}\n## new table"
    fresh = FakeCommentAPI()
    assert upsert_whole_comment(fresh, REPO, 1, EVALS_MARKER, f"{EVALS_MARKER}\nT")
    assert fresh.comments[0]["body"] == f"{EVALS_MARKER}\nT"
