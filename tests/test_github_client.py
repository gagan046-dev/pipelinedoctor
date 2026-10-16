from __future__ import annotations

import httpx
import pytest
import respx

from helpers import API, REPO, fixture_text, load_fixture
from pipelinedoctor.github.client import MAX_LOG_LINES, GitHubClient, GitHubError

BLOB = "https://productionresultssa1.blob.core.windows.net/actions-results/job-logs.txt?sig=abc"


@pytest.fixture
def gh() -> GitHubClient:
    return GitHubClient("test-token")


@respx.mock
def test_get_run_sends_auth_and_api_version(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/repos/{REPO}/actions/runs/1").respond(json=load_fixture("run.json"))
    run = gh.get_run(REPO, 1)
    assert run["name"] == "Demo CI"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


@respx.mock
def test_list_jobs_follows_pagination(gh: GitHubClient) -> None:
    page2 = f"{API}/repositories/1/actions/runs/1/jobs?filter=latest&per_page=100&page=2"
    respx.get(f"{API}/repos/{REPO}/actions/runs/1/jobs").respond(
        json=load_fixture("jobs-page1.json"), headers={"Link": f'<{page2}>; rel="next"'}
    )
    respx.get(page2).respond(json=load_fixture("jobs-page2.json"))
    jobs = gh.list_jobs(REPO, 1)
    assert [j["name"] for j in jobs][-1] == "docker" and len(jobs) == 3


@respx.mock
def test_log_download_drops_authorization_on_redirect(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/{REPO}/actions/jobs/7/logs").respond(302, headers={"Location": BLOB})
    blob = respx.get(BLOB).respond(text=fixture_text("job-log.txt"))
    text = gh.download_job_log(REPO, 7)
    assert "Run python -m pytest" in text
    assert "Authorization" not in blob.calls.last.request.headers


@respx.mock
def test_log_download_keeps_last_lines(gh: GitHubClient) -> None:
    body = "\n".join(f"line {i}" for i in range(MAX_LOG_LINES + 10))
    respx.get(f"{API}/repos/{REPO}/actions/jobs/7/logs").respond(200, text=body)
    lines = gh.download_job_log(REPO, 7).splitlines()
    assert len(lines) == MAX_LOG_LINES and lines[-1] == f"line {MAX_LOG_LINES + 9}"


@respx.mock
def test_blob_error(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/{REPO}/actions/jobs/7/logs").respond(302, headers={"Location": BLOB})
    respx.get(BLOB).respond(403)
    with pytest.raises(GitHubError):
        gh.download_job_log(REPO, 7)


@respx.mock
def test_errors_carry_status_and_message(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/{REPO}/actions/runs/1").respond(404, json={"message": "Not Found"})
    with pytest.raises(GitHubError) as info:
        gh.get_run(REPO, 1)
    assert info.value.status == 404 and "Not Found" in str(info.value)


@respx.mock
def test_transport_errors_become_github_errors(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/{REPO}/actions/runs/1").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(GitHubError) as info:
        gh.get_run(REPO, 1)
    assert info.value.status == 0


def test_pr_from_payload(gh: GitHubClient) -> None:
    pr = gh.find_pull_request(REPO, load_fixture("run.json"))
    assert pr is not None and pr.number == 12
    assert pr.html_url == f"https://github.com/{REPO}/pull/12"


@respx.mock
def test_pr_from_commit_lookup_for_fork_prs(gh: GitHubClient) -> None:
    run = dict(load_fixture("run.json"), pull_requests=[])
    respx.get(f"{API}/repos/{REPO}/commits/{run['head_sha']}/pulls").respond(
        json=load_fixture("commit-pulls.json")
    )
    pr = gh.find_pull_request(REPO, run)
    assert pr is not None and pr.number == 12


@respx.mock
def test_pr_from_head_branch_lookup(gh: GitHubClient) -> None:
    run = dict(load_fixture("run.json"), pull_requests=[])
    respx.get(f"{API}/repos/{REPO}/commits/{run['head_sha']}/pulls").respond(422, json={})
    route = respx.get(f"{API}/repos/{REPO}/pulls").respond(json=load_fixture("commit-pulls.json"))
    pr = gh.find_pull_request(REPO, run)
    assert pr is not None and pr.number == 12
    assert route.calls.last.request.url.params["head"] == "gagan046-dev:break/test-202610161155"


@respx.mock
def test_no_pr_for_push_to_main(gh: GitHubClient) -> None:
    run = dict(load_fixture("run.json"), pull_requests=[], head_branch="main")
    respx.get(f"{API}/repos/{REPO}/commits/{run['head_sha']}/pulls").respond(json=[])
    respx.get(f"{API}/repos/{REPO}/pulls").respond(json=[])
    assert gh.find_pull_request(REPO, run) is None


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
@respx.mock
def test_non_pr_runs_never_comment_on_the_merged_pr(gh: GitHubClient, event: str) -> None:
    merged = [dict(p, state="closed") for p in load_fixture("commit-pulls.json")]
    run = dict(load_fixture("run.json"), event=event, head_branch="main")
    route = respx.get(f"{API}/repos/{REPO}/commits/{run['head_sha']}/pulls").respond(json=merged)
    assert gh.find_pull_request(REPO, run) is None
    assert not route.called


@respx.mock
def test_closed_or_moved_prs_are_ignored(gh: GitHubClient) -> None:
    pulls = load_fixture("commit-pulls.json")
    closed = [dict(p, state="closed") for p in pulls]
    moved = [dict(p, head=dict(p["head"], sha="0" * 40)) for p in pulls]
    run = dict(load_fixture("run.json"), pull_requests=[])
    respx.get(f"{API}/repos/{REPO}/commits/{run['head_sha']}/pulls").respond(json=closed)
    respx.get(f"{API}/repos/{REPO}/pulls").respond(json=moved)
    assert gh.find_pull_request(REPO, run) is None


@respx.mock
def test_huge_log_keeps_its_tail(gh: GitHubClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipelinedoctor.github.client.MAX_LOG_BYTES", 1000)
    body = "\n".join(f"line {i:05d}" for i in range(500)) + "\n##[error]the real error"
    respx.get(f"{API}/repos/{REPO}/actions/jobs/7/logs").respond(302, headers={"Location": BLOB})
    respx.get(BLOB).respond(content=body.encode())
    text = gh.download_job_log(REPO, 7)
    assert text.endswith("##[error]the real error")
    assert "line 00000" not in text
    assert len(text.encode()) <= 1000
    assert gh.last_log_truncated


@respx.mock
def test_comment_crud(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/{REPO}/issues/12/comments").respond(json=load_fixture("comments.json"))
    respx.post(f"{API}/repos/{REPO}/issues/12/comments").respond(201, json={"id": 5})
    respx.patch(f"{API}/repos/{REPO}/issues/comments/5").respond(json={"id": 5, "body": "x"})
    respx.get(f"{API}/repos/{REPO}/issues/comments/5").respond(json={"id": 5, "body": "x"})
    respx.delete(f"{API}/repos/{REPO}/issues/comments/5").respond(204)
    assert len(gh.list_issue_comments(REPO, 12)) == 3
    assert gh.create_comment(REPO, 12, "x")["id"] == 5
    assert gh.update_comment(REPO, 5, "x")["body"] == "x"
    assert gh.get_comment(REPO, 5)["id"] == 5
    gh.delete_comment(REPO, 5)


@respx.mock
def test_own_login_is_none_for_installation_tokens(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/user").respond(403, json={"message": "Resource not accessible"})
    assert gh.own_login() is None
    assert gh.own_login() is None
    assert route.call_count == 1  # looked up once, then cached


@respx.mock
def test_own_login_for_user_tokens() -> None:
    respx.get(f"{API}/user").respond(json={"login": "gagan046-dev"})
    assert GitHubClient("t").own_login() == "gagan046-dev"
