from __future__ import annotations

from fakes import FakeLLM, llm_answer
from pipelinedoctor.config import Settings
from pipelinedoctor.engine.diagnose import JobContext, analyze_log, build_report
from pipelinedoctor.engine.llm import RequestBudget
from pipelinedoctor.github.comments import parse_sections
from pipelinedoctor.render.markdown import fence, render_pr_section, render_summary, safe_md
from pipelinedoctor.schemas import DiagnosisReport, JobRef, RunInfo, StepRef


def test_safe_md_neutralises_mentions_and_html() -> None:
    text = safe_md("ping @octocat <img src=x onerror=alert(1)> and <script>x</script>")
    assert "@\u200boctocat" in text
    assert "<" not in text and ">" not in text
    assert safe_md(None) == ""
    assert safe_md("a" * 20, limit=5) == "aaaa…"


def test_fence_is_longer_than_any_backtick_run() -> None:
    content = "```\nnested ```` fence"
    fenced = fence(content)
    assert fenced.startswith("`````\n") and fenced.endswith("\n`````")
    assert fence("plain").startswith("```")


def test_fence_neutralises_html_comments() -> None:
    assert "<!--" not in fence("<!-- pd:end -->")


def _report(log: str, llm: FakeLLM | None) -> DiagnosisReport:
    ctx = JobContext(
        workflow="Demo CI", job="test", failed_step=StepRef(number=6, name="Run tests")
    )
    analysis = analyze_log(
        log, ctx, Settings(min_request_interval_s=0), llm_client=llm, budget=RequestBudget(2)
    )
    return build_report(
        analysis,
        occurrence="PD-2026-0042",
        repo="o/r",
        run=RunInfo(id=5, run_number=57, workflow_name="Demo CI", html_url="https://x/runs/5"),
        failed_job=JobRef(id=1, name="test"),
        ctx=ctx,
    )


def test_pr_section_layout_and_safety() -> None:
    log = (
        "E   assert 1 == 2\n<!-- pd:end --> @admin ```injected```\n"
        "##[error]Process completed with exit code 1.\n"
    )
    answer = llm_answer(
        line=1,
        quote="assert 1 == 2",
        summary="Assertion failed <b>bold</b> cc @admin",
        recommended_actions=[{"action": "Fix it", "command": "pytest ```x```"}],
    )
    report = _report(log, FakeLLM(answer))
    section = render_pr_section(report, "demo-ci.yml")
    assert section.startswith("<!-- pd:section workflow=demo-ci.yml -->")
    assert section.endswith("<!-- pd:end -->")
    assert parse_sections(section) == {"demo-ci.yml": section}
    assert "### TEST · Demo CI → test → Run tests" in section
    assert "**Occurrence** `PD-2026-0042` · **Confidence** A (0.90) · **Source** llm" in section
    assert "EXHIBIT A — line 1" in section
    assert "@\u200badmin" in section and "<b>" not in section
    assert "````bash\npytest ```x```\n````" in section
    assert "prompt diagnose.v1" in section and "run #57" in section and "$0.0000" in section


def test_summary_marks_rule_based_reports() -> None:
    summary = render_summary(_report("ModuleNotFoundError: No module named 'x'\n", None))
    assert summary.startswith("## PipelineDoctor diagnosis")
    assert "DEPENDENCY" in summary and "rule-based" in summary
    assert "pd:section" not in summary
    assert "Engine notes" in summary


def test_secret_permission_stamp_and_empty_evidence() -> None:
    report = _report("gh: Resource not accessible by integration (HTTP 403)\n", None)
    report = report.model_copy(update={"evidence": []})
    body = render_summary(report)
    assert "SECRET / PERMISSION" in body
    assert "No log line could be verified" in body


def test_safe_md_defangs_links_and_images() -> None:
    text = safe_md("[Fix here](http://evil.example) ![t](https://evil.example/p.gif) www")
    assert "](" not in text and "![" not in text
    assert "http://" not in text and "https://" not in text
