# Evals: the LLMOps gate

Unit tests prove the code works. They cannot prove that a prompt change, a different model or a
silent model update still produces *good diagnoses*. Evals do that: a fixed dataset of failed CI
logs with known answers, scored automatically, with a gate that fails the PR when quality drops.

## What is measured

| Metric | Definition | Gated |
|---|---|---|
| `category_accuracy` | predicted category == expected | PR + nightly |
| `evidence_validity` | verified citations / all citations the model made (0 if it cited nothing) | PR + nightly |
| `key_fact_recall` | mean share of a case's key facts mentioned in summary, cause, actions, verification | PR + nightly |
| `secret_leak_free` | share of planted-secret cases whose report does not contain the secret (must be 1.0) | PR + nightly |
| `llm_coverage` | share of scored cases answered by an LLM rather than the rule fallback | PR + nightly |
| `latency_p95_ms` | 95th percentile latency (nearest rank) | PR + nightly |
| `judge_score_mean` | LLM-as-judge rating 1-5 of the fix vs. the reference fix | nightly only |
| `min_scored_ratio` | scored cases / all cases (budget skips lower it) | PR + nightly |
| cost, LLM calls, cache hits | reported in the table | no |

Thresholds are in [`evals/thresholds.yaml`](../evals/thresholds.yaml). The gate outcome is
`pass`, `fail` or `neutral`:

* **neutral** — no API key and not everything came from cache (fork PRs, Dependabot, secret not
  configured yet). Exit 0 with a warning: *Evals skipped: OPENROUTER_API_KEY not available —
  nightly run on main remains the safety net.*
* **fail** — any check below its threshold. If the LLM was unreachable or rate-limited the
  coverage check fails too and the message says *LLM provider unavailable or rate-limited —
  re-run later*. An honest gate never passes on fallback answers.

## Commands

```powershell
# Offline baseline, no key, no network: rules only (expect llm_coverage 0 -> gate fails)
uv run pipelinedoctor eval run --subset all --llm rules --output eval-results/rules.json

# What CI runs on a PR (needs a key; reuses .eval-cache)
uv run pipelinedoctor eval run --subset pr --output eval-results/result.json
uv run pipelinedoctor eval gate --result eval-results/result.json --thresholds evals/thresholds.yaml --mode pr --markdown-output eval-results/gate.md

# Without a key: only cached results are used, misses are "skipped_no_key"
uv run pipelinedoctor eval run --subset pr --llm cache-only

# Nightly: all 30 cases, ignore cached answers (still writes them), judge 8 of them
uv run pipelinedoctor eval run --subset all --no-cache-read --judge --judge-sample 4 --max-requests 45
uv run pipelinedoctor eval ledger-entry --result eval-results/result.json --thresholds evals/thresholds.yaml --mode nightly --run-url <url>
```

`scripts/eval-local.ps1` wraps the local run and points `OPENROUTER_API_KEY_FILE` at the key
file outside the repository for the duration of the run only.

`--llm` modes: `live` (call the LLM for cache misses), `cache-only` (never call), `rules`
(offline deterministic baseline; not cached; useful to see what the fallback alone achieves).
The rules were written while looking at these synthetic logs, so their 100 % accuracy here is an
upper bound, not evidence that rules generalise. The LLM has to win on real logs.

## Staying inside the free tier

OpenRouter's free models allow roughly **50 requests per day and 20 per minute** (about 1000 per
day once the account has bought at least 10 credits). The harness is built around that:

* **One request per case** in the normal path; the fallback model is called only if the first
  answer was unusable. LiteLLM retries are switched off so every request is counted.
* **Request budget.** `--max-requests` (default 12 for `pr`, 45 for `all`) is enforced by
  `RequestBudget`; judge calls count too. When it runs out the remaining cases are
  `skipped_budget`, which lowers `min_scored_ratio` (the gate tells you why).
* **Spacing.** At least 3.5 s between requests (`PD_MIN_REQUEST_INTERVAL_S`) keeps the rate
  under 20/min. Cases run sequentially.
* **Cache.** Each case result is stored under
  `sha256(case.yaml + log hash + prompt id/version/sha256 + model list + engine fingerprint)`.
  An unchanged case is never re-sent. Change a rule in `engine/` and the fingerprint changes;
  edit a prompt file and its sha changes; either way the cache invalidates itself.
  Rule-based fallbacks are *not* cached, so a rate-limit outage is retried next time.
* **Small PR subset.** 10 cases on PRs, 30 nightly.
* **Preflight.** A live run first calls `GET https://openrouter.ai/api/v1/key` (free, not
  counted) and logs `is_free_tier`, limit and usage; failures are ignored.

In CI the cache directory `.eval-cache` is persisted with `actions/cache` (key
`pd-evals-v1-<run id>`, restore prefix `pd-evals-v1-`). Caches saved on `main` (the nightly run)
are readable from PR branches, not the other way round, so the nightly run warms the cache that
PRs reuse.

## Files

| Path | Content |
|---|---|
| `evals/cases/<id>/` | the dataset ([evals/README.md](../evals/README.md)) |
| `evals/thresholds.yaml` | gate thresholds per mode |
| `eval-results/result.json` | `EvalResult` (`schemas/eval-result.v1.schema.json`), uploaded as artifact `eval-results` |
| `.eval-cache/v1/diagnose/<key>.json` | cached report + scores per case |
| `.eval-cache/v1/judge/<key>-<judge sha12>.json` | cached judge verdicts |
| `ledger.jsonl` on branch `eval-history` | one `LedgerEntry` per nightly run (read by the Console) |

## Reading a failing gate

1. Open the job summary or the PR comment (marker `<!-- pipelinedoctor:evals v1 -->`): the table
   shows each metric against its threshold and a per-case breakdown.
2. A single wrong category → look at that case's report in the `eval-results` artifact.
3. `evidence_validity` dropped → the model is quoting lines that are not in the excerpt or
   paraphrasing them; tighten the prompt (as a new version).
4. `llm_coverage` dropped with many `rules_fallback` rows → provider trouble, re-run later.
5. Nightly fails with no code change → silent model drift. That is exactly what the nightly run
   is for; the Console's eval ledger shows when it started.
