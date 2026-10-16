"""Console settings, read once from the environment (ARCHITECTURE.md section 4.3)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipelinedoctor_console import __version__

DEFAULT_REPO = "gagan046-dev/pipelinedoctor"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
UNAUTHENTICATED_TTL_S = 900
BUILTIN_FIXTURES = Path(__file__).parent / "fixture_data"
# Fixture data is a frozen snapshot; DORA windows are measured from this instant in fixture mode.
FIXTURE_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

# Order matters: the console-specific variable wins, then the generic ones.
TOKEN_VARIABLES = ("PD_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")


def _read_token(env: Mapping[str, str]) -> str:
    """Return the first token found in VAR or VAR_FILE (a path, e.g. a Docker secret)."""
    for name in TOKEN_VARIABLES:
        value = env.get(name, "").strip()
        if value:
            return value
        path = env.get(f"{name}_FILE", "").strip()
        if path:
            try:
                value = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                return value
    return ""


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    repo: str = DEFAULT_REPO
    # repr=False keeps the token out of logs, tracebacks and debug output.
    token: str = field(default="", repr=False)
    api_url: str = "https://api.github.com"
    cache_dir: Path = Path("/var/cache/pd")
    cache_ttl_s: int = 300
    color: str = "none"
    version: str = __version__
    git_sha: str = "unknown"
    doctor_workflow: str = "doctor.yml"
    eval_history_branch: str = "eval-history"
    prod_environment: str = "production"
    log_level: str = "INFO"
    fixtures_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        token = _read_token(env)
        fixtures = env.get("PD_CONSOLE_FIXTURES", "").strip()
        fixtures_dir: Path | None = None
        if fixtures.lower() in {"1", "true", "yes", "builtin"}:
            fixtures_dir = BUILTIN_FIXTURES
        elif fixtures and fixtures.lower() not in {"0", "false", "no"}:
            fixtures_dir = Path(fixtures)
        default_ttl = 300 if token else UNAUTHENTICATED_TTL_S
        return cls(
            repo=env.get("PD_CONSOLE_REPO", "").strip() or DEFAULT_REPO,
            token=token,
            api_url=(env.get("GITHUB_API_URL", "").strip() or "https://api.github.com").rstrip("/"),
            cache_dir=Path(env.get("PD_CONSOLE_CACHE_DIR", "").strip() or "/var/cache/pd"),
            cache_ttl_s=max(_int(env, "PD_CONSOLE_CACHE_TTL_S", default_ttl), 0),
            color=env.get("PD_CONSOLE_COLOR", "").strip() or "none",
            version=env.get("PD_CONSOLE_VERSION", "").strip() or __version__,
            git_sha=env.get("PD_GIT_SHA", "").strip() or "unknown",
            doctor_workflow=env.get("PD_DOCTOR_WORKFLOW", "").strip() or "doctor.yml",
            eval_history_branch=env.get("PD_EVAL_HISTORY_BRANCH", "").strip() or "eval-history",
            prod_environment=env.get("PD_PROD_ENVIRONMENT", "").strip() or "production",
            log_level=env.get("PD_LOG_LEVEL", "").strip().upper() or "INFO",
            fixtures_dir=fixtures_dir,
        )

    @property
    def fixture_mode(self) -> bool:
        return self.fixtures_dir is not None

    @property
    def authenticated(self) -> bool:
        return bool(self.token) or self.fixture_mode

    def now(self) -> datetime:
        return FIXTURE_NOW if self.fixture_mode else datetime.now(UTC)

    def problems(self) -> list[str]:
        """Configuration errors that make the console unable to work (used by /readyz)."""
        problems = []
        if not REPO_PATTERN.match(self.repo):
            problems.append(f"PD_CONSOLE_REPO is not owner/name: {self.repo!r}")
        if not self.api_url.startswith(("https://", "http://")):
            problems.append("GITHUB_API_URL must be an http(s) URL")
        if self.fixtures_dir is not None and not self.fixtures_dir.is_dir():
            problems.append(f"PD_CONSOLE_FIXTURES directory not found: {self.fixtures_dir}")
        return problems
