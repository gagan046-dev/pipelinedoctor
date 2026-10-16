"""Post-deploy smoke test: `python -m pipelinedoctor_console.smoke --base-url URL [...]`.

The same code runs in staging and production (inside the release image, on the compose network),
so a green smoke test means the exact image that serves traffic works. Exit code = number of
failed checks.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _check(name: str, fn: Callable[[], str]) -> Check:
    try:
        return Check(name, True, fn())
    except AssertionError as exc:
        return Check(name, False, str(exc) or "assertion failed")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return Check(name, False, f"{type(exc).__name__}: {exc}")


def run_checks(
    client: httpx.Client,
    expect_version: str | None = None,
    expect_color: str | None = None,
    require_github: bool = False,
) -> list[Check]:
    def healthz() -> str:
        r = client.get("/healthz")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert r.json().get("status") == "ok", "status is not ok"
        return "200 ok"

    def version() -> str:
        r = client.get("/version")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        data = r.json()
        if expect_version is not None:
            want = expect_version.removeprefix("v")
            assert data.get("version") == want, f"version {data.get('version')!r} != {want!r}"
        if expect_color is not None:
            got = data.get("color")
            assert got == expect_color, f"color {got!r} != {expect_color!r}"
        return f"version={data.get('version')} color={data.get('color')}"

    def home() -> str:
        r = client.get("/")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert r.headers.get("content-type", "").startswith("text/html"), "not text/html"
        assert "Occurrence register" in r.text, "page heading missing"
        return "register page renders"

    def occurrences() -> str:
        r = client.get("/api/occurrences")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        items = r.json()["items"]
        assert isinstance(items, list), "items is not a list"
        return f"{len(items)} items"

    def dora() -> str:
        r = client.get("/api/dora")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert "deployment_frequency_per_week" in r.json(), "key missing"
        return "metrics present"

    def css() -> str:
        r = client.get("/static/css/console.css")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        return f"{len(r.content)} bytes"

    def csp() -> str:
        r = client.get("/healthz")
        policy = r.headers.get("content-security-policy", "")
        assert "default-src 'self'" in policy, "Content-Security-Policy missing"
        return "present"

    def readyz() -> str:
        r = client.get("/readyz")
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        return "ready"

    checks = [
        ("healthz", healthz),
        ("version", version),
        ("register page", home),
        ("api occurrences", occurrences),
        ("api dora", dora),
        ("stylesheet", css),
        ("csp header", csp),
    ]
    if require_github:
        checks.append(("readyz", readyz))
    return [_check(name, fn) for name, fn in checks]


def main(argv: Sequence[str] | None = None, client: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipelinedoctor_console.smoke")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expect-version")
    parser.add_argument("--expect-color")
    parser.add_argument("--require-github", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    own_client = client is None
    client = client or httpx.Client(base_url=args.base_url, timeout=args.timeout)
    try:
        results = run_checks(client, args.expect_version, args.expect_color, args.require_github)
    finally:
        if own_client:
            client.close()
    for check in results:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name:<16} {check.detail}")
    failed = sum(not c.ok for c in results)
    print(f"smoke: {len(results) - failed}/{len(results)} checks passed")
    return failed


if __name__ == "__main__":
    sys.exit(main())
