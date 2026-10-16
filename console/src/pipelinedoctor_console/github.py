"""Read-only GitHub REST client with a TTL cache, ETag revalidation and stale-on-error fallback.

Why the cache matters: unauthenticated callers get 60 requests per hour, authenticated ones 5000.
A conditional request (`If-None-Match`) answered with `304 Not Modified` does not count against
the authenticated limit, so revalidating with ETags is almost free.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from pipelinedoctor_console import __version__
from pipelinedoctor_console.settings import Settings

log = logging.getLogger(__name__)

ErrorKind = Literal["rate_limited", "unauthorized", "not_found", "network", "http"]
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_MEMBER_BYTES = 5 * 1024 * 1024


class GitHubError(Exception):
    def __init__(self, kind: ErrorKind, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.reset_at = reset_at

    def notice(self) -> str:
        if self.kind == "rate_limited":
            when = f" It resets at {self.reset_at:%H:%M} UTC." if self.reset_at else ""
            return f"GitHub API rate limit reached.{when}"
        if self.kind == "unauthorized":
            return "GitHub rejected the console's token (401). Check PD_GITHUB_TOKEN."
        if self.kind == "network":
            return "GitHub API unreachable."
        return f"GitHub API error: {self}"


@dataclass
class _Entry:
    body: Any
    etag: str | None
    stored_at: float


@dataclass
class Fetched:
    """A response body plus whether it came from cache because GitHub was unavailable."""

    data: Any
    stale_because: GitHubError | None = None


class GitHubReader:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"pipelinedoctor-console/{__version__}",
        }
        if settings.token:
            headers["Authorization"] = f"Bearer {settings.token}"
        self._client = httpx.Client(
            base_url=settings.api_url,
            headers=headers,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        )
        # Blob-storage downloads get their own client WITHOUT the Authorization header (see below).
        self._blob_client = httpx.Client(timeout=30.0, follow_redirects=True, transport=transport)
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "cache_hits": 0, "not_modified": 0, "errors": 0}
        self.rate_limit_remaining: int | None = None
        self._artifact_dir = settings.cache_dir / "artifacts"
        self._disk_ok = True

    def close(self) -> None:
        self._client.close()
        self._blob_client.close()

    @property
    def repo_path(self) -> str:
        return f"/repos/{self.settings.repo}"

    # --- core request path ------------------------------------------------------------

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, ttl: int | None = None
    ) -> Fetched:
        return self._get(path, params, ttl, raw=False)

    def get_text(
        self, path: str, params: dict[str, Any] | None = None, ttl: int | None = None
    ) -> Fetched:
        return self._get(path, params, ttl, raw=True)

    def _get(self, path: str, params: dict[str, Any] | None, ttl: int | None, raw: bool) -> Fetched:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        ttl = self.settings.cache_ttl_s if ttl is None else ttl
        with self._lock:
            entry = self._cache.get(key)
        if entry and time.monotonic() - entry.stored_at < ttl:
            self.stats["cache_hits"] += 1
            return Fetched(entry.body)

        headers = {}
        if raw:
            headers["Accept"] = "application/vnd.github.raw+json"
        if entry and entry.etag:
            headers["If-None-Match"] = entry.etag
        try:
            response = self._request(path, params, headers)
        except GitHubError as exc:
            self.stats["errors"] += 1
            if entry is not None and exc.kind != "not_found":
                log.warning("GitHub %s for %s, serving stale cache", exc.kind, path)
                return Fetched(entry.body, stale_because=exc)
            raise

        if response.status_code == 304 and entry is not None:
            self.stats["not_modified"] += 1
            body = entry.body
        else:
            body = response.text if raw else response.json()
        with self._lock:
            self._cache[key] = _Entry(body, response.headers.get("etag"), time.monotonic())
        return Fetched(body)

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None,
        headers: dict[str, str],
        ok: tuple[int, ...] = (200, 304),
    ) -> httpx.Response:
        self.stats["requests"] += 1
        try:
            response = self._client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubError("network", type(exc).__name__) from exc
        self._note_rate_limit(response)
        if response.status_code in ok:
            return response
        raise self._error_for(response)

    def _note_rate_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

    @staticmethod
    def _error_for(response: httpx.Response) -> GitHubError:
        status = response.status_code
        reset = response.headers.get("x-ratelimit-reset")
        reset_at = datetime.fromtimestamp(int(reset), UTC) if reset and reset.isdigit() else None
        # GitHub signals an exhausted primary limit with 403 + remaining=0, secondary limits
        # with 403/429 and Retry-After.
        if status == 429 or (
            status == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in response.headers
            )
        ):
            return GitHubError("rate_limited", f"HTTP {status}", reset_at)
        if status == 401:
            return GitHubError("unauthorized", "HTTP 401")
        if status == 404:
            return GitHubError("not_found", "HTTP 404")
        return GitHubError("http", f"HTTP {status}")

    # --- artifacts --------------------------------------------------------------------

    def read_artifact_files(self, artifact_id: int, names: tuple[str, ...]) -> dict[str, str]:
        """Return the named text files from an artifact zip. Artifacts are immutable, so the
        extracted files are cached on disk by artifact id and never fetched twice."""
        cached = self._read_artifact_cache(artifact_id)
        if cached is not None:
            return cached
        url = f"{self.repo_path}/actions/artifacts/{artifact_id}/zip"
        response = self._request(url, None, {}, ok=(302,))
        location = response.headers.get("location")
        if not location:
            raise GitHubError("http", "artifact download did not redirect")
        # The API answers with a short-lived signed blob-storage URL. The signature IS the
        # credential, so the GitHub token must not be sent to that other host.
        try:
            blob = self._blob_client.get(str(location))
        except httpx.HTTPError as exc:
            raise GitHubError("network", type(exc).__name__) from exc
        if blob.status_code != 200:
            raise GitHubError("http", f"artifact blob HTTP {blob.status_code}")
        if len(blob.content) > MAX_ARTIFACT_BYTES:
            raise GitHubError("http", "artifact too large")
        files = _extract(blob.content, names)
        self._write_artifact_cache(artifact_id, files)
        return files

    def _read_artifact_cache(self, artifact_id: int) -> dict[str, str] | None:
        path = self._artifact_dir / f"{artifact_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else None

    def _write_artifact_cache(self, artifact_id: int, files: dict[str, str]) -> None:
        if not self._disk_ok:
            return
        try:
            self._artifact_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._artifact_dir / f"{artifact_id}.json.tmp"
            tmp.write_text(json.dumps(files), encoding="utf-8")
            tmp.replace(self._artifact_dir / f"{artifact_id}.json")
        except OSError:
            # Not fatal: a read-only or missing cache dir only costs extra downloads.
            log.warning("artifact cache dir %s not writable; caching disabled", self._artifact_dir)
            self._disk_ok = False


def _extract(content: bytes, names: tuple[str, ...]) -> dict[str, str]:
    files: dict[str, str] = {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise GitHubError("http", "artifact is not a zip file") from exc
    with archive:
        for info in archive.infolist():
            name = Path(info.filename).name
            if name not in names:
                continue
            # Read at most MAX+1 bytes: a zip bomb cannot make us inflate more than that.
            with archive.open(info) as member:
                data = member.read(MAX_MEMBER_BYTES + 1)
            if len(data) <= MAX_MEMBER_BYTES:
                files[name] = data.decode("utf-8", errors="replace")
    return files
