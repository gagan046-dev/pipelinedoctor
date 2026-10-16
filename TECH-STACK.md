# PipelineDoctor — Tech Stack

**Project type:** AI agent that diagnoses failed GitHub Actions runs and comments on the pull request, delivered by a production-grade CI/CD pipeline built in ten tagged stages (`stage-01` … `stage-10`).
**Level:** Advanced CI/CD + LLMOps teaching repository. Current release: **v0.2.0**.

Learning path: [LEARNING-GUIDE.md](LEARNING-GUIDE.md) · Commands: [COMMANDS.md](COMMANDS.md)

---

## At a glance

| Category | Technologies |
|---|---|
| **CI/CD platform** | GitHub Actions (15 workflow files + 1 composite action), GitHub-hosted runners (ubuntu-latest, windows-latest), self-hosted runner `ghcr.io/actions/actions-runner:2.337.0` + docker compose plugin v5.5.1 |
| **Engine (AI agent)** | Python 3.11 / 3.12 (images: 3.12.14-slim), Typer 0.27.2 (CLI), httpx 0.28.1, Pydantic 2.13.5, PyYAML 6.0.3, LiteLLM 1.103.2 |
| **LLMs** | OpenRouter free tier: NVIDIA Nemotron 3 Super 120B (primary), Nemotron 3 Ultra 550B (fallback); max 2 calls per diagnosis; rule-based fallback without a key |
| **Console (web app)** | FastAPI 0.142.2, Uvicorn 0.54.0, Jinja2 3.1.6, httpx 0.28.1, vanilla JS + CSS, @fontsource IBM Plex Mono / Source Serif 4 5.3.0 (self-hosted fonts, built with Node.js 22.23.3) |
| **Reverse proxy** | nginx 1.30.4 (`nginxinc/nginx-unprivileged`, alpine) — blue-green switch via `nginx -s reload` |
| **Containers** | Docker multi-stage builds, Docker Buildx + QEMU (linux/amd64 + linux/arm64), BuildKit cache mounts, GHA layer cache, Docker Compose (staging, production, runner) |
| **Registry** | GitHub Container Registry (`ghcr.io/gagan046-dev/pipelinedoctor`, `-console`, `-proxy`) |
| **Supply chain** | cosign (keyless, Sigstore, via cosign-installer v4.1.2), syft SBOM SPDX JSON (anchore/sbom-action v0.24.2), SLSA provenance (actions/attest-build-provenance v4.2.2), `gh attestation verify`, SHA-pinned actions, digest-pinned base images |
| **Security scanning** | CodeQL (codeql-action v4.38.2: python, javascript-typescript, actions), Trivy v0.75.0 (trivy-action v0.36.0, fs + image, SARIF), gitleaks 8.30.1 (gitleaks-action v3.0.0), dependency-review-action v5.0.0, actionlint 1.7.12, zizmor 1.30.1, step-security/harden-runner v2.21.1, custom `scripts/check_workflows.py` (rules R1–R8) |
| **Releases** | release-please (release-please-action v5.0.0), Conventional Commits (`pr-title.yml`), CHANGELOG.md |
| **Dependency management** | uv 0.12.21 (`uv.lock`, `--frozen`, `exclude-newer` two-week rule), pip + requirements files (demo), Dependabot (uv, pip, docker, npm, github-actions; 14-day cooldown) |
| **Testing** | pytest 9.1.1, pytest-cov 7.1.0, respx 0.23.1, ruff 0.16.10, mypy 2.4.0 (strict), hatchling 1.32.4 (build) |
| **LLMOps** | 30-case eval dataset, thresholds YAML, result cache (`actions/cache` v6.1.0), budget guard, nightly drift run, LLM-as-judge, ledger on the `eval-history` branch |
| **Demo infra** | PostgreSQL 17.11 (alpine, service container), psycopg[binary] 3.3.6 |
| **Local tooling** | act v0.2.89, GitHub CLI (`gh`), PowerShell 5.1 scripts, Git Bash (deploy scripts), Docker Desktop |

---

## Tech stack by folder

| Folder | What it is | Tech used |
|---|---|---|
| `src/pipelinedoctor/` | The engine + CLI `pipelinedoctor` (diagnose, diagnose-log, eval run/gate/comment) | Python, Typer, httpx (GitHub REST), Pydantic schemas, LiteLLM (OpenRouter), log cleaning/excerpting, secret redaction, rule pre-classifier, nonce-fenced prompts, JSON + evidence validation |
| `src/pipelinedoctor/evals/` | Eval runner, scoring, cache, gate, judge | Python, sha256 cache keys, thresholds, Markdown score tables |
| `prompts/` | Versioned, immutable prompts (`diagnose.v1.md`, `judge.v1.md`) | Markdown, shipped inside the wheel (hatch `force-include`) |
| `evals/` | 30 synthetic CI failure logs + `thresholds.yaml` | YAML, text logs |
| `schemas/` | Contracts: diagnosis report, eval case/result, ledger entry | JSON Schema |
| `tests/` | Engine unit tests | pytest, pytest-cov (85 % floor), respx (HTTP mocks), fake LLM |
| `console/` | "PipelineDoctor Console": occurrence register, investigation reports, eval ledger, DORA | FastAPI, Uvicorn, Jinja2, httpx, vanilla JS/CSS, @fontsource fonts (Node 22 build stage), multi-stage Dockerfile, pytest (75 % floor) |
| `deploy/proxy/` | Front door for blue-green | nginx-unprivileged 1.30.4, `switch-color.sh` |
| `deploy/scripts/`, `deploy/compose.*.yml` | Staging + production deploy | Bash (`bluegreen.sh`, `smoke.sh`, `annotate-deployment.sh`), Docker Compose, GitHub Deployments API |
| `deploy/local/` | Blue-green on the laptop without GitHub | PowerShell + Git Bash, Docker Desktop |
| `runner/` | Self-hosted production runner | actions-runner 2.337.0 image (digest-pinned), compose plugin v5.5.1 (checksum-verified), PowerShell register/start/stop/remove |
| `demo/` | Breakable invoice library + `break/apply.py` (14 failure modes) | Python, pip, psycopg 3.3.6, PostgreSQL 17.11 service container, Dockerfile, ruff/mypy/pytest (90 % floor) |
| `scripts/` | Repo setup and policy | PowerShell (repo settings, environments, ruleset, secret), Python workflow policy checker + tests |
| `.github/workflows/` | 15 workflow files (CI, Demo CI, Container, Security, CodeQL, Evals, Nightly Evals, PR title, Release, Doctor, Deploy, Resolve release, Rollback, Reusable Python CI, Hello) | GitHub Actions YAML, all actions SHA-pinned |
| `.github/actions/setup-python-env/` | Composite action: Python + deps with uv or pip, cached | setup-python v7.0.0, setup-uv v10.2.0 |
| `.github/rulesets/`, `CODEOWNERS`, templates | Branch protection as code, review routing, PR/issue templates | Ruleset JSON (6 required checks), issue forms |
| *(root)* `Dockerfile`, `action.yml` | Engine image and the reusable Docker container action | python:3.12.14-slim (digest), uv 0.12.21 image, OCI labels |
| *(root)* `release-please-config.json`, `.release-please-manifest.json`, `CHANGELOG.md` | Release automation | release-please (`simple`, `bump-minor-pre-major`, `extra-files`) |

---

## CI/CD topics implemented

| # | Topic | Implemented with |
|---|---|---|
| 1 | Events, jobs, steps, outputs, summaries | `hello.yml`: push + `workflow_dispatch` input, `needs`, `$GITHUB_OUTPUT`, `$GITHUB_STEP_SUMMARY` |
| 2 | Quality gates | ruff (GitHub annotations), mypy strict, pytest + coverage floors, `uv sync --frozen`, aggregate `ci-ok` |
| 3 | Branch protection as code | Ruleset `main-protection`: PR + squash only, 6 required checks pinned to the Actions app (`integration_id: 15368`) |
| 4 | Caching | setup-uv cache, setup-python pip cache, BuildKit GHA cache per image, eval result cache with immutable keys |
| 5 | Matrix | OS × Python with `exclude`, `fail-fast: false`, matrix as JSON input (`fromJSON`) |
| 6 | Path filters | Job-level `dorny/paths-filter` for required checks; workflow-level `on.paths` only for non-required Demo CI |
| 7 | Concurrency & timeouts | per-PR groups with cancel-in-progress (never on main), job-level groups for release-please and production; `timeout-minutes` everywhere |
| 8 | Container builds & registry | Multi-stage Dockerfiles, Buildx, metadata-action tags (`sha-`, `edge`, semver, `latest`), GHCR push only from main |
| 9 | Least privilege | `permissions: {}` + per-job scopes with reasons; read-only default token; secrets passed explicitly (no `secrets: inherit`) |
| 10 | Pinning & supply-chain hygiene | 40-hex action SHAs, digest-pinned images, two-week rule (`exclude-newer`, Dependabot cooldown) |
| 11 | Security scanning | CodeQL, Trivy SARIF, gitleaks, dependency review, actionlint, zizmor, harden-runner, workflow policy R1–R8 |
| 12 | LLMOps eval gate | `evals.yml` (10-case PR subset, thresholds, NEUTRAL without key, budget guard), `nightly-evals.yml` (30 cases, drift, judge, ledger) |
| 13 | Releases | release-please, Conventional Commits PR titles, CHANGELOG, tag `vX.Y.Z` |
| 14 | Multi-arch, SBOM, provenance, signing | QEMU + Buildx amd64/arm64, syft SPDX SBOM, SLSA provenance attestation, keyless cosign sign + attest + verify |
| 15 | Safe `workflow_run` | `doctor.yml`: default-branch checkout only, logs as data, env-passed event fields, no cache in secret-holding job |
| 16 | Reusable Docker action | `action.yml` → `docker://ghcr.io/gagan046-dev/pipelinedoctor:<version>` |
| 17 | Environments & approvals | `staging` (no reviewer), `production` (required reviewer, main/`v*` only) |
| 18 | Continuous delivery | verify-then-pin by digest (`resolve-release.yml`), ephemeral compose staging + smoke, blue-green with automatic rollback, manual `rollback.yml` |
| 19 | Self-hosted runner | Containerized Linux runner with label `pd-prod`, reachable only from approved production jobs |
| 20 | DORA metrics | Deployment statuses annotated by `annotate-deployment.sh`, computed in the Console |
| 21 | Reuse | Reusable workflow (`reusable-python-ci.yml`) for 3 projects, composite action, CODEOWNERS, PR/issue templates |
| 22 | Dependency automation | Dependabot for 5 ecosystems, grouped PRs, ignored runtime jumps |
| + | Local workflow runs | act v0.2.89 (`docs/local-act.md`) |

---

## Concepts and patterns used

Workflow / job / step / action · events (push, pull_request, workflow_run, schedule, workflow_dispatch, workflow_call) ·
job outputs and artifacts · expressions and contexts · script-injection prevention via `env:` · least privilege ·
SHA pinning · aggregate required checks · rulesets · dependency and layer caching · cache scoping (cache-poisoning protection) ·
matrix builds · path filtering · concurrency groups · multi-stage builds · multi-arch images · immutable tags vs digests ·
SBOM · SLSA provenance · keyless signing (OIDC + Sigstore) · Conventional Commits · semantic versioning · release PRs ·
LLM evals vs unit tests · thresholds · result caching · budget guard · model drift · LLM-as-judge · graceful degradation without secrets (NEUTRAL) ·
prompt-injection boundaries (nonce fencing, redaction) · grounded output (cited-line validation) · `workflow_run` trust model ·
environments with required reviewers · ephemeral staging · blue-green deployment · automatic rollback · smoke tests ·
self-hosted runner hardening · DORA metrics · reusable workflows · composite actions · supply-chain cooldown (two-week rule).
