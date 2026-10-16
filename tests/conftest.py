from __future__ import annotations

import os
import socket

import pytest

from helpers import fixture_text
from pipelinedoctor.config import Settings


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never reach the network. respx intercepts httpx before any socket."""

    def guard(*args: object, **kwargs: object) -> None:
        raise NetworkBlocked("network access is disabled in unit tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests start without any CI/secret variables from the developer's shell."""
    for name in list(os.environ):
        if name.startswith(("GITHUB_", "PD_", "OPENROUTER_", "INPUT_")) or name in (
            "GH_TOKEN",
            "GITHUB_ACTIONS",
        ):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(min_request_interval_s=0.0)


@pytest.fixture
def job_log() -> str:
    return fixture_text("job-log.txt")
