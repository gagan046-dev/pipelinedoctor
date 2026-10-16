# Stage 08 - PipelineDoctor itself

**Tag:** `stage-08` · **Files:** `.github/workflows/doctor.yml`, `action.yml`,
`docs/examples/consumer-workflow.yml`

## workflow_run

```yaml
on:
  workflow_run:
    workflows: [CI, Demo CI, Container, Security, CodeQL, Evals, Nightly Evals, Release, Deploy, Rollback]
    types: [completed]
```

`workflow_run` fires when another workflow finishes. It always runs **the default-branch
version** of `doctor.yml`, in the context of the base repository: it gets secrets and a token
that can comment on PRs - even when the failed run came from a fork. That is exactly what the
doctor needs (fork PRs get diagnosed too) and exactly why it is dangerous.

Rules this workflow follows (and `scripts/check_workflows.py` enforces):

1. Check out **only** `github.event.repository.default_branch`, never
   `github.event.workflow_run.head_sha` (rule R8). PR code never runs here.
2. Download logs **as data** through the REST API; nothing from them is executed.
3. Event fields (branch names, titles - attacker-controlled) go through `env:` (rule R6).
4. Minimal permissions: `actions: read`, `contents: read`, `pull-requests: write`.
5. Logs are untrusted LLM input: fenced with a random nonce, redacted, and instructions inside
   them are ignored (see [security.md](../security.md)).
6. No cache restore in a job that holds secrets (`enable-cache: false` for setup-uv).

Limits: `workflow_run` only fires for workflow files that exist on the default branch, and
chains at most 3 levels deep. The job-level `if:` skips successful runs; `startup_failure`
(invalid YAML, no jobs) is diagnosed from run metadata.

Outputs: one idempotent PR comment (hidden marker `<!-- pipelinedoctor:report v1 -->`, one
section per failed workflow), the job summary, and the artifact `pipelinedoctor-diagnosis`
(`diagnosis.json`, read by the Console).

## The reusable Docker action

`action.yml` (repo root) runs the released engine image
`docker://ghcr.io/gagan046-dev/pipelinedoctor:<version>` (bumped by release-please). Other
repositories use it like this - pinned by SHA, same least privilege:

```yaml
- uses: gagan046-dev/pipelinedoctor@<full-commit-sha> # vX.Y.Z
  with:
    openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}
```

Docker actions only run on Linux runners, and run as root by GitHub's design (the justified
ignore in `.trivyignore.yaml`). The image exists after the first release.

## Commands

```powershell
gh run list --workflow doctor.yml
gh run view <doctor-run-id> --log
gh run download <doctor-run-id> -n pipelinedoctor-diagnosis
gh pr view <n> --comments
powershell -ExecutionPolicy Bypass -File demo\scripts\break.ps1 -Mode test   # cause a real failure
```
