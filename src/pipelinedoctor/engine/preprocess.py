"""Turn a raw GitHub Actions job log into a small, numbered excerpt worth sending to an LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# GitHub prefixes every log line with an RFC 3339 timestamp, e.g. "2026-10-16T11:58:03.1234567Z ".
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ANCHOR_RE = re.compile(
    r"##\[error\]|Error|ERROR|FAILED|Traceback|npm ERR!|error:|Killed|exit code [1-9]"
    r"|Process completed with exit code"
)

MAX_ANCHORS = 6
TAIL_LINES = 30


def clean_line(line: str) -> str:
    return ANSI_RE.sub("", TIMESTAMP_RE.sub("", line)).rstrip()


def clean_log(raw: str) -> list[str]:
    """Strip BOM, timestamps and ANSI colour codes.

    Index 0 of the returned list is line 1. Line numbers are assigned *after* cleaning over the
    whole job log; they are the numbers a report cites as evidence.
    """
    text = raw.removeprefix("﻿")
    # A BOM can also appear at the start of each step's output on Windows runners.
    return [clean_line(line.replace("﻿", "")) for line in text.splitlines()]


def find_anchors(lines: list[str]) -> list[int]:
    """1-based line numbers of lines that look like errors."""
    return [i for i, line in enumerate(lines, start=1) if ANCHOR_RE.search(line)]


@dataclass
class Excerpt:
    """A bounded subset of log lines that keeps the original line numbers."""

    lines: dict[int, str]
    total_lines: int
    ranges: list[tuple[int, int]] = field(default_factory=list)
    redactions: int = 0

    @property
    def numbers(self) -> list[int]:
        return sorted(self.lines)

    def get(self, number: int) -> str | None:
        return self.lines.get(number)

    def render(self) -> str:
        """Numbered text, with a gap marker where lines were left out."""
        out: list[str] = []
        previous = 0
        width = len(str(self.total_lines))
        for number in self.numbers:
            if previous and number != previous + 1:
                out.append(f"{'':>{width}}| ... ({number - previous - 1} lines omitted)")
            out.append(f"{number:>{width}}| {self.lines[number]}")
            previous = number
        return "\n".join(out)


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_excerpt(lines: list[str], window: int = 40, max_lines: int = 400) -> Excerpt:
    """Windows of ±`window` lines around the last anchors plus the log tail, capped at `max_lines`.

    When the cap is hit, the windows closest to the end of the log win: in CI logs the real error
    is almost always near the bottom, while earlier "errors" are often noise.
    """
    total = len(lines)
    if total == 0:
        return Excerpt(lines={}, total_lines=0)
    anchors = find_anchors(lines)[-MAX_ANCHORS:]
    wanted = [(max(1, a - window), min(total, a + window)) for a in anchors]
    wanted.append((max(1, total - TAIL_LINES + 1), total))
    merged = _merge(wanted)

    kept: list[tuple[int, int]] = []
    budget = max_lines
    for start, end in reversed(merged):
        if budget <= 0:
            break
        size = end - start + 1
        if size > budget:
            start = end - budget + 1
            size = budget
        kept.append((start, end))
        budget -= size
    kept.reverse()

    selected = {n: lines[n - 1] for start, end in kept for n in range(start, end + 1)}
    return Excerpt(lines=selected, total_lines=total, ranges=kept)
