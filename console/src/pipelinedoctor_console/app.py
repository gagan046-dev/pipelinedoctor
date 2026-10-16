"""FastAPI application: HTML page shells + JSON API + health endpoints.

The HTML templates never contain data from GitHub. Pages load `/api/*` with JavaScript and
render every log line and every LLM sentence with `textContent`, so a hostile log line such as
`<img src=x onerror=...>` is displayed as text, never parsed as markup.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipelinedoctor_console.dora import DoraService
from pipelinedoctor_console.evals import EvalService
from pipelinedoctor_console.fixtures import FixtureTransport
from pipelinedoctor_console.github import GitHubError, GitHubReader
from pipelinedoctor_console.models import DoraMetrics, EvalList, OccurrenceDetail, OccurrenceList
from pipelinedoctor_console.occurrences import OccurrenceNotFound, OccurrenceService
from pipelinedoctor_console.settings import Settings

HERE = Path(__file__).parent
PROBE_TTL_S = 60
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class _ReadinessProbe:
    """Caches the result of one cheap GitHub call so /readyz cannot burn the rate limit."""

    def __init__(self, gh: GitHubReader) -> None:
        self.gh = gh
        self._at = 0.0
        self._result: tuple[bool, str] = (False, "not probed yet")
        self._lock = threading.Lock()

    def check(self) -> tuple[bool, str]:
        with self._lock:
            if time.monotonic() - self._at < PROBE_TTL_S:
                return self._result
            try:
                self.gh.get_json(self.gh.repo_path, ttl=0)
                self._result = (True, "github reachable")
            except GitHubError as exc:
                self._result = (False, f"github probe failed: {exc.kind}")
            self._at = time.monotonic()
            return self._result


def create_app(settings: Settings | None = None, gh: GitHubReader | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")
    # httpx logs every request URL at INFO. Artifact downloads redirect to signed blob URLs whose
    # query string is a credential, so keep these loggers quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if gh is None:
        transport = FixtureTransport(settings.fixtures_dir) if settings.fixtures_dir else None
        gh = GitHubReader(settings, transport=transport)
    occurrences = OccurrenceService(gh)
    evals = EvalService(gh)
    dora = DoraService(gh)
    probe = _ReadinessProbe(gh)
    started_at = datetime.now(UTC)

    app = FastAPI(
        title="PipelineDoctor Console",
        version=settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.gh = gh
    templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def page(request: Request, template: str, title: str, active: str) -> HTMLResponse:
        context: dict[str, Any] = {
            "title": title,
            "active": active,
            "repo": settings.repo,
            "version": settings.version,
            "color": settings.color,
            "fixture_mode": settings.fixture_mode,
        }
        return templates.TemplateResponse(request, template, context)

    @app.get("/", response_class=HTMLResponse)
    def register_page(request: Request) -> HTMLResponse:
        return page(request, "register.html", "Occurrence register", "register")

    @app.get("/occurrences/{occurrence_id}", response_class=HTMLResponse)
    def report_page(request: Request, occurrence_id: str) -> HTMLResponse:
        # The id is read again by report.js from the URL; it is not echoed into the template.
        return page(request, "report.html", "Investigation report", "register")

    @app.get("/evals", response_class=HTMLResponse)
    def evals_page(request: Request) -> HTMLResponse:
        return page(request, "evals.html", "Eval ledger", "evals")

    @app.get("/api/occurrences")
    def api_occurrences(limit: int = Query(50, ge=1, le=100)) -> OccurrenceList:
        return occurrences.list(limit)

    @app.get("/api/occurrences/{occurrence_id}")
    def api_occurrence(occurrence_id: str) -> OccurrenceDetail:
        try:
            return occurrences.get(occurrence_id)
        except OccurrenceNotFound:
            raise HTTPException(404, "No occurrence with this number.") from None
        except GitHubError as exc:
            raise HTTPException(503, exc.notice()) from None

    @app.get("/api/evals")
    def api_evals() -> EvalList:
        return evals.list()

    @app.get("/api/dora")
    def api_dora(days: int = Query(30, ge=1, le=365)) -> DoraMetrics:
        return dora.metrics(days)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        problems = settings.problems()
        if problems:
            return JSONResponse({"status": "not_ready", "reason": "; ".join(problems)}, 503)
        ok, reason = probe.check()
        return JSONResponse(
            {"status": "ok" if ok else "not_ready", "reason": reason}, 200 if ok else 503
        )

    @app.get("/version")
    def version() -> dict[str, str]:
        return {
            "version": settings.version,
            "git_sha": settings.git_sha,
            "color": settings.color,
            "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> PlainTextResponse:
        # Prometheus text exposition format, written by hand: four gauges do not need a library.
        lines = [
            "# TYPE pd_console_info gauge",
            f'pd_console_info{{version="{settings.version}",color="{settings.color}",'
            f'fixture_mode="{str(settings.fixture_mode).lower()}"}} 1',
        ]
        for stat, value in gh.stats.items():
            lines.append(f"# TYPE pd_console_github_{stat}_total counter")
            lines.append(f"pd_console_github_{stat}_total {value}")
        if gh.rate_limit_remaining is not None:
            lines += [
                "# TYPE pd_console_github_rate_limit_remaining gauge",
                f"pd_console_github_rate_limit_remaining {gh.rate_limit_remaining}",
            ]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> Response:
        detail = getattr(exc, "detail", "Not Found")
        if request.url.path.startswith(("/api/", "/static/")):
            return JSONResponse({"detail": detail}, 404)
        return templates.TemplateResponse(
            request,
            "notfound.html",
            {
                "title": "Not on the register",
                "active": "",
                "repo": settings.repo,
                "version": settings.version,
                "color": settings.color,
                "fixture_mode": settings.fixture_mode,
            },
            status_code=404,
        )

    return app


app = create_app()
