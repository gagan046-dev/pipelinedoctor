# Stage 02 - CI quality gates

**Tag:** `stage-02` · **Files:** `.github/workflows/ci.yml` (stage-02 version), engine code,
`.github/rulesets/main.json`, `scripts/apply-ruleset.ps1`, `scripts/repo-settings.ps1`

## What you learn

* **Quality gates** - a check that turns red on purpose:
  * `ruff check` (lint) and `ruff format --check` (formatting) - `--output-format=github`
    makes every finding an inline annotation on the PR diff;
  * `mypy src` with `strict = true` (types);
  * `pytest --cov-fail-under=85` (tests **and** a coverage floor).
* **Parallel jobs**: lint, typecheck and test run at the same time on three VMs.
* **Reproducible installs**: `uv sync --frozen` installs exactly `uv.lock` and fails if
  `pyproject.toml` and the lock disagree, instead of silently resolving new versions.
* **Job outputs**: the test job publishes `coverage`; `ci-ok` prints it in the summary.
* **Aggregate check**: `ci-ok` (with `if: always()`) is the *one* check branch protection
  requires. Adding or renaming matrix legs later does not touch the protection rules.

## Branch protection with a repository ruleset

A ruleset is the modern branch protection. `.github/rulesets/main.json` requires a PR
(squash only) and the checks `ci-ok`, `eval-gate`, `secret-scan`, `workflow-policy`,
`dependency-review`, `pr-title`. Each check is pinned to `integration_id: 15368` (the GitHub
Actions app), so a commit status with the same name posted by someone else does not count.

The ruleset is applied only **after** stage 10 is pushed, because the stage commits go
straight to `main`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\apply-ruleset.ps1
gh api repos/gagan046-dev/pipelinedoctor/rulesets --jq '.[] | {id, name, enforcement}'
gh api repos/gagan046-dev/pipelinedoctor/rules/branches/main --jq '.[].type'
```

`scripts/repo-settings.ps1` sets the rest: read-only default `GITHUB_TOKEN`, squash merge with
the PR title as commit message, secret scanning + push protection.

## Commands

```powershell
uv run ruff check ; uv run ruff format --check ; uv run mypy src ; uv run pytest
gh pr checks                       # which checks passed on the current PR
gh run view <run-id> --log-failed  # only the failing step logs
```

## Exercise

Open a PR that adds an unused import. Find the inline annotation on the diff, then see `ci-ok`
fail because `lint` failed.
