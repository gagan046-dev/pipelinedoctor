"""Static checks on the frontend: XSS-safe rendering, self-hosted assets, design-brief rules."""

from __future__ import annotations

import re
from pathlib import Path

from pipelinedoctor_console.models import Category

PKG = Path(__file__).resolve().parents[1] / "src" / "pipelinedoctor_console"
JS = sorted((PKG / "static" / "js").glob("*.js"))
CSS = (PKG / "static" / "css" / "console.css").read_text(encoding="utf-8")
TEMPLATES = sorted((PKG / "templates").glob("*.html"))


def test_no_html_injection_sinks_in_js() -> None:
    forbidden = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(")
    for path in JS:
        hits = forbidden.findall(path.read_text(encoding="utf-8"))
        assert not hits, f"{path.name}: {hits}"


def test_templates_do_not_disable_autoescape_or_inline_scripts() -> None:
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        assert "|safe" not in text and "autoescape false" not in text, path.name
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), f"inline script in {path.name}"
        assert " style=" not in text, f"inline style in {path.name} (blocked by CSP)"
        assert not re.search(r"\son[a-z]+=", text), f"inline handler in {path.name}"


def test_no_external_requests() -> None:
    sources = [p.read_text(encoding="utf-8") for p in [*JS, *TEMPLATES]] + [CSS]
    for text in sources:
        for url in re.findall(r"https?://[^\s\"')]+", text):
            assert url.startswith("http://www.w3.org/2000/svg"), url


def test_fonts_are_self_hosted_and_not_generic() -> None:
    assert "/static/fonts/source-serif-4-latin-400-normal.woff2" in CSS
    assert "/static/fonts/ibm-plex-mono-latin-400-normal.woff2" in CSS
    for banned in ("Inter", "Roboto", "system-ui", "-apple-system", "Segoe UI", "Helvetica"):
        assert banned not in CSS, banned


def test_stamp_inks_for_every_category() -> None:
    for category in Category:
        assert f"--ink-{category.value}:" in CSS, category
        assert f".stamp-{category.value}" in CSS, category
    assert "rotate(-1.5deg)" in CSS
    assert "double" in CSS
    common = (PKG / "static" / "js" / "common.js").read_text(encoding="utf-8")
    assert '"Secret / Permission"' in common


def test_design_brief_avoid_list() -> None:
    lowered = CSS.lower()
    for banned in (
        "linear-gradient",
        "radial-gradient",
        "backdrop-filter",
        "blur(",
        "text-shadow",
        "drop-shadow",
    ):
        assert banned not in lowered, banned
    # Only an inset rule for the cited log line; never a drop shadow.
    for shadow in re.findall(r"box-shadow:\s*([^;]+);", CSS):
        assert shadow.strip().startswith("inset"), shadow
    for radius in re.findall(r"border-radius:\s*([0-9.]+)px", CSS):
        assert float(radius) <= 2, radius
    # Both themes: prefers-color-scheme with an explicit light override, and a manual dark toggle.
    assert ':root:not([data-theme="light"])' in CSS
    assert ':root[data-theme="dark"]' in CSS


def test_report_sections_exact() -> None:
    report_js = (PKG / "static" / "js" / "report.js").read_text(encoding="utf-8")
    for n, title in enumerate(
        [
            "Synopsis",
            "Sequence of events",
            "Evidence",
            "Probable cause",
            "Recommended actions",
            "Verification",
        ],
        start=1,
    ):
        assert f'section({n}, "{title}"' in report_js


def test_theme_storage_is_guarded() -> None:
    common = (PKG / "static" / "js" / "common.js").read_text(encoding="utf-8")
    for call in re.findall(r".*localStorage.*", common):
        assert "try" in call, call
