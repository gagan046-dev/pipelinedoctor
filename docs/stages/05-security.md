# Stage 05 - Security

**Tag:** `stage-05` · **Files:** `security.yml`, `codeql.yml`, `.github/dependabot.yml`,
`.gitleaks.toml`, `trivy.yaml`, `.trivyignore.yaml`, `scripts/check_workflows.py`, `docs/security.md`

## The layers

| Layer | Tool | Fails the PR when |
|---|---|---|
| Secrets in git history | gitleaks (`secret-scan`, required) | a token/key pattern appears in any commit of the PR |
| Secrets at push time | GitHub push protection (repo setting) | you try to push a known token format |
| Vulnerable new dependencies | `dependency-review` (required) | a PR adds a dependency with a HIGH+ advisory |
| Vulnerable files / images | Trivy fs + image | a **fixable CRITICAL** is found (everything else -> SARIF in the Security tab) |
| Code flaws | CodeQL (python, javascript-typescript, **actions**) | results appear in Security -> Code scanning |
| Workflow mistakes | `workflow-policy` (required): actionlint, zizmor, `scripts/check_workflows.py` | invalid syntax/expressions, a medium+ zizmor finding, or a policy rule below is broken |
| Outdated pins | Dependabot (uv, pip, docker, npm, github-actions) | - (opens grouped PRs weekly, 14-day cooldown) |

## Least privilege

Every workflow starts with `permissions: {}`; each job lists only what it needs, with a comment
saying why (`security-events: write # upload SARIF`). The repository default token is read-only
(`scripts/repo-settings.ps1`).

## Pin actions by commit SHA

Tags are mutable: whoever controls `some/action` can move `v4` to malicious code. A 40-hex
commit SHA cannot be moved. Popular CI tools and Python packages - including scanners and LLM
SDKs - have shipped compromised releases in the past, which is why this repo pins SHAs, locks
hashes in `uv.lock`, and only adopts releases older than two weeks.

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

Verify a pin yourself:

```powershell
gh api repos/actions/checkout/git/ref/tags/v7.0.1 --jq .object
# if .type is "tag" (annotated tag), dereference it to the commit:
gh api repos/<owner>/<repo>/git/tags/<sha-from-above> --jq .object.sha
gh api repos/actions/checkout/releases/tags/v7.0.1 --jq .published_at   # two-week rule
```

Dependabot updates the SHA **and** the `# vX.Y.Z` comment together.

## The workflow policy (`scripts/check_workflows.py`)

| Rule | Policy |
|---|---|
| R1 | top-level `permissions: {}`, every job has its own `permissions` |
| R2 | third-party `uses:` pinned to a 40-hex SHA with a `# vX.Y.Z` comment |
| R3 | `self-hosted` only in `deploy.yml/production` and `rollback.yml/rollback`, with `environment: production`, and never reachable from `pull_request`, `pull_request_target`, `issue_comment`, `workflow_run` |
| R4 | no `pull_request_target` |
| R5 | every job with steps has `timeout-minutes` |
| R6 | no `${{ github.event.* }}` / `${{ github.head_ref }}` inside `run:` |
| R7 | `actions/checkout` with `persist-credentials: false` (only `nightly-evals.yml/record` pushes) |
| R8 | `workflow_run` workflows never check out the triggering run's head |

```powershell
uv run python scripts/check_workflows.py
uv run pytest -o addopts= -q scripts/tests
```

## Linters you can run locally (the same ones `workflow-policy` runs)

```powershell
# actionlint (syntax, expressions, shellcheck on run: blocks)
docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
# zizmor (workflow security audit)
uvx zizmor@1.30.1 .
```

## SARIF

Trivy writes SARIF (Static Analysis Results Interchange Format); `codeql-action/upload-sarif`
sends it to code scanning with a `category` per scan, so results of different scans do not
overwrite each other. Fork PR tokens cannot upload, hence `continue-on-error` only for forks.

```powershell
gh api repos/gagan046-dev/pipelinedoctor/code-scanning/alerts --jq '.[] | {rule: .rule.id, tool: .tool.name, state}'
```

Threat model: [docs/security.md](../security.md).
