"""Secrets are assembled at runtime so that no secret-looking literal is ever committed."""

from __future__ import annotations

import pytest

from pipelinedoctor.config import Settings
from pipelinedoctor.engine.diagnose import prepare_excerpt
from pipelinedoctor.engine.redact import redact, redact_lines

A20 = "A1b2C3d4E5f6G7h8I9j0"
CASES = [
    ("ghp_" + A20 * 2, "[REDACTED:github_token]"),
    ("ghs_" + A20, "[REDACTED:github_token]"),
    ("github_pat_" + A20 + "_" + A20, "[REDACTED:github_pat]"),
    ("sk-or-v1-" + A20 * 3, "[REDACTED:openrouter_key]"),
    ("sk-" + A20 * 2, "[REDACTED:api_key]"),
    ("AKIA" + "IOSFODNN7EXAMPLE", "[REDACTED:aws_key]"),
    ("eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "[REDACTED:jwt]"),
    ("Authorization: Bearer " + "abc.def-ghi", "Bearer [REDACTED:bearer]"),
    ("https://user:" + "hunter2@registry.example.com/simple", "://[REDACTED:url_credentials]@"),
    ("DB_PASSWORD=" + "s3cr3t-value", "DB_PASSWORD=[REDACTED:assignment]"),
    ("//registry.npmjs.org/:_authToken=" + "npm_" + A20, "_authToken=[REDACTED:assignment]"),
    ("api_key: " + "q9w8e7r6t5", "api_key: [REDACTED:assignment]"),
    ("npm_" + A20 + "x" * 16, "[REDACTED:npm_token]"),
    ("pypi-AgE" + A20 * 3, "[REDACTED:pypi_token]"),
    ("xoxb-" + "1234567890-" + A20, "[REDACTED:slack_token]"),
    ("glpat-" + A20, "[REDACTED:gitlab_token]"),
    ("AIza" + A20 + "x" * 15, "[REDACTED:google_api_key]"),
]


@pytest.mark.parametrize(("secret_text", "marker"), CASES)
def test_each_pattern_is_redacted(secret_text: str, marker: str) -> None:
    redacted, count = redact(f"before {secret_text} after")
    assert marker in redacted
    assert count >= 1


def test_private_key_block_keeps_line_count() -> None:
    begin = "-----BEGIN " + "RSA PRIVATE KEY-----"
    end = "-----END " + "RSA PRIVATE KEY-----"
    text = f"start\n{begin}\nMIIEow\nIBAAKC\n{end}\nafter"
    redacted, count = redact(text)
    assert count == 1
    assert redacted.split("\n") == ["start"] + ["[REDACTED:private_key]"] * 4 + ["after"]


def test_exact_known_secret_values() -> None:
    value = "plain-looking-value-123"
    redacted, count = redact(f"echo {value}", extra_secrets=[value, "short"])
    assert redacted == "echo [REDACTED:known_secret]" and count == 1


def test_github_masks_and_empty_values_are_left_alone() -> None:
    text = "  OPENROUTER_API_KEY: ***\n  DEMO_PUBLISH_TOKEN: \nError: DEMO_PUBLISH_TOKEN is not set"
    redacted, count = redact(text)
    assert redacted == text and count == 0


def test_assignment_never_crosses_lines() -> None:
    redacted, _ = redact("token:\nnext line stays")
    assert redacted == "token:\nnext line stays"


def test_redact_lines_preserves_line_count() -> None:
    lines, count = redact_lines(["token=" + A20, "fine", "x"])
    assert lines == ["token=[REDACTED:assignment]", "fine", "x"]
    assert count == 1
    assert redact_lines([]) == ([], 0)


BEGIN = "-----BEGIN " + "RSA PRIVATE KEY-----"
END = "-----END " + "RSA PRIVATE KEY-----"
BODY = "MIIEowIBAAKCAQEA" + "Qk9EWQ" * 8


def test_key_without_end_line_is_redacted() -> None:
    redacted, _ = redact(f"start\n{BEGIN}\n{BODY}\n{BODY}\nError: cut")
    assert BODY not in redacted
    assert redacted.split("\n")[-1] == "Error: cut"


def test_key_without_begin_line_is_redacted() -> None:
    redacted, _ = redact(f"{BODY}\n{BODY}\n{END}\nafter")
    assert BODY not in redacted and END not in redacted
    assert redacted.split("\n") == ["[REDACTED:private_key]"] * 3 + ["after"]


def test_key_straddling_the_excerpt_edge_never_reaches_the_excerpt() -> None:
    # 200-line key whose BEGIN line is far outside the +-40 line window around the error.
    log = ["setup"] + [BEGIN] + [BODY] * 200 + [END] + ["noise"] * 10 + ["Error: boom"]
    excerpt = prepare_excerpt("\n".join(log), Settings(), [])
    assert all(BODY not in line for line in excerpt.lines.values())
    assert excerpt.redactions >= 1


def test_bare_npm_token_in_a_plain_log_line() -> None:
    token = "npm_" + A20 + "Q" * 16
    excerpt = prepare_excerpt(f"npm notice using {token}\nnpm ERR! code E401\n", Settings(), [])
    assert all(token not in line for line in excerpt.lines.values())
