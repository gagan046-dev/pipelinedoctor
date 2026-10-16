# Demo: break CI on purpose, watch PipelineDoctor diagnose it

`demo/` is a small, healthy Python library (`invoicer`: invoice totals with correct decimal
rounding, money formatting, a tiny PostgreSQL store). Its workflow **Demo CI**
(`.github/workflows/demo-ci.yml`) is green on `main`. `demo/break/apply.py` injects one *real*
defect at a time, so the failure you watch is a genuine one - not a fake log.

## 1. What Demo CI runs

| Job | What | Typical break |
|---|---|---|
| `quality` (reusable workflow) | ruff, `ruff format --check`, mypy strict, pytest + coverage >= 90 % on 3.11 / 3.12 | test, lint, type, dependency, missing-module, conflict |
| `integration` | pytest `-m integration` against a `postgres:17.11-alpine` service container | db, timeout |
| `docker` | builds `demo/Dockerfile`, runs the unit tests inside it with `--memory=256m` | docker, docker-base, oom |
| `ops` | `gh api` with a read-only token, a dry-run publish step | permission, secret |

Locally (no Docker needed for the unit part):

```powershell
cd demo
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # includes -e . (run it from demo/)
ruff check .; ruff format --check .; mypy; pytest --cov=invoicer --cov-fail-under=90
# integration tests need a database:
docker run -d --rm --name pd-demo-pg -e POSTGRES_PASSWORD=demo -p 127.0.0.1:55432:5432 postgres:17.11-alpine
$env:DEMO_DB_PORT = '55432'; pytest -m integration; docker rm -f pd-demo-pg
# the container test run, exactly like CI:
docker build -t pipelinedoctor-demo:ci .
docker run --rm --memory=256m pipelinedoctor-demo:ci python -m pytest -q -m "not integration"
```

## 2. Break it

```powershell
python demo/break/apply.py --list            # 14 modes
python demo/break/apply.py --dry-run test    # see exactly what will change
.\demo\scripts\break.ps1 -Mode test          # branch, apply, commit, push, draft PR
```

`break.ps1` creates `break/<mode>-<yyyyMMddHHmm>` from `origin/main`, commits
`test(demo): break <mode> on purpose` (a valid Conventional Commit, so `pr-title` stays green)
and opens a **draft** PR. Your working tree must be clean; afterwards you are on the break
branch (`git switch main` to go back).

## 3. Watch

```powershell
gh run list --branch break/test-202610161155                 # Demo CI fails
gh run watch <run-id>
gh run list --workflow doctor.yml --limit 3                    # Doctor starts on workflow_run
gh pr view --web                                               # one PipelineDoctor comment
```

What happens behind the scenes: Demo CI completes with `failure` -> GitHub fires
`workflow_run` for `doctor.yml` **from the default branch** -> Doctor downloads the failed job's
log, cleans and redacts it, classifies it, asks the LLM (or falls back to rules) and upserts a
single comment on the PR. Push a fix to the same branch and the comment's section is updated,
not duplicated. The diagnosis also appears as an occurrence in the Console.

## 4. The modes

| Mode | Defect | Where it fails | Category to expect |
|---|---|---|---|
| `test` | `apply_tax` truncates (`ROUND_DOWN`) | quality / test: `assert Decimal('4.19') == Decimal('4.20')` | test |
| `lint` | unused `import os`, unformatted line | quality / lint | lint |
| `type` | `format_money(...) -> int` | quality / typecheck (mypy) | lint |
| `dependency` | `typing-extensions==4.1.1` vs psycopg's `>=4.6` | every `pip install`: ResolutionImpossible | dependency |
| `missing-module` | `import humanize` (never declared) | ModuleNotFoundError | dependency |
| `conflict` | `<<<<<<<`/`>>>>>>>` markers in `totals.py` | SyntaxError in lint and tests | build |
| `docker` | `COPY config/ ./config/` (no such directory) | docker / Build demo image | docker |
| `docker-base` | `FROM python:3.12.99-slim` | docker / Build demo image: not found | docker |
| `timeout` | an integration test sleeps 400 s | step `timeout-minutes: 3` | flaky |
| `oom` | a unit test allocates 1 GiB | container killed, exit code 137 | infrastructure |
| `db` | `DEMO_DB_PORT: "5433"` | connection refused | infrastructure |
| `permission` | `gh api -X POST .../labels` with `contents: read` | HTTP 403 | secret_permission |
| `secret` | publish step without `--optional-token` | `Error: DEMO_PUBLISH_TOKEN is not set` | secret_permission |
| `yaml` | invalid YAML in `demo-ci.yml` | run has no jobs (`startup_failure`) | config |

Verified locally while building this: `oom` exits 137 under `--memory=256m`, `db` gives
"connection refused", `yaml` no longer parses, every other mode's diff applies cleanly
(`--dry-run`).

Caveat for `yaml`: when the workflow file cannot be parsed, GitHub may not know the workflow's
`name:` (`Demo CI`), and `doctor.yml` listens by name - so Doctor may not fire for this one.
That is a real limitation of `workflow_run`, worth seeing once.

## 5. Clean up

```powershell
.\demo\scripts\cleanup-breaks.ps1 -WhatIf    # what would be closed/deleted
.\demo\scripts\cleanup-breaks.ps1            # close break PRs, delete break/* branches
```

## 6. How the break tool stays honest

* It matches **exact anchor text**. In the workflow those are the `# pd-break-anchor: <mode>`
  comments; in code, specific lines. If someone reformats a file, `apply.py` stops with
  "anchor text not found" instead of guessing.
* Idempotent (`already applied`), refuses to run on `main` or a detached HEAD.
* New files come from readable templates in `demo/break/patches/<mode>/`.
* Standard library only, so it runs before any dependency is installed.
