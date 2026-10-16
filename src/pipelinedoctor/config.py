"""Runtime settings, read once from environment variables (see ARCHITECTURE.md §4.3)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_MODELS: tuple[str, ...] = (
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
)
HARD_MAX_LLM_CALLS = 2
DEFAULT_DIAGNOSE_REQUEST_BUDGET = 2
DEFAULT_EVAL_REQUEST_BUDGET = {"pr": 12, "all": 45}


class ConfigError(Exception):
    """Raised for missing or invalid configuration (CLI exit code 2)."""


def load_openrouter_key(env: Mapping[str, str] | None = None) -> str | None:
    """Return the key from OPENROUTER_API_KEY, or from the file named by OPENROUTER_API_KEY_FILE.

    The key is never logged. A missing key is not an error: the engine degrades to rules.
    """
    env = os.environ if env is None else env
    value = env.get("OPENROUTER_API_KEY", "").strip()
    if value:
        return value
    key_file = env.get("OPENROUTER_API_KEY_FILE", "").strip()
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            raise ConfigError(f"OPENROUTER_API_KEY_FILE points to a missing file: {path}")
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    # repr=False keeps secrets out of tracebacks and debug prints.
    openrouter_api_key: str | None = field(default=None, repr=False)
    github_token: str | None = field(default=None, repr=False)
    github_api_url: str = "https://api.github.com"
    github_server_url: str = "https://github.com"
    repository: str | None = None
    run_number: int | None = None
    in_actions: bool = False
    step_summary_path: str | None = None
    github_output_path: str | None = None
    models: tuple[str, ...] = DEFAULT_MODELS
    prompt_version: str = "v1"
    llm_timeout_s: float = 60.0
    llm_max_calls: int = HARD_MAX_LLM_CALLS
    max_llm_requests: int | None = None
    min_request_interval_s: float = 3.5
    log_window_lines: int = 40
    max_excerpt_lines: int = 400
    disable_llm: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        models_raw = env.get("PD_MODELS", "").strip()
        models = (
            tuple(m.strip() for m in models_raw.split(",") if m.strip())
            if models_raw
            else DEFAULT_MODELS
        )
        max_requests_raw = env.get("PD_MAX_LLM_REQUESTS", "").strip()
        run_number_raw = env.get("GITHUB_RUN_NUMBER", "").strip()
        return cls(
            openrouter_api_key=load_openrouter_key(env),
            github_token=(env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "").strip() or None,
            github_api_url=(env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/"),
            github_server_url=(env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/"),
            repository=env.get("GITHUB_REPOSITORY") or None,
            run_number=int(run_number_raw) if run_number_raw.isdigit() else None,
            in_actions=env.get("GITHUB_ACTIONS", "") == "true",
            step_summary_path=env.get("GITHUB_STEP_SUMMARY") or None,
            github_output_path=env.get("GITHUB_OUTPUT") or None,
            models=models,
            prompt_version=env.get("PD_PROMPT_VERSION", "").strip() or "v1",
            llm_timeout_s=_float(env, "PD_LLM_TIMEOUT_S", 60.0),
            llm_max_calls=max(1, min(_int(env, "PD_LLM_MAX_CALLS", 2), HARD_MAX_LLM_CALLS)),
            max_llm_requests=_int(env, "PD_MAX_LLM_REQUESTS", 0) if max_requests_raw else None,
            min_request_interval_s=_float(env, "PD_MIN_REQUEST_INTERVAL_S", 3.5),
            log_window_lines=_int(env, "PD_LOG_WINDOW_LINES", 40),
            max_excerpt_lines=_int(env, "PD_MAX_EXCERPT_LINES", 400),
            disable_llm=env.get("PD_DISABLE_LLM", "0").strip() == "1",
            log_level=env.get("PD_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )

    def with_overrides(self, **changes: object) -> Settings:
        return replace(self, **changes)  # type: ignore[arg-type]

    def request_budget(self, default: int) -> int:
        return self.max_llm_requests if self.max_llm_requests is not None else default

    def secret_values(self) -> list[str]:
        """Exact secret values that redaction must remove even if no pattern matches them."""
        return [s for s in (self.openrouter_api_key, self.github_token) if s]
