from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from helpers import API, MINI_CASES, REPO, ROOT, THRESHOLDS, fixture_text, load_fixture
from pipelinedoctor import __version__
from pipelinedoctor.cli import app
from pipelinedoctor.github.comments import EVALS_MARKER

runner = CliRunner()
THRESHOLDS_FILE = str(THRESHOLDS)
BLOB = "https://results.blob.core.windows.net/logs/job.txt?sig=1"


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip() == __version__


def test_schema_files_are_up_to_date(tmp_path: Path) -> None:
    result = runner.invoke(app, ["schema", "--write", str(tmp_path)])
    assert result.exit_code == 0
    for generated in tmp_path.iterdir():
        committed = ROOT / "schemas" / generated.name
        assert committed.read_text(encoding="utf-8") == generated.read_text(encoding="utf-8"), (
            f"run `pipelinedoctor schema --write schemas` ({generated.name} is stale)"
        )


def test_diagnose_log_prints_markdown(tmp_path: Path) -> None:
    log = MINI_CASES / "docker-copy-not-found" / "log.txt"
    out = tmp_path / "r.json"
    result = runner.invoke(
        app,
        [
            "diagnose-log",
            str(log),
            "--workflow",
            "Container",
            "--step",
            "Build image",
            "--output",
            str(out),
            "--no-llm",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "### DOCKER · Container → local → Build image" in result.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["category"] == "docker"


def test_diagnose_requires_token() -> None:
    result = runner.invoke(app, ["diagnose", "--repo", REPO, "--run-id", "1"])
    assert result.exit_code == 2
    assert "GITHUB_TOKEN" in result.output


def test_diagnose_rejects_bad_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    result = runner.invoke(app, ["diagnose", "--repo", "nope", "--run-id", "1"])
    assert result.exit_code == 2


def test_bad_key_file_is_a_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY_FILE", str(tmp_path / "missing.txt"))
    result = runner.invoke(app, ["diagnose", "--repo", REPO, "--run-id", "1"])
    assert result.exit_code == 2


@respx.mock
def test_diagnose_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    outputs = tmp_path / "outputs.txt"
    for name, value in {
        "GITHUB_TOKEN": "test-token",
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_NUMBER": "42",
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_OUTPUT": str(outputs),
    }.items():
        monkeypatch.setenv(name, value)
    run = load_fixture("run.json")
    respx.get(f"{API}/repos/{REPO}/actions/runs/{run['id']}").respond(json=run)
    respx.get(f"{API}/repos/{REPO}/actions/runs/{run['id']}/jobs").respond(
        json={"jobs": load_fixture("jobs-page1.json")["jobs"]}
    )
    respx.get(f"{API}/repos/{REPO}/actions/jobs/51234567891/logs").respond(
        302, headers={"Location": BLOB}
    )
    respx.get(BLOB).respond(text=fixture_text("job-log.txt"))
    respx.get(f"{API}/user").respond(403, json={})
    comments: list[dict[str, object]] = []

    def create(request: httpx.Request) -> httpx.Response:
        comment = {
            "id": 77,
            "body": json.loads(request.content)["body"],
            "html_url": "https://github.com/x/pull/12#c77",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
        comments.append(comment)
        return httpx.Response(201, json=comment)

    respx.get(f"{API}/repos/{REPO}/issues/12/comments").mock(
        side_effect=lambda request: httpx.Response(200, json=comments)
    )
    respx.post(f"{API}/repos/{REPO}/issues/12/comments").mock(side_effect=create)
    respx.get(f"{API}/repos/{REPO}/issues/comments/77").mock(
        side_effect=lambda request: httpx.Response(200, json=comments[0])
    )

    report_path = tmp_path / "diagnosis.json"
    excerpt_path = tmp_path / "excerpt.txt"
    result = runner.invoke(
        app,
        [
            "diagnose",
            "--repo",
            REPO,
            "--run-id",
            str(run["id"]),
            "--output",
            str(report_path),
            "--excerpt-output",
            str(excerpt_path),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["occurrence_id"].startswith("PD-") and report["occurrence_id"].endswith("-0042")
    assert report["source"] == "rules_only" and report["category"] == "test"
    assert "| " in excerpt_path.read_text(encoding="utf-8")
    assert "## PipelineDoctor diagnosis" in summary.read_text(encoding="utf-8")
    out = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines())
    assert out["category"] == "test" and out["source"] == "rules_only"
    assert out["comment-url"] == "https://github.com/x/pull/12#c77"
    assert "pd:section workflow=demo-ci.yml" in str(comments[0]["body"])


@respx.mock
def test_diagnose_github_failure_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    respx.get(f"{API}/repos/{REPO}/actions/runs/1").respond(401, json={"message": "Bad creds"})
    result = runner.invoke(app, ["diagnose", "--repo", REPO, "--run-id", "1"])
    assert result.exit_code == 3


def _rules_result(tmp_path: Path) -> Path:
    out = tmp_path / "result.json"
    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--subset",
            "pr",
            "--llm",
            "rules",
            "--cases",
            str(MINI_CASES),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    return out


def test_eval_run_and_gate_fail_on_low_llm_coverage(tmp_path: Path) -> None:
    out = _rules_result(tmp_path)
    md = tmp_path / "gate.md"
    gate = runner.invoke(
        app,
        [
            "eval",
            "gate",
            "--result",
            str(out),
            "--thresholds",
            THRESHOLDS_FILE,
            "--mode",
            "pr",
            "--markdown-output",
            str(md),
        ],
    )
    assert gate.exit_code == 1
    assert "::error::Eval gate failed: llm_coverage" in gate.stdout
    assert "Offline baseline" in gate.stdout
    assert "| llm_coverage |" in md.read_text(encoding="utf-8")


def test_eval_gate_neutral_without_key(tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    runner.invoke(
        app,
        [
            "eval",
            "run",
            "--llm",
            "cache-only",
            "--cases",
            str(MINI_CASES),
            "--cache-dir",
            str(tmp_path / "c"),
            "--output",
            str(out),
        ],
    )
    gate = runner.invoke(
        app, ["eval", "gate", "--result", str(out), "--thresholds", THRESHOLDS_FILE, "--mode", "pr"]
    )
    assert gate.exit_code == 0 and "::warning::Evals skipped" in gate.stdout


def test_eval_gate_pass_with_example(tmp_path: Path) -> None:
    example = ROOT / "schemas" / "examples" / "eval-result.json"
    gate = runner.invoke(
        app,
        ["eval", "gate", "--result", str(example), "--thresholds", THRESHOLDS_FILE, "--mode", "pr"],
    )
    assert gate.exit_code == 0 and "PASS" in gate.stdout


def test_ledger_entry_is_one_json_line() -> None:
    example = ROOT / "schemas" / "examples" / "eval-result.json"
    result = runner.invoke(
        app,
        [
            "eval",
            "ledger-entry",
            "--result",
            str(example),
            "--thresholds",
            THRESHOLDS_FILE,
            "--mode",
            "nightly",
            "--run-url",
            "https://github.com/x/actions/runs/1",
        ],
    )
    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["schema_version"] == "1" and entry["run_url"].endswith("/1")


@respx.mock
def test_eval_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    respx.get(f"{API}/user").respond(403, json={})
    respx.get(f"{API}/repos/{REPO}/issues/5/comments").respond(json=[])
    post = respx.post(f"{API}/repos/{REPO}/issues/5/comments").respond(
        201, json={"id": 1, "html_url": "https://github.com/x/pull/5#c1"}
    )
    example = ROOT / "schemas" / "examples" / "eval-result.json"
    result = runner.invoke(
        app,
        [
            "eval",
            "comment",
            "--result",
            str(example),
            "--thresholds",
            THRESHOLDS_FILE,
            "--mode",
            "pr",
            "--repo",
            REPO,
            "--pr",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(post.calls.last.request.content)["body"].startswith(EVALS_MARKER)


def test_eval_comment_requires_token() -> None:
    example = ROOT / "schemas" / "examples" / "eval-result.json"
    result = runner.invoke(
        app,
        [
            "eval",
            "comment",
            "--result",
            str(example),
            "--thresholds",
            THRESHOLDS_FILE,
            "--repo",
            REPO,
            "--pr",
            "5",
        ],
    )
    assert result.exit_code == 2
