# The diagnosis engine (`pipelinedoctor`)

The engine turns one failed GitHub Actions run into a structured, verified diagnosis and posts
it on the pull request. It is a normal Python package (src layout, Python 3.11+, managed with
uv) with a Typer CLI, and it is also shipped as a Docker container action (`action.yml`).

```
GitHub API ──> fetch ──> clean ──> redact ──> excerpt ──> rules hint ──> LLM (≤2 calls)
                                                                    └─> rules fallback
           <── PR comment / job summary / diagnosis.json <── render <── validate + verify
```

| Module | Job |
|---|---|
| `github/client.py` | REST calls with httpx: run, jobs, job log, PR lookup, comments |
| `engine/preprocess.py` | strip timestamps/ANSI/BOM, number lines, choose a bounded excerpt |
| `engine/redact.py` | remove tokens, keys, JWTs, URL credentials before anything leaves the job |
| `engine/rules.py` | 31 regex rules: a hint for the LLM and the deterministic fallback |
| `engine/prompting.py` | load versioned prompt files, hash them |
| `engine/llm.py` | LiteLLM client, request budget, one-call-per-model fallback |
| `engine/parse.py` | extract JSON, validate with Pydantic, verify cited log lines |
| `engine/diagnose.py` | the pipeline: `analyze_log()` (pure) and `diagnose_run()` (I/O) |
| `render/markdown.py` | PR section and job summary, made safe for untrusted text |
| `github/comments.py` | one idempotent comment per PR, one section per workflow |
| `evals/*` | dataset, scoring, cache, judge, gate (see [evals.md](evals.md)) |
| `schemas.py` | every JSON contract as a Pydantic model; JSON Schema is generated from it |

## Run it locally (PowerShell)

```powershell
uv sync                                   # creates .venv from uv.lock (dev tools included)
uv run pipelinedoctor version

# Learning mode: diagnose a saved log, no GitHub token needed, rules only
uv run pipelinedoctor diagnose-log evals/cases/docker-copy-not-found/log.txt --no-llm

# Same with the LLM. The key file lives OUTSIDE the repo; never copy it in.
$env:OPENROUTER_API_KEY_FILE = "C:\Users\gagan\OneDrive\Desktop\AIOPs\vitalops\secrets\openrouter_api_key.txt"
uv run pipelinedoctor diagnose-log evals/cases/docker-copy-not-found/log.txt
Remove-Item Env:OPENROUTER_API_KEY_FILE

# A real failed run of this repository (needs a token that can read Actions)
$env:GITHUB_TOKEN = (gh auth token)
uv run pipelinedoctor diagnose --repo gagan046-dev/pipelinedoctor --run-id <run-id> --comment never --no-llm
Remove-Item Env:GITHUB_TOKEN
```

Find a failed run id with `gh run list --status failure --limit 5`.

Quality gates, exactly as CI runs them:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=pipelinedoctor --cov-report=term --cov-fail-under=85
```

The tests never touch the network: `tests/conftest.py` makes every socket connection fail, GitHub
is replaced by `respx` routes with recorded response shapes (`tests/fixtures/github/`), and the
LLM by `tests/fakes.py:FakeLLM`.

## CLI

| Command | Purpose |
|---|---|
| `pipelinedoctor diagnose --repo R --run-id N [--job-id N] [--comment auto\|always\|never] [--output diagnosis.json] [--excerpt-output excerpt.txt] [--summary/--no-summary] [--no-llm]` | used by `doctor.yml` |
| `pipelinedoctor diagnose-log LOGFILE [--workflow W] [--job J] [--step S] [--output PATH] [--no-llm]` | local learning mode |
| `pipelinedoctor eval run / gate / comment / ledger-entry` | see [evals.md](evals.md) |
| `pipelinedoctor schema --write schemas` | regenerate the JSON Schema files after changing `schemas.py` |
| `pipelinedoctor action` | Docker action entrypoint |

Exit codes of `diagnose`: `0` a report was written (also for fallbacks), `2` configuration
error (no token, bad repo, unreadable key file), `3` GitHub API error. An LLM failure never
fails the doctor job: the report then says `source: rules_fallback`.

Inside Actions, `diagnose` appends `occurrence-id`, `category`, `confidence`, `source`,
`report-path` and `comment-url` to `$GITHUB_OUTPUT` and the report to `$GITHUB_STEP_SUMMARY`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` / `OPENROUTER_API_KEY_FILE` | unset | LLM key, or a file containing it; unset = rules only |
| `GITHUB_TOKEN` (or `GH_TOKEN`) | unset | API token |
| `PD_MODELS` | super → ultra (`:free`) | ordered fallback list (LiteLLM model names) |
| `PD_PROMPT_VERSION` | `v1` | which `prompts/diagnose.<v>.md` to use |
| `PD_LLM_TIMEOUT_S` | `60` | per request |
| `PD_LLM_MAX_CALLS` | `2` | per diagnosis (hard cap 2: one per model, no hidden retries) |
| `PD_MAX_LLM_REQUESTS` | 2 / 12 / 45 | process-wide request budget (diagnose / eval pr / eval all) |
| `PD_MIN_REQUEST_INTERVAL_S` | `3.5` | spacing between requests (stays under ~17/min) |
| `PD_LOG_WINDOW_LINES` / `PD_MAX_EXCERPT_LINES` | `40` / `400` | excerpt size |
| `PD_DISABLE_LLM` | `0` | `1` = rules only |
| `PD_LOG_LEVEL` | `INFO` | logging to stderr |

## How a diagnosis is made

1. **Fetch.** Run, jobs (`filter=latest`) and the failed job's log. The logs endpoint answers
   `302` with a short-lived signed URL; the engine follows it *without* the Authorization header
   (the token must never reach a third-party host). Logs are capped at 20 MB / 50 000 lines,
   keeping the **end** of the log (that is where the error is); the report notes when the head
   was dropped.
   A run without jobs (`startup_failure`, e.g. invalid workflow YAML) gets a `config` report from
   run metadata only.
2. **Clean and number.** Timestamps, ANSI colours and BOMs are stripped; lines are numbered over
   the whole job log. These numbers are what evidence cites.
3. **Redact** tokens, keys and credentials, plus the exact values of the configured secrets,
   over the **whole** log. Doing this before the excerpt matters: a 200-line private key whose
   BEGIN line lies outside the excerpt would otherwise reach the LLM half-redacted.
4. **Excerpt.** Windows of ±40 lines around the last 6 error-looking lines plus the last 30
   lines, capped at 400 lines (windows nearest the end win). This keeps one request small and
   cheap, and the real error is almost always near the end of the failed step.
5. **Rules** give a non-binding hint (`pre_classification`).
6. **LLM.** One call to the primary model, one to the fallback model only if the first failed
   (timeout, 429, provider error, unusable JSON). The super model is called with JSON mode; the
   ultra free endpoint does not support it, so its answer is parsed from prose. A model that
   answered 429 is skipped for 60 s (a small circuit breaker in `RequestBudget`), so an eval run
   does not pay two requests per case while the primary model is rate-limited.
7. **Verify.** The answer is validated with Pydantic. Each evidence item must cite a line that
   is in the excerpt *and* quote text that is really on that line; anything else is dropped and
   the report always shows the real log line, never the model's quote.
8. **Calibrate.** Confidence is capped at 0.95, reduced when the model disagrees with a strong
   rule, and capped at 0.3 without verified evidence. Grades: A ≥ 0.75, B ≥ 0.5, C below.
9. **Fallback.** No key, LLM disabled, or both calls failed: a deterministic rule-based report,
   clearly labelled (`source: rules_only` or `rules_fallback`).
10. **Where it is posted.** Only a `pull_request` run gets a PR comment, and only on an OPEN PR
    whose head is exactly the failed commit. A failure on `main` (push, schedule, dispatch)
    gets the job summary and the artifact, never a comment on last week's merged PR.

## Trust boundaries

* Logs, branch names, PR titles and model output are **untrusted**. Nothing from them is ever
  executed; recommended commands are display text.
* The excerpt sits inside `<<<UNTRUSTED_LOG nonce=…` / `UNTRUSTED_LOG nonce=…>>>` with a random
  nonce; copies of the nonce inside the log are replaced, so the log cannot close the fence. The
  system prompt says that instructions inside the fence are data. Lines that look like
  instructions to an AI add a warning to the report (see the `prompt-injection-in-log` eval).
* Workflow, job and step names come from a workflow file that a PR can edit, so they sit in a
  second fenced block (`UNTRUSTED_METADATA`), one line each, max 100 characters, with
  instruction-like phrases replaced.
* Rendering strips HTML, neutralises `@mentions`, defangs Markdown links and images (a
  prompt-injected log must not make the bot show a phishing link) and puts log lines in code fences longer than
  any backtick run in the content, so a log cannot inject markdown, ping people or forge the
  comment's hidden section markers.
* `doctor.yml` runs on `workflow_run`, which has the base repository's secrets. It checks out
  only the default branch, never the PR head.

## The PR comment

One comment per PR, found again by its first line `<!-- pipelinedoctor:report v1 -->`. Each
failed workflow owns a section between `<!-- pd:section workflow=<file> -->` and
`<!-- pd:end -->`. Updating replaces only that section; after writing, the engine re-reads the
comment and retries (up to 3 times with jitter) if a parallel doctor run overwrote it. Only
comments by `github-actions[bot]` (or the token's own user) are candidates, so pasting the
marker into a human comment changes nothing.

## The Docker action

`action.yml` points at the prebuilt image `ghcr.io/gagan046-dev/pipelinedoctor:<version>` and
runs `pipelinedoctor action`. GitHub passes inputs as environment variables named
`INPUT_<NAME>`; for Docker actions the hyphens are kept (`INPUT_GITHUB-TOKEN`), which a shell
cannot even reference, so the entrypoint reads them with `os.environ` in Python. Docker actions
run only on Linux runners. The report is written to `pipelinedoctor-report.json` in the
workspace. The image does not exist before the first release (stage 07); until then use the CLI
as `doctor.yml` does. A consumer example is in
[examples/consumer-workflow.yml](examples/consumer-workflow.yml).

## Changing things safely

* New rule or wording in the engine → the engine fingerprint changes → the PR eval run re-sends
  only affected cases to the LLM (cache key covers code, prompt and models).
* New prompt wording → new file `prompts/diagnose.v2.md` (see `prompts/README.md`).
* Changed JSON shape → edit `schemas.py`, run `uv run pipelinedoctor schema --write schemas`,
  regenerate the examples, and remember the Console mirrors these models.
