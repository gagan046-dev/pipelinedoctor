"""Fixture mode: an httpx transport that answers like the GitHub API from files on disk.

Only the HTTP layer is replaced. Caching, ETags, artifact redirects, zip extraction, ledger
parsing and DORA maths all run exactly as in production, which makes fixture mode useful both
for offline viewing (`PD_CONSOLE_FIXTURES=1`) and for tests.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx

BLOB_HOST = "blob.fixtures.invalid"
ARTIFACT_NAME = "pipelinedoctor-diagnosis"


class FixtureTransport(httpx.BaseTransport):
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.requests: list[httpx.Request] = []

    def _load(self, name: str) -> Any:
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "GET":
            return _json(405, {"message": "fixture transport is read-only"})
        if request.url.host == BLOB_HOST:
            return self._blob(request)
        path = request.url.path
        match = re.match(r"^/repos/([^/]+/[^/]+)(/.*)?$", path)
        if not match:
            return _json(404, {"message": "Not Found"})
        repo, rest = match.group(1), match.group(2) or ""
        response = self._route(repo, rest, request)
        return _with_etag(request, response)

    def _route(self, repo: str, rest: str, request: httpx.Request) -> httpx.Response:
        if rest == "":
            return _json(200, {"full_name": repo, "default_branch": "main"})
        if re.fullmatch(r"/actions/workflows/[^/]+/runs", rest):
            return _json(200, self._load("doctor-runs.json"))
        if m := re.fullmatch(r"/actions/runs/(\d+)/artifacts", rest):
            run_id = int(m.group(1))
            artifacts = []
            if (self.dir / "diagnoses" / f"{run_id}.json").exists():
                artifacts.append(
                    {"id": run_id * 10, "name": ARTIFACT_NAME, "expired": False, "size_in_bytes": 1}
                )
            return _json(200, {"total_count": len(artifacts), "artifacts": artifacts})
        if m := re.fullmatch(r"/actions/artifacts/(\d+)/zip", rest):
            location = f"https://{BLOB_HOST}/artifacts/{int(m.group(1)) // 10}.zip?sig=fixture"
            return httpx.Response(302, headers={"location": location})
        if rest == "/contents/ledger.jsonl":
            path = self.dir / "ledger.jsonl"
            if not path.exists():
                return _json(404, {"message": "Not Found"})
            return httpx.Response(200, text=path.read_text(encoding="utf-8"))
        if rest == "/deployments":
            deployments = self._load("deployments.json")
            return _json(
                200, [{k: v for k, v in d.items() if k != "statuses"} for d in deployments]
            )
        if m := re.fullmatch(r"/deployments/(\d+)/statuses", rest):
            for d in self._load("deployments.json"):
                if d["id"] == int(m.group(1)):
                    return _json(200, d["statuses"])
            return _json(404, {"message": "Not Found"})
        if m := re.fullmatch(r"/commits/([0-9a-f]+)", rest):
            for c in self._load("commits.json"):
                if c["sha"] == m.group(1):
                    return _json(200, _commit(c))
            return _json(404, {"message": "Not Found"})
        if m := re.fullmatch(r"/compare/([0-9a-f]+)\.\.\.([0-9a-f]+)", rest):
            shas = [c["sha"] for c in self._load("commits.json")]
            commits = self._load("commits.json")
            try:
                lo, hi = shas.index(m.group(1)), shas.index(m.group(2))
            except ValueError:
                return _json(404, {"message": "Not Found"})
            return _json(200, {"commits": [_commit(c) for c in commits[lo + 1 : hi + 1]]})
        return _json(404, {"message": "Not Found"})

    def _blob(self, request: httpx.Request) -> httpx.Response:
        # Mirrors the real contract: the signed blob URL must be fetched WITHOUT the token.
        if "authorization" in request.headers:
            return httpx.Response(400, text="Authorization header must not reach blob storage")
        m = re.fullmatch(r"/artifacts/(\d+)\.zip", request.url.path)
        if not m:
            return httpx.Response(404)
        run_id = m.group(1)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.write(self.dir / "diagnoses" / f"{run_id}.json", "diagnosis.json")
            excerpt = self.dir / "excerpts" / f"{run_id}.txt"
            if excerpt.exists():
                archive.write(excerpt, "excerpt.txt")
        return httpx.Response(200, content=buffer.getvalue())


def _commit(c: dict[str, str]) -> dict[str, Any]:
    return {"sha": c["sha"], "commit": {"message": c["message"], "committer": {"date": c["date"]}}}


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def _with_etag(request: httpx.Request, response: httpx.Response) -> httpx.Response:
    if response.status_code != 200:
        return response
    etag = '"' + hashlib.sha256(response.content).hexdigest()[:16] + '"'
    if request.headers.get("if-none-match") == etag:
        return httpx.Response(304, headers={"etag": etag})
    response.headers["etag"] = etag
    return response
