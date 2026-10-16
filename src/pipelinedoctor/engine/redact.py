"""Remove secrets from log text before it reaches an LLM, a PR comment or an artifact.

GitHub masks registered secrets as `***`, but only the ones it knows about: tokens printed by a
tool, keys in a cat'ed config file or credentials inside URLs slip through. Defence in depth.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

Replacement = str | Callable[[re.Match[str]], str]


def _keep_lines(kind: str) -> Callable[[re.Match[str]], str]:
    # A multi-line secret becomes one marker per line, so evidence line numbers stay valid.
    def replace(match: re.Match[str]) -> str:
        return "\n".join(f"[REDACTED:{kind}]" for _ in match.group(0).split("\n"))

    return replace


_B64_LINE = r"[ \t]*[A-Za-z0-9+/=]{16,}[ \t]*"

PATTERNS: list[tuple[str, re.Pattern[str], Replacement]] = [
    (
        # A complete block, or a BEGIN line followed by base64 body lines with no END in sight
        # (a log cut off in the middle of a key must not leak the part that is there).
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
            r"(?:.*?-----END [A-Z ]*PRIVATE KEY-----|(?:\n" + _B64_LINE + r")*)",
            re.DOTALL,
        ),
        _keep_lines("private_key"),
    ),
    (
        # The other half: body lines whose BEGIN line was cut off before the log starts.
        "private_key",
        re.compile(
            r"(?m)(?:^[ \t]*[A-Za-z0-9+/=]{40,}[ \t]*\n)+[ \t]*-----END [A-Z ]*PRIVATE KEY-----"
        ),
        _keep_lines("private_key"),
    ),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
        "[REDACTED:github_token]",
    ),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github_pat]"),
    ("openrouter_key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}"), "[REDACTED:openrouter_key]"),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "[REDACTED:api_key]"),
    # Vendor tokens that tools print bare, without a key= or Bearer prefix.
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), "[REDACTED:npm_token]"),
    ("pypi_token", re.compile(r"\bpypi-AgE[A-Za-z0-9_-]{50,}"), "[REDACTED:pypi_token]"),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack_token]"),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "[REDACTED:gitlab_token]"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "[REDACTED:google_api_key]"),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    ("jwt", re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), "[REDACTED:jwt]"),
    ("bearer", re.compile(r"\bBearer[ \t]+(?!\[REDACTED)\S+"), "Bearer [REDACTED:bearer]"),
    (
        "url_credentials",
        re.compile(r"://[^/\s:@]+:[^/\s@]+@"),
        "://[REDACTED:url_credentials]@",
    ),
    (
        # [ \t] instead of \s: never let a match run across a line break.
        "assignment",
        re.compile(
            r"(?i)\b([\w.-]*?(?:password|passwd|secret|token|api[_-]?key))([ \t]*[=:][ \t]*)"
            r"(?!\[REDACTED|\*\*\*)([^\s\"']+)"
        ),
        r"\1\2[REDACTED:assignment]",
    ),
]

MIN_EXACT_SECRET_LENGTH = 8


def redact(text: str, extra_secrets: Iterable[str] = ()) -> tuple[str, int]:
    """Return (redacted text, number of replacements). Line count is always preserved."""
    count = 0
    for secret in sorted({s for s in extra_secrets if len(s) >= MIN_EXACT_SECRET_LENGTH}, key=len)[
        ::-1
    ]:
        hits = text.count(secret)
        if hits:
            text = text.replace(secret, "[REDACTED:known_secret]")
            count += hits
    for _kind, pattern, replacement in PATTERNS:
        text, hits = pattern.subn(replacement, text)
        count += hits
    return text, count


def redact_lines(lines: list[str], extra_secrets: Iterable[str] = ()) -> tuple[list[str], int]:
    """Redact a whole log. Run this BEFORE cutting the excerpt: a secret that straddles the
    excerpt edge would otherwise be seen only in part, and the visible part would leak."""
    if not lines:
        return [], 0
    text, count = redact("\n".join(lines), extra_secrets)
    return text.split("\n"), count
