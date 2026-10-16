# Stage 06 - LLMOps eval gate

**Tag:** `stage-06` · **Files:** `evals.yml`, `nightly-evals.yml`, `evals/**`,
`scripts/set-openrouter-secret.ps1`, `scripts/eval-local.ps1`, `docs/evals.md`

## Why evals are CI

A prompt edit or a model update can silently make diagnoses worse while every unit test stays
green. Evals are tests for behaviour: 30 realistic failure logs with expected categories and
key facts. A PR that changes the prompt, the engine or the dataset must keep the scores above
`evals/thresholds.yaml`.

## evals.yml (PRs)

```
changes (paths-filter: prompts/, src/pipelinedoctor/, evals/)
   └── eval (only if changed)  restore cache -> 10 cases -> save cache -> gate -> PR comment -> artifact
         └── eval-gate (always) success if eval succeeded or was skipped   <- required check
```

* **Job-level path filter + always-running gate**: a path-filtered workflow would leave the
  required check *pending forever* on PRs that do not touch those paths.
* **Gate step with `continue-on-error`** so the comment and artifact steps still run; a final
  step fails the job if `steps.gate.outcome == 'failure'`.
* **Missing key -> neutral, never red**: `HAS_KEY: ${{ secrets.OPENROUTER_API_KEY != '' }}`.
  Fork PRs and Dependabot PRs have no secrets; the engine then answers from the cache only
  (`--llm cache-only`) and the gate prints "Evals skipped: OPENROUTER_API_KEY not available".

## Budget: OpenRouter free tier

About 50 requests/day and 20/minute. Therefore: max 2 LLM calls per case, `--max-requests 12`
(PR) / `45` (nightly), 3.5 s spacing, and a **result cache**:

```
key = sha256(case.yaml + log + prompt version + prompt sha256 + models + engine fingerprint)
```

Unchanged cases are not re-run. The cache directory is persisted with `actions/cache`:

* `restore` with `restore-keys: pd-evals-v1-` takes the newest cache;
* `save` with a unique key (`run_id` + `run_attempt`) - caches are immutable, so every run
  saves a new entry and GitHub evicts old ones (10 GB per repo).
* **Scope:** a PR can read caches saved on `main` (the nightly run), main cannot read PR
  caches, and PRs cannot read each other's. That is cache-poisoning protection.

## nightly-evals.yml

Full 30 cases with `--no-cache-read` (always ask the model: detects **drift** of `:free` models
that change behind the same name), optional LLM-as-judge on 8 cases, nightly thresholds. The
`record` job appends one JSON line to `ledger.jsonl` on the orphan branch **`eval-history`**
(created on first run with `git switch --orphan`), which the Console charts. It is the only job
in the repo whose checkout keeps credentials, because it pushes; it uses no secrets.

## Commands

```powershell
powershell -ExecutionPolicy Bypass -File scripts\set-openrouter-secret.ps1   # once
gh secret list
gh workflow run nightly-evals.yml -f judge=false
gh run download <run-id> -n eval-results -D eval-results
gh api "repos/gagan046-dev/pipelinedoctor/contents/ledger.jsonl?ref=eval-history" -H "Accept: application/vnd.github.raw+json"
gh cache list --key pd-evals-v1-
powershell -ExecutionPolicy Bypass -File scripts\eval-local.ps1              # run locally
```

## Exercise

Copy `prompts/diagnose.v1.md` to `diagnose.v2.md`, remove the evidence instructions, set
`PD_PROMPT_VERSION=v2` and open a PR. Watch `evidence_validity` drop and `eval-gate` turn red.
