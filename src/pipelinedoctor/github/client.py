"""Minimal GitHub REST client (httpx) with only the calls PipelineDoctor needs."""

from __future__ import annotations

import contextlib
import logging
from collections import deque
from typing import Any

import httpx

from pipelinedoctor.schemas import PullRequestRef

log = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
MAX_LOG_BYTES = 20 * 1024 * 1024
MAX_LOG_LINES = 50_000


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        api_url: str = "https://api.github.com",
        server_url: str = "https://github.com",
        http: httpx.Client | None = None,
    ) -> None:
        self._token = token
        self.api_url = api_url.rstrip("/")
        self.server_url = server_url.rstrip("/")
        # No Authorization in the client defaults: it is added per request to API URLs only,
        # so it can never leak to the blob-storage host a log download redirects to.
        self._http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        self._own_login: str | bool | None = False  # False = not looked up yet
        self.last_log_truncated = False

    # ------------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "pipelinedoctor",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.api_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(
                method, self._url(path), headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise GitHubError(0, f"{method} {path}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            message = response.text[:300]
            with contextlib.suppress(ValueError):
                message = str(response.json().get("message", message))
            raise GitHubError(response.status_code, f"{method} {path}: {message}")
        return response

    def get_json(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None).json()

    def paginate(self, path: str, key: str | None = None, **params: Any) -> list[dict[str, Any]]:
        """Follow `Link: rel="next"` headers. `key` selects the list inside wrapped responses."""
        items: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": 100, **params}
        while url:
            response = self.request("GET", url, params=query)
            data = response.json()
            items.extend(data[key] if key else data)
            url = response.links.get("next", {}).get("url")
            query = None  # the next URL already carries the query string
        return items

    # ------------------------------------------------------------------ runs and logs

    def get_run(self, repo: str, run_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self.get_json(f"repos/{repo}/actions/runs/{run_id}")
        return data

    def list_jobs(self, repo: str, run_id: int) -> list[dict[str, Any]]:
        return self.paginate(
            f"repos/{repo}/actions/runs/{run_id}/jobs", key="jobs", filter="latest"
        )

    def download_job_log(self, repo: str, job_id: int) -> str:
        """GitHub answers 302 with a short-lived signed URL; fetch that WITHOUT our token.

        A huge log keeps its LAST MAX_LOG_BYTES: the failing step and the final error lines are
        at the end of a CI log, so that is the part a diagnosis cannot lose.
        """
        self.last_log_truncated = False
        response = self.request(
            "GET", f"repos/{repo}/actions/jobs/{job_id}/logs", follow_redirects=False
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers["location"]
            with self._http.stream(
                "GET", location, headers={"User-Agent": "pipelinedoctor"}
            ) as blob:
                if blob.status_code >= 400:
                    raise GitHubError(blob.status_code, "log blob download failed")
                chunks: deque[bytes] = deque()
                size = 0
                for chunk in blob.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    while size - len(chunks[0]) >= MAX_LOG_BYTES:
                        size -= len(chunks.popleft())
                        self.last_log_truncated = True
                body = b"".join(chunks)
        else:
            body = response.content
        if len(body) > MAX_LOG_BYTES:
            body = body[-MAX_LOG_BYTES:]
            self.last_log_truncated = True
        lines = body.decode("utf-8", errors="replace").splitlines()
        if self.last_log_truncated:
            lines = lines[1:]  # the first line was probably cut in the middle
        if len(lines) > MAX_LOG_LINES:
            lines = lines[-MAX_LOG_LINES:]
            self.last_log_truncated = True
        if self.last_log_truncated:
            log.warning("job log longer than the limit: kept only its last part")
        return "\n".join(lines)

    # ------------------------------------------------------------------ pull requests

    def _pr_ref(self, number: int, repo: str) -> PullRequestRef:
        return PullRequestRef(number=number, html_url=f"{self.server_url}/{repo}/pull/{number}")

    def find_pull_request(self, repo: str, run: dict[str, Any]) -> PullRequestRef | None:
        """The OPEN pull request whose head is exactly this run's commit, or None.

        Only pull_request runs belong to a PR. A push to main, a schedule or a dispatch on main
        must not land on the PR that was squash-merged into that commit last week.
        Order: payload `pull_requests` -> commits/{sha}/pulls -> pulls?head=owner:branch
        (`pull_requests` is empty for PRs from forks, hence the two fallbacks).
        """
        if run.get("event") not in ("pull_request", "pull_request_target"):
            return None
        head_sha = run.get("head_sha")
        prs = run.get("pull_requests") or []
        if prs:
            return self._pr_ref(int(prs[0]["number"]), repo)
        if head_sha:
            try:
                pulls = self.get_json(f"repos/{repo}/commits/{head_sha}/pulls")
            except GitHubError as exc:
                log.info("commit->pulls lookup failed: %s", exc)
                pulls = []
            matching = [p for p in pulls if _is_open_pr_for(p, head_sha)]
            if matching:
                return self._pr_ref(int(matching[0]["number"]), repo)
        head_repo = run.get("head_repository") or {}
        owner = (head_repo.get("owner") or {}).get("login")
        branch = run.get("head_branch")
        if owner and branch and head_sha:
            pulls = self.get_json(f"repos/{repo}/pulls", state="open", head=f"{owner}:{branch}")
            matching = [p for p in pulls if _is_open_pr_for(p, head_sha)]
            if matching:
                return self._pr_ref(int(matching[0]["number"]), repo)
        return None

    # ------------------------------------------------------------------ comments

    def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self.paginate(f"repos/{repo}/issues/{number}/comments")

    def get_comment(self, repo: str, comment_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self.get_json(f"repos/{repo}/issues/comments/{comment_id}")
        return data

    def create_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        data: dict[str, Any] = self.request(
            "POST", f"repos/{repo}/issues/{number}/comments", json={"body": body}
        ).json()
        return data

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        data: dict[str, Any] = self.request(
            "PATCH", f"repos/{repo}/issues/comments/{comment_id}", json={"body": body}
        ).json()
        return data

    def delete_comment(self, repo: str, comment_id: int) -> None:
        self.request("DELETE", f"repos/{repo}/issues/comments/{comment_id}")

    def own_login(self) -> str | None:
        """Login of the token owner. Installation tokens (GITHUB_TOKEN) get 403 here -> None."""
        if self._own_login is False:
            try:
                self._own_login = str(self.get_json("user").get("login"))
            except GitHubError:
                self._own_login = None
        return self._own_login if isinstance(self._own_login, str) else None


def _is_open_pr_for(pull: dict[str, Any], head_sha: str) -> bool:
    return pull.get("state") == "open" and (pull.get("head") or {}).get("sha") == head_sha
