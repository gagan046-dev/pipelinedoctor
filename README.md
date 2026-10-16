# PipelineDoctor

An AI agent that **diagnoses failed GitHub Actions runs** and posts the diagnosis on the pull
request, delivered by a production-grade CI/CD pipeline that you can read stage by stage.

```
failed run ──workflow_run──▶ doctor.yml ──▶ engine: fetch logs → clean → excerpt → redact
                                                  → rule pre-classifier → LLM (2 models max)
                                                  → validate JSON + cited lines → PR comment
```

This is a **teaching repository**. The git history is the course: ten commits tagged
`stage-01` … `stage-10`, each adding one CI/CD topic. `git checkout stage-03` shows the
repository exactly as it was when caching and matrices arrived.

## What is inside

| Part | Path | What it does |
|---|---|---|
| Engine | `src/pipelinedoctor/` | Python package + CLI `pipelinedoctor` (Typer, httpx, Pydantic, LiteLLM) |
| Docker action | `action.yml` | The engine as a reusable GitHub Action for other repositories |
| Prompts | `prompts/` | Versioned, immutable prompt files (`diagnose.v1.md`) |
| Evals | `evals/` | 30 synthetic CI failure logs + thresholds = the LLMOps gate |
| Console | `console/` | FastAPI "PipelineDoctor Console": occurrence register, reports, eval ledger, DORA |
| Demo | `demo/` | A small library you can **break on purpose** to watch the doctor work |
| Deploy | `deploy/`, `runner/` | Blue-green production on a self-hosted runner, automatic rollback |
| Workflows | `.github/workflows/` | 14 workflows, every action pinned by commit SHA |

## The pipeline

| Workflow | Trigger | Purpose | Required check |
|---|---|---|---|
| `hello.yml` | push main, dispatch | Stage 01: events, jobs, outputs, summaries | |
| `ci.yml` | push main, PR | ruff, mypy, pytest + coverage, matrix, path filter | `ci-ok` |
| `demo-ci.yml` | demo changes | The breakable demo's CI | |
| `container.yml` | image changes, called by release | Buildx + GHA cache, GHCR push on main | |
| `security.yml` | push, PR, weekly | gitleaks, dependency review, workflow policy, Trivy SARIF | `secret-scan`, `dependency-review`, `workflow-policy` |
| `codeql.yml` | push, PR, weekly | CodeQL for Python, JS and the workflows themselves | |
| `evals.yml` | PR | 10-case eval subset when prompts/engine/evals change | `eval-gate` |
| `nightly-evals.yml` | cron, dispatch | Full 30 cases + LLM judge, ledger on `eval-history` | |
| `pr-title.yml` | PR | Conventional Commits title | `pr-title` |
| `release.yml` | push main | release-please, signed multi-arch images, SBOM, provenance, deploy | |
| `doctor.yml` | workflow_run | **PipelineDoctor itself** | |
| `deploy.yml` | dispatch, called by release | verify → ephemeral staging → blue-green production | |
| `rollback.yml` | dispatch | Manual production rollback | |
| `reusable-python-ci.yml` | workflow_call | Shared Python CI for engine, console and demo | |

## Learning path

| Stage | Topic | Read |
|---|---|---|
| 01 | Hello workflow: events, jobs, steps, runners, outputs, summary | [docs/stages/01-hello.md](docs/stages/01-hello.md) |
| 02 | Quality gates, job outputs, rulesets | [docs/stages/02-quality-gates.md](docs/stages/02-quality-gates.md) |
| 03 | Speed: caching, matrix, path filters, concurrency, timeouts | [docs/stages/03-speed.md](docs/stages/03-speed.md) |
| 04 | Container builds: Buildx, GHA cache, metadata, GHCR | [docs/stages/04-containers.md](docs/stages/04-containers.md) |
| 05 | Security: CodeQL, Trivy, gitleaks, SHA pinning, least privilege | [docs/stages/05-security.md](docs/stages/05-security.md) |
| 06 | LLMOps eval gate and nightly drift detection | [docs/stages/06-evals.md](docs/stages/06-evals.md) |
| 07 | Releases: release-please, multi-arch, SBOM, SLSA, cosign | [docs/stages/07-releases.md](docs/stages/07-releases.md) |
| 08 | PipelineDoctor: workflow_run + Docker action | [docs/stages/08-doctor.md](docs/stages/08-doctor.md) |
| 09 | CD: environments, staging, blue-green, rollback, DORA | [docs/stages/09-cd.md](docs/stages/09-cd.md) |
| 10 | Reuse: reusable workflow, composite action, templates | [docs/stages/10-reuse.md](docs/stages/10-reuse.md) |

New to CI/CD? Start with the **[Learning Guide](LEARNING-GUIDE.md)** (all ten stages with the
important lines explained and a glossary), keep **[COMMANDS.md](COMMANDS.md)** open for
copy-paste commands, and see **[TECH-STACK.md](TECH-STACK.md)** for every tool and version.

Running workflows locally with `act`: [docs/local-act.md](docs/local-act.md).
Threat model (workflow_run, self-hosted runner, prompt injection): [docs/security.md](docs/security.md).

## Quick start (Windows, PowerShell 5.1)

```powershell
# engine
uv sync
uv run pytest
uv run pipelinedoctor diagnose-log evals\cases\pytest-assertion-failure\log.txt --no-llm

# with the LLM: the key file lives OUTSIDE the repository
$env:OPENROUTER_API_KEY_FILE = 'C:\Users\gagan\OneDrive\Desktop\AIOPs\vitalops\secrets\openrouter_api_key.txt'
uv run pipelinedoctor diagnose-log evals\cases\pytest-assertion-failure\log.txt

# watch the doctor diagnose a real failure on a PR
powershell -ExecutionPolicy Bypass -File demo\scripts\break.ps1 -Mode test
```

Useful `gh` commands:

```powershell
gh run list --limit 10                      # recent runs of every workflow
gh run watch                                # follow the newest run live
gh run view <run-id> --log-failed           # only the logs of failed steps
gh workflow run hello.yml -f who=Philips    # trigger a workflow_dispatch
gh pr checks                                # required checks of the current PR
gh secret list                              # names of configured secrets (never values)
```

## One-time repository setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\repo-settings.ps1         # token, merge, scanning settings
powershell -ExecutionPolicy Bypass -File scripts\setup-environments.ps1    # staging + production
powershell -ExecutionPolicy Bypass -File scripts\set-openrouter-secret.ps1 # OPENROUTER_API_KEY
powershell -ExecutionPolicy Bypass -File scripts\apply-ruleset.ps1         # required checks on main
```

Missing secrets never break CI: without `OPENROUTER_API_KEY` the doctor writes a rule-based
diagnosis and the PR eval gate reports *neutral*.

## License

MIT, see [LICENSE](LICENSE).
