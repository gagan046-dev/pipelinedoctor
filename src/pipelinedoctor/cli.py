"""`pipelinedoctor` command line (ARCHITECTURE.md §4.2)."""

from __future__ import annotations

import json
import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from pipelinedoctor import __version__
from pipelinedoctor.config import ConfigError, Settings
from pipelinedoctor.engine.diagnose import CommentMode as DiagnoseCommentMode
from pipelinedoctor.engine.diagnose import (
    JobContext,
    RunDiagnosis,
    analyze_log,
    build_report,
    diagnose_run,
    make_budget,
    make_llm_client,
    occurrence_id,
)
from pipelinedoctor.github.client import GitHubClient, GitHubError
from pipelinedoctor.render.markdown import render_summary
from pipelinedoctor.schemas import EvalResult, JobRef, RunInfo, StepRef, write_schemas

app = typer.Typer(
    name="pipelinedoctor",
    help="Diagnose failed GitHub Actions runs and explain them on the pull request.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
eval_app = typer.Typer(
    help="Evaluate prompt + models against the eval dataset.", no_args_is_help=True
)
app.add_typer(eval_app, name="eval")

EXIT_CONFIG = 2
EXIT_GITHUB = 3


class CommentMode(StrEnum):
    auto = "auto"
    always = "always"
    never = "never"


class Subset(StrEnum):
    pr = "pr"
    all = "all"


class LLMModeOption(StrEnum):
    live = "live"
    cache_only = "cache-only"
    rules = "rules"


class GateMode(StrEnum):
    pr = "pr"
    nightly = "nightly"


log = logging.getLogger("pipelinedoctor")


def _utf8_stdio() -> None:
    # Windows consoles default to cp1252; reports contain arrows and ellipses.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _settings() -> Settings:
    _utf8_stdio()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        typer.echo(f"configuration error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG) from exc
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return settings


def write_github_output(path: str | None, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            # Values are single-line by construction; strip newlines so nothing can inject keys.
            fh.write(f"{key}={value.replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def append_step_summary(path: str | None, markdown: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def _write_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def publish_diagnosis(
    settings: Settings,
    outcome: RunDiagnosis,
    output: Path,
    excerpt_output: Path | None,
    summary: bool,
) -> None:
    report = outcome.report
    _write_json(output, report.model_dump_json(indent=2))
    if excerpt_output is not None:
        excerpt_output.parent.mkdir(parents=True, exist_ok=True)
        excerpt_output.write_text(outcome.excerpt.render() + "\n", encoding="utf-8", newline="\n")
    if summary:
        append_step_summary(settings.step_summary_path, render_summary(report))
    write_github_output(
        settings.github_output_path,
        {
            "occurrence-id": report.occurrence_id,
            "category": report.category.value,
            "confidence": f"{report.confidence:.3f}",
            "source": report.source,
            "report-path": str(output),
            "comment-url": outcome.comment_url or "",
        },
    )


def run_diagnose(
    settings: Settings,
    repo: str,
    run_id: int,
    job_id: int | None,
    comment: DiagnoseCommentMode,
    output: Path,
    excerpt_output: Path | None,
    summary: bool,
    no_llm: bool,
) -> RunDiagnosis:
    """Shared by `diagnose` and the Docker action. Raises typer.Exit with the contract codes."""
    if not settings.github_token:
        typer.echo("configuration error: GITHUB_TOKEN (or GH_TOKEN) is required", err=True)
        raise typer.Exit(EXIT_CONFIG)
    if "/" not in repo:
        typer.echo(f"configuration error: --repo must be OWNER/REPO, got {repo!r}", err=True)
        raise typer.Exit(EXIT_CONFIG)
    gh = GitHubClient(settings.github_token, settings.github_api_url, settings.github_server_url)
    try:
        outcome = diagnose_run(
            settings,
            gh,
            repo,
            run_id,
            job_id=job_id,
            comment=comment,
            use_llm=not no_llm,
            llm_client=None if no_llm else make_llm_client(settings),
        )
    except GitHubError as exc:
        typer.echo(f"GitHub API error: {exc}", err=True)
        raise typer.Exit(EXIT_GITHUB) from exc
    publish_diagnosis(settings, outcome, output, excerpt_output, summary)
    report = outcome.report
    typer.echo(
        f"{report.occurrence_id}: {report.category.value} "
        f"(confidence {report.confidence:.2f}, source {report.source})"
        + (f" -> {outcome.comment_url}" if outcome.comment_url else "")
    )
    return outcome


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


@app.command()
def diagnose(
    repo: Annotated[str, typer.Option(help="OWNER/REPO of the failed run.")],
    run_id: Annotated[int, typer.Option(help="ID of the failed workflow run.")],
    job_id: Annotated[
        int | None, typer.Option(help="Job to diagnose (default: first failed).")
    ] = None,
    comment: Annotated[CommentMode, typer.Option(help="Post the PR comment.")] = CommentMode.auto,
    output: Annotated[Path, typer.Option(help="Report JSON path.")] = Path("diagnosis.json"),
    excerpt_output: Annotated[Path, typer.Option(help="Redacted excerpt path.")] = Path(
        "excerpt.txt"
    ),
    summary: Annotated[bool, typer.Option(help="Write $GITHUB_STEP_SUMMARY.")] = True,
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Rules only.")] = False,
) -> None:
    """Diagnose a failed GitHub Actions run and (optionally) comment on its pull request."""
    settings = _settings()
    run_diagnose(
        settings, repo, run_id, job_id, comment.value, output, excerpt_output, summary, no_llm
    )


@app.command("diagnose-log")
def diagnose_log(
    logfile: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Saved job log.")],
    workflow: Annotated[str, typer.Option(help="Workflow name for context.")] = "local",
    job: Annotated[str, typer.Option(help="Job name for context.")] = "local",
    step: Annotated[str | None, typer.Option(help="Failed step name, if known.")] = None,
    output: Annotated[Path | None, typer.Option(help="Also write the report JSON here.")] = None,
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Rules only.")] = False,
) -> None:
    """Learning mode: diagnose a log file on disk and print the markdown report."""
    settings = _settings()
    raw = logfile.read_text(encoding="utf-8", errors="replace")
    ctx = JobContext(
        workflow=workflow, job=job, failed_step=StepRef(number=0, name=step) if step else None
    )
    analysis = analyze_log(
        raw,
        ctx,
        settings,
        llm_client=None if no_llm else make_llm_client(settings),
        budget=make_budget(settings),
        use_llm=not no_llm,
    )
    report = build_report(
        analysis,
        occurrence=occurrence_id(settings),
        repo=settings.repository or "local/local",
        run=RunInfo(workflow_name=workflow),
        failed_job=JobRef(id=0, name=job),
        ctx=ctx,
    )
    if output is not None:
        _write_json(output, report.model_dump_json(indent=2))
    typer.echo(render_summary(report))


@app.command()
def schema(
    write: Annotated[Path, typer.Option("--write", help="Directory for the JSON schema files.")],
) -> None:
    """Export the JSON contracts as JSON Schema files."""
    for path in write_schemas(write):
        typer.echo(str(path))


@app.command()
def action() -> None:
    """Entrypoint of the Docker container action (reads INPUT_* variables)."""
    from pipelinedoctor.action import main

    main()


# --------------------------------------------------------------------------- eval commands


@eval_app.command("run")
def eval_run(
    subset: Annotated[Subset, typer.Option(help="pr = 10 cases, all = full dataset.")] = Subset.pr,
    cases: Annotated[Path, typer.Option(help="Cases directory.")] = Path("evals/cases"),
    cache_dir: Annotated[Path, typer.Option(help="Result cache directory.")] = Path(".eval-cache"),
    no_cache_read: Annotated[
        bool, typer.Option("--no-cache-read", help="Ignore cached results (still writes).")
    ] = False,
    llm: Annotated[
        LLMModeOption,
        typer.Option(help="live = call the LLM, cache-only = no calls, rules = offline baseline."),
    ] = LLMModeOption.live,
    judge: Annotated[bool, typer.Option("--judge", help="Score fixes with LLM-as-judge.")] = False,
    judge_sample: Annotated[int, typer.Option(help="Cases to judge.")] = 8,
    max_requests: Annotated[int | None, typer.Option(help="LLM request budget.")] = None,
    output: Annotated[Path, typer.Option(help="Result JSON.")] = Path("eval-results/result.json"),
) -> None:
    """Run the eval dataset and write eval-results/result.json."""
    from pipelinedoctor.evals.runner import EvalOptions, run_evals

    settings = _settings()
    options = EvalOptions(
        subset=subset.value,
        cases_dir=cases,
        cache_dir=cache_dir,
        cache_read=not no_cache_read,
        llm_mode=llm.value,
        judge=judge,
        judge_sample=judge_sample,
        max_requests=max_requests,
    )
    result = run_evals(options, settings)
    _write_json(output, result.model_dump_json(indent=2))
    agg = result.aggregate
    typer.echo(
        f"scored {agg.cases_scored}/{agg.cases_total} · accuracy {agg.category_accuracy} · "
        f"evidence {agg.evidence_validity} · recall {agg.key_fact_recall} · "
        f"llm calls {agg.total_llm_calls} · cache hits {agg.cache_hits} -> {output}"
    )


def _load_result(path: Path) -> EvalResult:
    return EvalResult.model_validate_json(path.read_text(encoding="utf-8"))


@eval_app.command("gate")
def eval_gate(
    result: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    thresholds: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    mode: Annotated[GateMode, typer.Option()],
    markdown_output: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Exit 1 if the result is below the thresholds (neutral = exit 0 with a warning)."""
    from pipelinedoctor.evals.gate import evaluate_gate, load_thresholds, render_gate_markdown

    settings = _settings()
    eval_result = _load_result(result)
    outcome = evaluate_gate(eval_result, load_thresholds(thresholds, mode.value))
    markdown = render_gate_markdown(eval_result, outcome, mode.value)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(markdown, encoding="utf-8", newline="\n")
    append_step_summary(settings.step_summary_path, markdown)
    typer.echo(markdown)
    for message in outcome.messages:
        level = "warning" if outcome.status != "fail" else "error"
        typer.echo(f"::{level}::{message}")
    if outcome.status == "fail":
        typer.echo(f"::error::Eval gate failed: {', '.join(outcome.failures)}")
        raise typer.Exit(1)


@eval_app.command("comment")
def eval_comment(
    result: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    thresholds: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    repo: Annotated[str, typer.Option()],
    pr: Annotated[int, typer.Option()],
    mode: Annotated[GateMode, typer.Option()] = GateMode.pr,
) -> None:
    """Create or update the eval score-table comment on a PR."""
    from pipelinedoctor.evals.gate import evaluate_gate, load_thresholds, render_gate_markdown
    from pipelinedoctor.github.comments import EVALS_MARKER, upsert_whole_comment

    settings = _settings()
    if not settings.github_token:
        typer.echo("configuration error: GITHUB_TOKEN is required", err=True)
        raise typer.Exit(EXIT_CONFIG)
    eval_result = _load_result(result)
    outcome = evaluate_gate(eval_result, load_thresholds(thresholds, mode.value))
    body = render_gate_markdown(eval_result, outcome, mode.value)
    gh = GitHubClient(settings.github_token, settings.github_api_url, settings.github_server_url)
    try:
        url = upsert_whole_comment(gh, repo, pr, EVALS_MARKER, body)
    except GitHubError as exc:
        typer.echo(f"GitHub API error: {exc}", err=True)
        raise typer.Exit(EXIT_GITHUB) from exc
    typer.echo(url or "comment written")


@eval_app.command("ledger-entry")
def eval_ledger_entry(
    result: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    thresholds: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    mode: Annotated[GateMode, typer.Option()] = GateMode.nightly,
    run_url: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Print one LedgerEntry JSON line (appended to ledger.jsonl on the eval-history branch)."""
    from pipelinedoctor.evals.gate import evaluate_gate, ledger_entry, load_thresholds

    eval_result = _load_result(result)
    limits = load_thresholds(thresholds, mode.value)
    outcome = evaluate_gate(eval_result, limits)
    entry = ledger_entry(eval_result, outcome, limits, run_url, mode.value)
    typer.echo(json.dumps(entry.model_dump(mode="json"), separators=(",", ":")))
