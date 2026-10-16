"""Docker container action entrypoint.

GitHub passes action inputs as environment variables named INPUT_<NAME upper-cased>. For Docker
actions the hyphens are kept (INPUT_GITHUB-TOKEN). Shells cannot read such names
(`$INPUT_GITHUB-TOKEN` is not a valid variable), so this entrypoint is Python and uses os.environ.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast, get_args

import typer

from pipelinedoctor.config import ConfigError, Settings
from pipelinedoctor.engine.diagnose import CommentMode

COMMENT_MODES = get_args(CommentMode)


@dataclass(frozen=True)
class ActionInputs:
    github_token: str
    openrouter_api_key: str
    repo: str
    run_id: int | None
    comment: CommentMode
    models: tuple[str, ...]
    prompt_version: str
    max_llm_calls: int


def read_input(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(f"INPUT_{name.upper()}")
    if value is None:  # also accept the underscore spelling (e.g. when run outside Actions)
        value = env.get(f"INPUT_{name.upper().replace('-', '_')}", default)
    return value.strip()


def parse_inputs(env: Mapping[str, str]) -> ActionInputs:
    run_id_raw = read_input(env, "run-id")
    if run_id_raw and not run_id_raw.isdigit():
        raise ConfigError(f"input run-id must be a number, got {run_id_raw!r}")
    comment = read_input(env, "comment", "auto") or "auto"
    if comment not in COMMENT_MODES:
        raise ConfigError(f"input comment must be auto|always|never, got {comment!r}")
    calls_raw = read_input(env, "max-llm-calls", "2") or "2"
    if calls_raw not in ("1", "2"):
        raise ConfigError(f"input max-llm-calls must be 1 or 2, got {calls_raw!r}")
    models = tuple(m.strip() for m in read_input(env, "models").split(",") if m.strip())
    return ActionInputs(
        github_token=read_input(env, "github-token"),
        openrouter_api_key=read_input(env, "openrouter-api-key"),
        repo=read_input(env, "repo") or env.get("GITHUB_REPOSITORY", ""),
        run_id=int(run_id_raw) if run_id_raw else None,
        comment=cast(CommentMode, comment),
        models=models,
        prompt_version=read_input(env, "prompt-version", "v1") or "v1",
        max_llm_calls=int(calls_raw),
    )


def settings_for_action(inputs: ActionInputs, env: Mapping[str, str]) -> Settings:
    merged = dict(env)
    if inputs.github_token:
        merged["GITHUB_TOKEN"] = inputs.github_token
    if inputs.openrouter_api_key:
        merged["OPENROUTER_API_KEY"] = inputs.openrouter_api_key
    if inputs.models:
        merged["PD_MODELS"] = ",".join(inputs.models)
    merged["PD_PROMPT_VERSION"] = inputs.prompt_version
    merged["PD_LLM_MAX_CALLS"] = str(inputs.max_llm_calls)
    return Settings.from_env(merged)


def main(env: Mapping[str, str] | None = None) -> None:
    from pipelinedoctor.cli import EXIT_CONFIG, run_diagnose

    env = os.environ if env is None else env
    try:
        inputs = parse_inputs(env)
        settings = settings_for_action(inputs, env)
    except ConfigError as exc:
        typer.echo(f"configuration error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG) from exc
    if inputs.run_id is None:
        typer.echo(
            "configuration error: input run-id is empty (not a workflow_run event?)", err=True
        )
        raise typer.Exit(EXIT_CONFIG)
    # Docker actions start in /github/workspace (the host workspace, mounted). Relative paths
    # keep the `report-path` output meaningful for later steps that run on the host.
    run_diagnose(
        settings,
        inputs.repo,
        inputs.run_id,
        None,
        inputs.comment,
        Path("pipelinedoctor-report.json"),
        Path("pipelinedoctor-excerpt.txt"),
        True,
        False,
    )
