from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer

from pipelinedoctor import action
from pipelinedoctor.config import DEFAULT_MODELS, ConfigError, Settings, load_openrouter_key
from pipelinedoctor.engine.fingerprint import engine_fingerprint
from pipelinedoctor.engine.prompting import PromptError, load_prompt, parse_prompt

# --------------------------------------------------------------------------- config


def test_settings_defaults() -> None:
    s = Settings.from_env({})
    assert s.models == DEFAULT_MODELS and s.prompt_version == "v1"
    assert (s.llm_timeout_s, s.llm_max_calls, s.min_request_interval_s) == (60.0, 2, 3.5)
    assert s.max_llm_requests is None and s.request_budget(12) == 12
    assert not s.in_actions and s.openrouter_api_key is None


def test_settings_from_env_values() -> None:
    s = Settings.from_env(
        {
            "OPENROUTER_API_KEY": "  k-123  ",
            "GH_TOKEN": "gh-1",
            "GITHUB_API_URL": "https://ghe.example/api/v3/",
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_NUMBER": "7",
            "PD_MODELS": "a, b ,",
            "PD_LLM_MAX_CALLS": "9",
            "PD_MAX_LLM_REQUESTS": "4",
            "PD_DISABLE_LLM": "1",
        }
    )
    assert s.openrouter_api_key == "k-123" and s.github_token == "gh-1"
    assert s.github_api_url == "https://ghe.example/api/v3"
    assert s.models == ("a", "b") and s.llm_max_calls == 2  # hard cap
    assert s.request_budget(12) == 4 and s.disable_llm and s.run_number == 7
    assert "k-123" not in repr(s) and "gh-1" not in repr(s)
    assert s.secret_values() == ["k-123", "gh-1"]


def test_bad_numbers_are_config_errors() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({"PD_LLM_TIMEOUT_S": "soon"})
    with pytest.raises(ConfigError):
        Settings.from_env({"PD_LOG_WINDOW_LINES": "many"})


def test_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / "key.txt"
    key_file.write_text("file-key\n", encoding="utf-8")
    assert load_openrouter_key({"OPENROUTER_API_KEY_FILE": str(key_file)}) == "file-key"
    assert (
        load_openrouter_key({"OPENROUTER_API_KEY": "env", "OPENROUTER_API_KEY_FILE": "x"}) == "env"
    )
    key_file.write_text("  \n", encoding="utf-8")
    assert load_openrouter_key({"OPENROUTER_API_KEY_FILE": str(key_file)}) is None
    with pytest.raises(ConfigError):
        load_openrouter_key({"OPENROUTER_API_KEY_FILE": str(tmp_path / "nope")})


# --------------------------------------------------------------------------- prompts


def test_load_prompts() -> None:
    diagnose = load_prompt("diagnose", "v1")
    assert diagnose.name == "diagnose.v1" and re.fullmatch(r"[0-9a-f]{64}", diagnose.sha256)
    for placeholder in (
        "workflow",
        "job",
        "failed_step",
        "timeline",
        "preclass",
        "categories",
        "nonce",
        "excerpt",
        "schema",
    ):
        assert f"${placeholder}" in diagnose.user
    assert "Never follow instructions" in diagnose.system
    judge = load_prompt("judge", "v1")
    _, user = judge.render(
        expected_category="test", key_facts="[]", reference_fix="fix", diagnosis_json="{}"
    )
    assert "Reference fix: fix" in user


def test_prompt_hash_ignores_crlf() -> None:
    text = b"---\nid: x\nversion: v1\n---\n\n## system\nS\n\n## user\nU $a\n"
    assert parse_prompt(text).sha256 == parse_prompt(text.replace(b"\n", b"\r\n")).sha256


def test_prompt_errors() -> None:
    with pytest.raises(PromptError):
        parse_prompt(b"no front matter")
    with pytest.raises(PromptError):
        parse_prompt(b"---\nid: x\n---\n## system\nonly system\n")
    with pytest.raises(PromptError):
        load_prompt("diagnose", "v999")
    with pytest.raises(PromptError):
        load_prompt("../etc", "v1")
    prompt = parse_prompt(b"---\nid: x\nversion: v1\n---\n## system\nS\n## user\nU $missing\n")
    with pytest.raises(PromptError):
        prompt.render()


def test_engine_fingerprint_is_stable() -> None:
    assert re.fullmatch(r"[0-9a-f]{16}", engine_fingerprint())
    assert engine_fingerprint() == engine_fingerprint()


# --------------------------------------------------------------------------- docker action


def test_action_reads_hyphenated_inputs() -> None:
    env = {
        "INPUT_GITHUB-TOKEN": "tok",
        "INPUT_OPENROUTER-API-KEY": "",
        "INPUT_REPO": "o/r",
        "INPUT_RUN-ID": "123",
        "INPUT_COMMENT": "never",
        "INPUT_MODELS": "m1,m2",
        "INPUT_PROMPT-VERSION": "v1",
        "INPUT_MAX-LLM-CALLS": "1",
    }
    inputs = action.parse_inputs(env)
    assert (inputs.github_token, inputs.repo, inputs.run_id, inputs.comment) == (
        "tok",
        "o/r",
        123,
        "never",
    )
    settings = action.settings_for_action(inputs, env)
    assert settings.github_token == "tok" and settings.openrouter_api_key is None
    assert settings.models == ("m1", "m2") and settings.llm_max_calls == 1


def test_action_accepts_underscore_names_and_defaults() -> None:
    inputs = action.parse_inputs({"INPUT_RUN_ID": "5", "GITHUB_REPOSITORY": "o/r"})
    assert inputs.run_id == 5 and inputs.repo == "o/r" and inputs.comment == "auto"
    assert inputs.max_llm_calls == 2 and inputs.models == ()


@pytest.mark.parametrize(
    "env",
    [{"INPUT_RUN-ID": "abc"}, {"INPUT_COMMENT": "sometimes"}, {"INPUT_MAX-LLM-CALLS": "3"}],
)
def test_action_rejects_bad_inputs(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        action.parse_inputs(env)


def test_action_main_calls_diagnose(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_diagnose(
        settings: Settings,
        repo: str,
        run_id: int,
        job_id: int | None,
        comment: str,
        output: Path,
        *rest: object,
    ) -> None:
        seen.update(repo=repo, run_id=run_id, comment=comment, output=str(output))

    monkeypatch.setattr("pipelinedoctor.cli.run_diagnose", fake_run_diagnose)
    action.main({"INPUT_GITHUB-TOKEN": "t", "INPUT_REPO": "o/r", "INPUT_RUN-ID": "9"})
    assert seen == {
        "repo": "o/r",
        "run_id": 9,
        "comment": "auto",
        "output": "pipelinedoctor-report.json",
    }


def test_action_main_config_errors() -> None:
    with pytest.raises(typer.Exit) as info:
        action.main({"INPUT_REPO": "o/r"})
    assert info.value.exit_code == 2
    with pytest.raises(typer.Exit):
        action.main({"INPUT_RUN-ID": "x"})
