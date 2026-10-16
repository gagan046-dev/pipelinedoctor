from __future__ import annotations

from pipelinedoctor.engine.preprocess import (
    build_excerpt,
    clean_line,
    clean_log,
    find_anchors,
)


def test_clean_strips_timestamps_ansi_and_bom() -> None:
    raw = (
        "﻿2026-10-16T11:58:03.1234567Z ##[group]Run pytest\r\n"
        "2026-10-16T11:58:04.0000001Z \x1b[31mE       assert 1 == 2\x1b[0m\r\n"
        "no timestamp here   \n"
    )
    assert clean_log(raw) == ["##[group]Run pytest", "E       assert 1 == 2", "no timestamp here"]


def test_clean_line_keeps_github_markers() -> None:
    assert clean_line("2026-10-16T11:58:03Z ##[error]boom") == "##[error]boom"


def test_line_numbers_cover_whole_log_and_survive_cleaning(job_log: str) -> None:
    lines = clean_log(job_log)
    assert len(lines) == len(job_log.splitlines())
    assert not any(line.startswith("2026-") for line in lines)


def test_find_anchors() -> None:
    lines = ["ok", "Traceback (most recent call last):", "fine", "npm ERR! code E401", "Killed"]
    assert find_anchors(lines) == [2, 4, 5]


def test_excerpt_windows_tail_and_original_numbers() -> None:
    lines = [f"line {i}" for i in range(1, 501)]
    lines[99] = "Error: something broke"  # line 100
    excerpt = build_excerpt(lines, window=5, max_lines=400)
    assert excerpt.total_lines == 500
    assert excerpt.ranges == [(95, 105), (471, 500)]
    assert excerpt.get(100) == "Error: something broke"
    assert excerpt.get(50) is None
    rendered = excerpt.render()
    assert "100| Error: something broke" in rendered
    assert "(365 lines omitted)" in rendered


def test_excerpt_merges_overlapping_windows() -> None:
    lines = ["x"] * 100
    lines[9] = "ERROR one"
    lines[14] = "ERROR two"
    excerpt = build_excerpt(lines, window=5, max_lines=400)
    assert excerpt.ranges[0] == (5, 20)


def test_excerpt_cap_keeps_windows_closest_to_the_end() -> None:
    lines = ["x"] * 2000
    for n in (100, 600, 1200, 1900):
        lines[n - 1] = f"FAILED test_{n}"
    excerpt = build_excerpt(lines, window=40, max_lines=150)
    assert len(excerpt.lines) == 150
    assert excerpt.get(1900) is not None
    assert excerpt.get(100) is None
    assert max(excerpt.lines) == 2000


def test_excerpt_uses_only_last_six_anchors() -> None:
    lines = ["x"] * 1000
    anchor_lines = [100, 200, 300, 400, 500, 600, 700, 800]
    for n in anchor_lines:
        lines[n - 1] = "Error here"
    excerpt = build_excerpt(lines, window=2, max_lines=400)
    assert excerpt.get(100) is None and excerpt.get(200) is None
    assert all(excerpt.get(n) for n in anchor_lines[2:])


def test_empty_log() -> None:
    excerpt = build_excerpt([], window=40, max_lines=400)
    assert excerpt.total_lines == 0 and excerpt.lines == {} and excerpt.render() == ""
