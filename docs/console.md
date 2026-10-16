# PipelineDoctor Console

A small FastAPI app that reads the GitHub API and shows what PipelineDoctor found:

| Page | What it shows | Data source |
|---|---|---|
| `/` **Occurrence register** | One row per Doctor run that investigated a failure (`PD-2026-0042`), plus the *pipeline health* strip (DORA) | Doctor workflow runs + their `pipelinedoctor-diagnosis` artifacts; production Deployments |
| `/occurrences/<id>` **Investigation report** | 1. Synopsis · 2. Sequence of events · 3. Evidence (exhibits) · 4. Probable cause · 5. Recommended actions · 6. Verification, and the publication data (model, prompt, cost, latency) | `diagnosis.json` + `excerpt.txt` inside the artifact |
| `/evals` **Eval ledger** | Every nightly eval run plus an accuracy chart with the gate threshold | `ledger.jsonl` on the `eval-history` branch |

There is no database. Everything comes from the GitHub REST API and is cached.

The visual design follows [`CONSOLE-DESIGN-BRIEF.md`](CONSOLE-DESIGN-BRIEF.md): the look of an
accident investigation report, with paper and ink, hairline rules and category stamps.

## Run it locally

```powershell
cd console
npm ci; npm run fonts                 # copy the self-hosted fonts into static/fonts (gitignored)
uv sync --frozen

# Offline, with bundled sample data (no token, no network):
$env:PD_CONSOLE_FIXTURES = "1"
uv run uvicorn pipelinedoctor_console.app:app --host 127.0.0.1 --port 8091
# open http://127.0.0.1:8091   (8090 is reserved for the blue-green deployment)

# Against the real repository:
Remove-Item Env:PD_CONSOLE_FIXTURES
$env:PD_CONSOLE_CACHE_DIR = "$env:TEMP\pd-console-cache"
gh auth token | Set-Content -NoNewline "$env:TEMP\pd-token.txt"    # or a fine-grained read-only PAT
$env:PD_GITHUB_TOKEN_FILE = "$env:TEMP\pd-token.txt"
uv run uvicorn pipelinedoctor_console.app:app --host 127.0.0.1 --port 8091
```

Quality gates (the same commands CI runs through the reusable workflow):

```powershell
uv run ruff check; uv run ruff format --check; uv run mypy; uv run pytest --cov
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PD_CONSOLE_REPO` | `gagan046-dev/pipelinedoctor` | Repository to read |
| `PD_GITHUB_TOKEN` / `GITHUB_TOKEN` / `GH_TOKEN` | — | API token. The first one set wins |
| `PD_GITHUB_TOKEN_FILE` (and `GITHUB_TOKEN_FILE`, `GH_TOKEN_FILE`) | — | Path to a file holding the token (Docker secret style) |
| `GITHUB_API_URL` | `https://api.github.com` | |
| `PD_CONSOLE_CACHE_DIR` | `/var/cache/pd` | Extracted artifacts, cached forever (artifacts are immutable) |
| `PD_CONSOLE_CACHE_TTL_S` | `300` (`900` without token) | API response freshness |
| `PD_CONSOLE_COLOR` | `none` | `blue`/`green` in production, `staging` in staging; shown at `/version` |
| `PD_CONSOLE_VERSION`, `PD_GIT_SHA` | baked into the image | Shown at `/version` |
| `PD_DOCTOR_WORKFLOW` | `doctor.yml` | |
| `PD_EVAL_HISTORY_BRANCH` | `eval-history` | |
| `PD_PROD_ENVIRONMENT` | `production` | Environment used for DORA |
| `PD_CONSOLE_FIXTURES` | off | `1` = bundled sample data; or a directory with the same layout |
| `PD_LOG_LEVEL` | `INFO` | |

**Token permissions.** Read-only is enough: `actions: read` (runs, artifacts), `contents: read`
(ledger, compare), `deployments: read`, `metadata: read`. In production the token comes from
the `CONSOLE_GITHUB_TOKEN` environment secret; it can be empty.

**The token is never logged.** `Settings` hides it from `repr()`, and the `httpx` loggers are set to
WARNING. httpx logs every request URL at INFO, and artifact downloads redirect to signed
blob-storage URLs whose query string *is* a credential. The token is also not sent to that blob
host: the console follows the redirect itself with a client that has no `Authorization` header.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /api/occurrences?limit=50` | Register rows |
| `GET /api/occurrences/{id}` | `{status, report, doctor_run_url, excerpt, notice}`; 404 if the number is not on the register |
| `GET /api/evals` | Ledger entries, oldest first |
| `GET /api/dora?days=30` | DORA metrics (see below) |
| `GET /healthz` | Liveness, no GitHub calls (Docker `HEALTHCHECK`) |
| `GET /readyz` | Readiness: settings valid and a GitHub probe succeeded (probe cached 60 s) |
| `GET /version` | `{version, git_sha, color, started_at}`; blue-green smoke tests check the colour |
| `GET /metrics` | Prometheus text format: instance info, GitHub request/cache counters, remaining rate limit |

Every response carries a strict `Content-Security-Policy` (`'self'` only: no inline scripts or
styles, no external fonts or CDNs), `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.

## How the data is read

```
doctor.yml runs ──► status queued/in_progress ─────────────► "pending"
                  └► completed + artifact pipelinedoctor-diagnosis ─► download zip ─► "diagnosed"
                  └► completed without artifact / failed ─────────────────────────► "skipped"
                  └► conclusion "skipped" (the watched run succeeded) ──► not an occurrence, hidden
```

* **Caching.** Each API response is kept for `PD_CONSOLE_CACHE_TTL_S`. After that the console sends
  `If-None-Match: <etag>`. A `304 Not Modified` answer does not count against the authenticated
  rate limit.
* **Graceful failure.** On a rate limit (`403` with `x-ratelimit-remaining: 0`, or `429`), a `401`
  or a network error, the console serves the last cached copy and shows a notice such as "GitHub API
  rate limit reached. It resets at 14:05 UTC. Showing cached data." With nothing cached, the page
  shows an empty register with that notice. Pages never show a 500.
* **No token.** Unauthenticated access allows 60 requests per hour and cannot download
  artifacts. The register still lists occurrences from the run conclusions, the report pages say
  why they are empty, and a notice says "Running without a GitHub token — data may be stale."
* **Retention.** Artifacts expire after 90 days. Older occurrences then drop off the register;
  the console does not fake them.

## DORA metrics (pipeline health strip)

They are computed in `dora.py` from GitHub Deployments in the `production` environment. Each
deployment's outcome is read from the status that `deploy/scripts/annotate-deployment.sh` writes
(`pd:kind=deploy;version=…;color=…;outcome=deployed|auto_rolled_back|failed_before_switch|manual_rollback`).

* **Deployment frequency** = successful deploys in the window ÷ weeks.
* **Lead time** = median hours from each shipped commit (`compare previous...this`) to the deploy.
* **Change failure rate** = deploys that rolled back automatically, failed before the switch, or
  were followed by a manual rollback, divided by all deploys.
* **Time to restore** = median hours from a failure to the next good record. A
  `failed_before_switch` counts as 0 h because production never served the bad version.

A metric without enough data shows "—", never 0.

## Fixture mode

`PD_CONSOLE_FIXTURES=1` swaps **only the HTTP transport** (`fixtures.py`) for one that answers
like the GitHub API from `src/pipelinedoctor_console/fixture_data/`. Caching, ETags, artifact
redirects (including the "no token on the blob host" rule), zip extraction and the DORA maths all
run unchanged. That makes the mode useful for tests, offline demos and design work. Every page
then carries a **Fixture mode** banner, so sample data is never mistaken for real records.

## Image

`console/Dockerfile` has three stages. `fonts` (Node, build platform only) copies the
`@fontsource` files. `builder` runs `uv sync --frozen --no-dev --no-editable` with a cache mount.
`runtime` is slim Python running as uid `10001`, with a `HEALTHCHECK` on `/healthz`, and it works
with `read_only: true` plus a `tmpfs` on `/tmp`. Hatch's `artifacts` setting puts the gitignored
fonts into the wheel.

```powershell
docker build -t pipelinedoctor-console:dev --build-arg VERSION=0.1.0 --build-arg GIT_SHA=$(git rev-parse HEAD) console
docker run --rm -p 127.0.0.1:8091:8000 -e PD_CONSOLE_FIXTURES=1 pipelinedoctor-console:dev
```

## Smoke test

`python -m pipelinedoctor_console.smoke --base-url URL [--expect-version V] [--expect-color C] [--require-github]`
runs inside the release image on the compose network, in both staging and production. It prints
one line per check and exits with the number of failed checks.

## Security notes

* Templates contain no data from GitHub. JavaScript renders every log line and every LLM sentence
  with `textContent` or text nodes. A test fails the build if `innerHTML`, `outerHTML`,
  `insertAdjacentHTML` or `document.write` appears in `static/js`.
* Links from the API are only rendered if they are `http(s)` URLs.
* The theme choice is the only thing kept in `localStorage`, and every access is wrapped in
  `try/catch`.
