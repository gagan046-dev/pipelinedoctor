from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pipelinedoctor_console.app import create_app
from pipelinedoctor_console.fixtures import FixtureTransport
from pipelinedoctor_console.github import GitHubReader
from pipelinedoctor_console.settings import BUILTIN_FIXTURES, Settings

REPO = "gagan046-dev/pipelinedoctor"
API = "https://api.github.com"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never reach the network: respx or the fixture transport answers instead."""

    real_connect = socket.socket.connect

    def guard(self: socket.socket, address: Any) -> None:
        # Loopback stays allowed: on Windows asyncio builds its event-loop socketpair over it.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return real_connect(self, address)
        raise RuntimeError(f"network access is disabled in tests: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def fixture_settings(cache_dir: Path) -> Settings:
    return Settings(fixtures_dir=BUILTIN_FIXTURES, cache_dir=cache_dir, color="blue")


@pytest.fixture
def token_settings(cache_dir: Path) -> Settings:
    return Settings(token="test-token-value", cache_dir=cache_dir)


@pytest.fixture
def fixture_gh(fixture_settings: Settings) -> Iterator[GitHubReader]:
    gh = GitHubReader(fixture_settings, transport=FixtureTransport(BUILTIN_FIXTURES))
    yield gh
    gh.close()


@pytest.fixture
def client(fixture_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fixture_settings)) as c:
        yield c
