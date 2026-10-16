# Stage 03 - Speed: caching, matrix, path filters, concurrency, timeouts

**Tag:** `stage-03` · **Files:** `ci.yml` and `demo-ci.yml` (stage-03 versions), `demo/**`

## What you learn

| Technique | How | Why |
|---|---|---|
| **Dependency cache** | `setup-uv` with `enable-cache: true`, key = hash of `uv.lock`; demo uses `setup-python` `cache: pip` keyed by `requirements*.txt` | unchanged lock file -> restore instead of download |
| **Matrix** | `os: [ubuntu-latest, windows-latest]` x `python: ["3.11","3.12"]` with an `exclude` | test every supported combination without copy-paste |
| **fail-fast: false** | in `strategy:` | one red leg does not cancel the others: you see *every* broken combination |
| **Job-level path filter** | `dorny/paths-filter` job + `if: needs.changes.outputs.engine == 'true'` | skip expensive jobs, but the workflow (and `ci-ok`) still reports |
| **Workflow-level path filter** | `on.pull_request.paths` in `demo-ci.yml` | the whole workflow is skipped - only OK for checks that are **not required** |
| **Concurrency** | `group: <workflow>-<PR or ref>`, `cancel-in-progress` only for PRs | a new push cancels the outdated PR run; main runs always finish |
| **Timeouts** | `timeout-minutes` on every job (and on one step in demo-ci) | the default is 360 minutes; a hung test should cost 3 |

## Two path-filter styles, one trap

A **required** check that never starts blocks merging forever ("Expected - waiting for status").
`ci.yml` therefore filters inside the workflow (jobs are *skipped*, and a skipped job counts as
passed), while `demo-ci.yml` can use `on.paths` because Demo CI is not required.

## Reading cache behaviour

In the logs of `setup-uv` look for `Cache restored from key: setup-uv-...` vs `No GitHub Actions
cache found`. Caches are scoped: a PR can read caches from `main`, but not from another PR.

```powershell
gh cache list --limit 20                 # caches, keys and sizes
gh cache delete <key>                    # force a cold run
gh run view <run-id> --json jobs --jq '.jobs[] | {name, started: .startedAt, done: .completedAt}'
```

## The demo project

`demo/` is a small invoice library with unit tests, a Postgres integration test (a *service
container* in the `integration` job) and a Dockerfile. Its workflow contains `pd-break-anchor`
comments: `demo/break/apply.py <mode>` edits exactly those lines to produce real failures
(see `docs/demo.md`). The doctor arrives in stage 08.

## Exercise

Push two commits to a PR within ten seconds and watch the first run get cancelled
("Canceling since a higher priority waiting request ... exists").
