# PipelineDoctor - Commands

Copy-paste commands for Windows PowerShell 5.1 (run from the repository root unless a block
says otherwise). Background for each topic: [LEARNING-GUIDE.md](LEARNING-GUIDE.md).

PowerShell notes: chain with `;` (5.1 has no `&&`), set env vars with `$env:NAME = 'value'`,
run repo scripts with `powershell -ExecutionPolicy Bypass -File <script>`.

```powershell
$repo = 'gagan046-dev/pipelinedoctor'   # used by several commands below
```

---

## 1. Run the tests locally

```powershell
# engine (repo root)
uv sync --frozen
uv run ruff check ; uv run ruff format --check ; uv run mypy src
uv run pytest                                   # unit tests, LLM is faked
uv run pytest --cov=pipelinedoctor --cov-fail-under=85

# workflow policy + its tests (what the workflow-policy check runs)
uv run python scripts/check_workflows.py
uv run pytest -o addopts= -q scripts/tests

# console
cd console ; uv sync --frozen ; uv run ruff check ; uv run mypy ; uv run pytest --cov ; cd ..

# demo library (pip, its own venv)
cd demo
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
ruff check . ; mypy ; pytest --cov=invoicer --cov-fail-under=90
deactivate ; cd ..
```

Try the engine on a sample failure log:

```powershell
uv run pipelinedoctor diagnose-log evals\cases\pytest-assertion-failure\log.txt --no-llm
# with the LLM - the key file stays OUTSIDE the repo, only its path is given:
$env:OPENROUTER_API_KEY_FILE = 'C:\Users\gagan\OneDrive\Desktop\AIOPs\vitalops\secrets\openrouter_api_key.txt'
uv run pipelinedoctor diagnose-log evals\cases\pytest-assertion-failure\log.txt
```

Evals locally:

```powershell
uv run pipelinedoctor eval run --subset all --llm rules --output eval-results/rules.json   # offline baseline
powershell -ExecutionPolicy Bypass -File scripts\eval-local.ps1                             # PR subset with the key
```

## 2. Run the Console on http://127.0.0.1:8090

Option A - the app directly (uvicorn). Port 8090 is also the production blue-green port; use it
only while no local or production deployment is running there (otherwise use 8091).

```powershell
cd console
npm ci ; npm run fonts            # self-hosted fonts into static/fonts
uv sync --frozen

# offline, bundled sample data:
$env:PD_CONSOLE_FIXTURES = '1'
uv run uvicorn pipelinedoctor_console.app:app --host 127.0.0.1 --port 8090

# against the real repository (token in a temp file, never on screen):
Remove-Item Env:PD_CONSOLE_FIXTURES
$env:PD_CONSOLE_CACHE_DIR = "$env:TEMP\pd-console-cache"
gh auth token | Set-Content -NoNewline "$env:TEMP\pd-token.txt"
$env:PD_GITHUB_TOKEN_FILE = "$env:TEMP\pd-token.txt"
uv run uvicorn pipelinedoctor_console.app:app --host 127.0.0.1 --port 8090
```

Option B - the full blue-green stack in Docker (proxy on 8090), no GitHub involved:

```powershell
.\deploy\local\deploy-local.ps1 -Version dev          # first deploy
.\deploy\local\deploy-local.ps1 -Version dev2         # switch blue <-> green
.\deploy\local\deploy-local.ps1 -Action status
.\deploy\local\deploy-local.ps1 -Action rollback
.\deploy\local\deploy-local.ps1 -Action down -RemoveVolumes
```

Open http://127.0.0.1:8090 (`/`, `/evals`, `/version`).

## 3. Run workflows locally with act

```powershell
winget install --id nektos.act --version 0.2.89 -e
act --version
act -l                                                         # jobs per event
act workflow_dispatch -W .github/workflows/hello.yml --input who=me
act push -W .github/workflows/hello.yml -j greet
act pull_request -W .github/workflows/ci.yml -n                # dry run (plan only)
act pull_request -W .github/workflows/ci.yml --matrix os:ubuntu-latest
```

act cannot run `doctor.yml` (workflow_run), OIDC signing, environments/self-hosted jobs or
CodeQL. Secrets file lives outside the repo: `act --secret-file $env:USERPROFILE\.act-secrets`.
Details: [docs/local-act.md](docs/local-act.md).

## 4. gh: runs, logs, PRs, workflows

```powershell
gh auth status
gh run list -R $repo --limit 10                                # recent runs
gh run list -R $repo --workflow ci.yml --limit 5
gh run list -R $repo --branch main --status failure
gh run watch -R $repo                                          # pick a run, follow it live
gh run watch <run-id> -R $repo --exit-status                   # exit code = run result
gh run view <run-id> -R $repo                                  # jobs + annotations
gh run view <run-id> -R $repo --log-failed                     # only failed steps' logs
gh run view <run-id> -R $repo --json jobs --jq '.jobs[] | {name, conclusion}'
gh run rerun <run-id> -R $repo --failed                        # re-run only failed jobs
gh run download <run-id> -R $repo -n eval-results -D eval-results

gh pr list -R $repo
gh pr checks <n> -R $repo                                      # required + other checks
gh pr checks <n> -R $repo --watch
gh pr view <n> -R $repo --comments

gh workflow list -R $repo
gh workflow run hello.yml -R $repo -f who=intern               # workflow_dispatch with input
gh workflow run nightly-evals.yml -R $repo -f judge=false
gh cache list -R $repo --limit 20
gh secret list -R $repo                                        # names only, never values
```

## 5. Releases and supply-chain verification

```powershell
gh release list -R $repo
gh release view v0.2.0 -R $repo                                # notes + SBOM assets
$img = 'ghcr.io/gagan046-dev/pipelinedoctor-console:0.2.0'
gh attestation verify "oci://$img" -R $repo                    # SLSA provenance
docker buildx imagetools inspect $img                          # amd64 + arm64
cosign verify $img --certificate-identity-regexp '^https://github\.com/gagan046-dev/pipelinedoctor/\.github/workflows/container\.yml@refs/heads/main$' --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## 6. Step through the stage tags

```powershell
git fetch --tags
git tag -l "stage-*"
git log --oneline --decorate stage-01^..main
git switch --detach stage-03                                   # look around
git switch main                                                # come back

git diff --stat stage-02 stage-03                              # files changed by stage 03
git diff stage-02 stage-03 -- .github/workflows/               # only workflow changes
git diff stage-05 stage-06 -- .github/workflows/evals.yml
git show stage-01:.github/workflows/hello.yml                  # a file as it was
```

Loop over every stage:

```powershell
$tags = 1..10 | ForEach-Object { 'stage-{0:D2}' -f $_ }
for ($i = 1; $i -lt $tags.Count; $i++) { "{0} -> {1}" -f $tags[$i-1], $tags[$i]; git diff --shortstat $tags[$i-1] $tags[$i] }
```

## 7. One-time actions only you (the repo owner) can do

### 7.1 Set the OpenRouter secret (enables live evals and LLM diagnoses)

Today the secret is **not set**, so the eval gate is NEUTRAL (`skipped_no_key`) and the doctor
writes rule-based diagnoses. The key is piped from its file through stdin, so it never appears
on screen, on a command line or in history. Never paste the key into chat, a commit or a
command.

In Claude Code, run it yourself with the `!` prefix:

```powershell
! gh secret set OPENROUTER_API_KEY -R gagan046-dev/pipelinedoctor
```

`gh` then prompts for the value: paste the contents of
`C:/Users/gagan/OneDrive/Desktop/AIOPs/vitalops/secrets/openrouter_api_key.txt`.
Or, without any prompt, let the repo script read the file and pipe it:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\set-openrouter-secret.ps1
# equivalent one-liner:
(Get-Content -Raw 'C:\Users\gagan\OneDrive\Desktop\AIOPs\vitalops\secrets\openrouter_api_key.txt').Trim() | gh secret set OPENROUTER_API_KEY -R gagan046-dev/pipelinedoctor
gh secret list -R gagan046-dev/pipelinedoctor                 # shows the name and date only
```

Then re-run the evals on PR #8 and the doctor on PR #5 (section 8) to see real scores and an
LLM diagnosis.

### 7.2 Register the self-hosted runner (production deploys)

Follow [docs/self-hosted-runner.md](docs/self-hosted-runner.md). Short version (Docker Desktop
running):

```powershell
.\runner\register.ps1 -UseGh      # builds pd-runner:local, gets a registration token via gh (never printed)
.\runner\start.ps1
gh api repos/gagan046-dev/pipelinedoctor/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'
# expected: pd-laptop, online, labels self-hosted, Linux, X64, pd-prod
.\runner\stop.ps1 ; .\runner\remove.ps1                        # when done
```

### 7.3 Approve a production deployment

A Deploy run pauses at `production` with "Waiting for review". Approve in the UI (run page ->
"Review deployments" -> production -> Approve) or:

```powershell
gh workflow run deploy.yml -R $repo -f version=0.2.0 -f environment=production
gh run list -R $repo --workflow deploy.yml --limit 1            # take the run id
$run = <run-id>
gh api repos/gagan046-dev/pipelinedoctor/actions/runs/$run/pending_deployments --jq '.[].environment | {id, name}'
gh api -X POST repos/gagan046-dev/pipelinedoctor/actions/runs/$run/pending_deployments -F "environment_ids[]=<env-id>" -f state=approved -f comment="ship it"
gh run watch $run -R $repo
```

Without a registered runner the approved job waits for a runner. Rollback:

```powershell
gh workflow run rollback.yml -R $repo -f reason="smoke ok but users see 500s"
gh workflow run rollback.yml -R $repo -f version=0.1.0 -f reason="bad release"
```

## 8. The demo PRs

### PR #5 - broken test -> doctor comment

```powershell
gh pr view 5 -R $repo                                          # draft, branch break/test-...
gh pr checks 5 -R $repo                                        # Demo CI: quality / test + docker fail
gh run list -R $repo --workflow doctor.yml --limit 5           # "Doctor: Demo CI #n"
gh pr view 5 -R $repo --comments                               # the PipelineDoctor comment
gh run download <doctor-run-id> -R $repo -n pipelinedoctor-diagnosis
```

Re-trigger the doctor (e.g. after setting the secret): re-run the failed Demo CI run; when it
fails again, `workflow_run` starts the doctor and the same comment is updated.

```powershell
$id = gh run list -R $repo --workflow demo-ci.yml --branch (gh pr view 5 -R $repo --json headRefName --jq .headRefName) --limit 1 --json databaseId --jq '.[0].databaseId'
gh run rerun $id -R $repo
```

Make a new break PR of another kind (working tree must be clean):

```powershell
python demo/break/apply.py --list
powershell -ExecutionPolicy Bypass -File demo\scripts\break.ps1 -Mode lint
git switch main
.\demo\scripts\cleanup-breaks.ps1 -WhatIf ; .\demo\scripts\cleanup-breaks.ps1
```

### PR #8 - prompt change -> eval table

```powershell
gh pr view 8 -R $repo
gh pr checks 8 -R $repo                                        # eval + eval-gate
gh pr view 8 -R $repo --comments                               # score table (NEUTRAL until the secret exists)
$id = gh run list -R $repo --workflow evals.yml --branch feat/prompt-diagnose-v2 --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $id -R $repo --log | Select-String 'LLM mode|Evals skipped|PASS|FAIL|NEUTRAL'
gh run rerun $id -R $repo                                      # after setting the secret: live scores
gh run download $id -R $repo -n eval-results -D eval-results
```
