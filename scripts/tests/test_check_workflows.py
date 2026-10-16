"""Tests for scripts/check_workflows.py: one good workflow, one bad workflow per rule."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import check_workflows as cw  # noqa: E402

REPO = SCRIPTS.parent
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"

GOOD = f"""
name: Good
on:
  push:
    branches: [main]
  pull_request:
permissions: {{}}
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
    steps:
      - uses: {CHECKOUT}
        with:
          persist-credentials: false
      - env:
          TITLE: ${{{{ github.event.pull_request.title }}}}
        run: echo "$TITLE"
  reuse:
    uses: ./.github/workflows/reusable-python-ci.yml
    permissions:
      contents: read
"""


def write(tmp_path: Path, body: str, name: str = "wf.yml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def rules(path: Path) -> list[str]:
    return [v.rule for v in cw.check_workflow(path)]


def test_good_workflow_is_clean(tmp_path: Path) -> None:
    assert cw.check_workflow(write(tmp_path, GOOD)) == []


def test_missing_top_level_permissions(tmp_path: Path) -> None:
    body = GOOD.replace("permissions: {}\n", "")
    assert "R1" in rules(write(tmp_path, body))


def test_top_level_permissions_must_be_empty(tmp_path: Path) -> None:
    body = GOOD.replace("permissions: {}\n", "permissions: write-all\n")
    assert "R1" in rules(write(tmp_path, body))


def test_job_without_permissions(tmp_path: Path) -> None:
    body = GOOD.replace("    permissions:\n      contents: read\n    steps:", "    steps:")
    assert "R1" in rules(write(tmp_path, body))


@pytest.mark.parametrize(
    "uses",
    [
        "actions/checkout@v7",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@3d3c42e",
    ],
)
def test_unpinned_or_uncommented_actions(tmp_path: Path, uses: str) -> None:
    body = GOOD.replace(CHECKOUT, uses)
    assert "R2" in rules(write(tmp_path, body))


def test_self_hosted_outside_allowlist(tmp_path: Path) -> None:
    body = GOOD.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted, pd-prod]")
    assert "R3" in rules(write(tmp_path, body))


def test_self_hosted_allowed_job_still_needs_environment_and_no_pr_trigger(tmp_path: Path) -> None:
    body = """
    name: Deploy
    on:
      pull_request:
    permissions: {}
    jobs:
      production:
        runs-on: [self-hosted, pd-prod]
        timeout-minutes: 5
        permissions: {}
        steps:
          - run: echo deploy
    """
    found = cw.check_workflow(write(tmp_path, body, "deploy.yml"))
    messages = " ".join(v.message for v in found if v.rule == "R3")
    assert "environment: production" in messages
    assert "pull_request" in messages


def test_self_hosted_allowed_job_passes(tmp_path: Path) -> None:
    body = """
    name: Deploy
    on:
      workflow_dispatch:
    permissions: {}
    jobs:
      production:
        runs-on: [self-hosted, pd-prod]
        timeout-minutes: 5
        environment:
          name: production
        permissions: {}
        steps:
          - run: echo deploy
    """
    assert cw.check_workflow(write(tmp_path, body, "deploy.yml")) == []


def test_pull_request_target_banned(tmp_path: Path) -> None:
    body = GOOD.replace("  pull_request:\n", "  pull_request_target:\n")
    assert "R4" in rules(write(tmp_path, body))


def test_missing_timeout(tmp_path: Path) -> None:
    body = GOOD.replace("    timeout-minutes: 5\n", "")
    assert "R5" in rules(write(tmp_path, body))


@pytest.mark.parametrize(
    "expr",
    ["${{ github.event.pull_request.title }}", "${{ github.head_ref }}"],
)
def test_event_data_in_run(tmp_path: Path, expr: str) -> None:
    body = GOOD.replace('run: echo "$TITLE"', f'run: echo "{expr}"')
    assert "R6" in rules(write(tmp_path, body))


def test_checkout_must_not_persist_credentials(tmp_path: Path) -> None:
    body = GOOD.replace("persist-credentials: false", "fetch-depth: 0")
    assert "R7" in rules(write(tmp_path, body))


def test_pushing_checkout_allowed_in_nightly_record(tmp_path: Path) -> None:
    body = GOOD.replace("persist-credentials: false", "persist-credentials: true").replace(
        "  build:", "  record:"
    )
    assert "R7" not in rules(write(tmp_path, body, "nightly-evals.yml"))


def test_workflow_run_must_not_checkout_head(tmp_path: Path) -> None:
    body = f"""
    name: Doctor
    on:
      workflow_run:
        workflows: [CI]
        types: [completed]
    permissions: {{}}
    jobs:
      diagnose:
        runs-on: ubuntu-latest
        timeout-minutes: 5
        permissions: {{}}
        steps:
          - uses: {CHECKOUT}
            with:
              ref: ${{{{ github.event.workflow_run.head_sha }}}}
              persist-credentials: false
    """
    assert "R8" in rules(write(tmp_path, body))


def test_yaml_error_is_reported(tmp_path: Path) -> None:
    assert rules(write(tmp_path, "on: [push\n")) == ["YAML"]


def test_composite_action_pinning(tmp_path: Path) -> None:
    action_dir = tmp_path / ".github" / "actions" / "demo"
    action_dir.mkdir(parents=True)
    path = action_dir / "action.yml"
    path.write_text(
        "name: x\nruns:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v7\n",
        encoding="utf-8",
    )
    assert [v.rule for v in cw.check_path(path)] == ["R2"]


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = write(tmp_path, GOOD, "good.yml")
    bad = write(tmp_path, GOOD.replace("permissions: {}\n", ""), "bad.yml")
    assert cw.main([str(good)]) == 0
    assert cw.main([str(bad)]) == 1
    assert "[R1]" in capsys.readouterr().out


def test_repository_workflows_follow_the_policy() -> None:
    targets = cw.default_targets(REPO)
    assert targets, "no workflows found"
    violations = [str(v) for t in targets for v in cw.check_path(t)]
    assert violations == []
