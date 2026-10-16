"""Test doubles: an LLM that answers from a script, and an in-memory GitHub comments API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pipelinedoctor.engine.llm import LLMError, LLMResponse


@dataclass
class Call:
    model: str
    system: str
    user: str
    json_mode: bool


class FakeLLM:
    """Each call pops the next scripted answer: a string (model output) or an LLMError."""

    def __init__(self, *script: str | LLMError | dict[str, Any]) -> None:
        self.script = list(script)
        self.calls: list[Call] = []

    def complete(
        self, *, model: str, system: str, user: str, json_mode: bool, timeout_s: float
    ) -> LLMResponse:
        self.calls.append(Call(model, system, user, json_mode))
        if not self.script:
            raise LLMError("provider_error", "script exhausted")
        item = self.script.pop(0)
        if isinstance(item, LLMError):
            raise item
        text = json.dumps(item) if isinstance(item, dict) else item
        return LLMResponse(
            text=text, model=model, latency_ms=1234, input_tokens=900, output_tokens=120
        )


def llm_answer(
    category: str = "test",
    line: int = 1,
    quote: str = "",
    confidence: float = 0.9,
    **extra: Any,
) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "category": category,
        "failed_step": "Run tests",
        "summary": "test_total_with_tax fails: expected 107.00, got 106.00.",
        "probable_cause": "apply_tax rounds before multiplying.",
        "evidence": [{"line": line, "quote": quote, "why": "The failing assertion."}],
        "recommended_actions": [
            {"action": "Round after applying the tax.", "command": "pytest -k tax"}
        ],
        "verification": ["Re-run Demo CI."],
        "confidence": confidence,
    }
    answer.update(extra)
    return answer


@dataclass
class FakeCommentAPI:
    """In-memory issue comments. `drop_next_update` simulates a racing writer."""

    comments: list[dict[str, Any]] = field(default_factory=list)
    login: str | None = None
    drop_next_updates: int = 0
    next_id: int = 9000
    deleted: list[int] = field(default_factory=list)

    def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return [dict(c) for c in self.comments]

    def get_comment(self, repo: str, comment_id: int) -> dict[str, Any]:
        return dict(next(c for c in self.comments if c["id"] == comment_id))

    def create_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        self.next_id += 1
        comment = {
            "id": self.next_id,
            "body": body,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
            "html_url": f"https://github.com/{repo}/pull/{number}#issuecomment-{self.next_id}",
        }
        self.comments.append(comment)
        return dict(comment)

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        comment = next(c for c in self.comments if c["id"] == comment_id)
        if self.drop_next_updates:
            self.drop_next_updates -= 1
            return dict(comment)
        comment["body"] = body
        return dict(comment)

    def delete_comment(self, repo: str, comment_id: int) -> None:
        self.deleted.append(comment_id)
        self.comments = [c for c in self.comments if c["id"] != comment_id]

    def own_login(self) -> str | None:
        return self.login
