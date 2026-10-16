from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from pipelinedoctor_console import __version__, smoke


def test_smoke_passes_against_app(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke.main(
        [
            "--base-url",
            "http://testserver",
            "--expect-version",
            f"v{__version__}",
            "--expect-color",
            "blue",
            "--require-github",
        ],
        client=client,
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "smoke: 8/8 checks passed" in out


def test_smoke_counts_failures(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke.main(
        ["--base-url", "http://testserver", "--expect-version", "9.9.9", "--expect-color", "green"],
        client=client,
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  version" in out


def test_smoke_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    down = httpx.Client(base_url="http://console:8000", transport=httpx.MockTransport(refuse))
    assert smoke.main(["--base-url", "http://console:8000"], client=down) == 7
    assert "ConnectError" in capsys.readouterr().out
