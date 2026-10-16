# demo/break - failures on purpose

`apply.py` puts one **real** defect into the demo (or into `.github/workflows/demo-ci.yml`)
so that Demo CI fails for a genuine reason and PipelineDoctor can diagnose it on the PR.

```powershell
python demo/break/apply.py --list               # what can break
python demo/break/apply.py --dry-run oom        # show the diff, change nothing
.\demo\scripts\break.ps1 -Mode oom              # branch + apply + commit + push + draft PR
.\demo\scripts\cleanup-breaks.ps1               # close those PRs, delete the branches
```

| mode | what changes | failing job / step | expected category |
|---|---|---|---|
| `test` | `apply_tax` truncates (`ROUND_DOWN`) | quality / test | test |
| `lint` | unused `import os`, unformatted line in `money.py` | quality / lint | lint |
| `type` | `format_money(...) -> int` | quality / typecheck | lint |
| `dependency` | `typing-extensions==4.1.1` (psycopg needs >= 4.6) | every install step | dependency |
| `missing-module` | `import humanize` (never declared) | quality / test, typecheck | dependency |
| `conflict` | `<<<<<<<` markers in `totals.py` | quality / lint, test | build |
| `docker` | `COPY config/ ./config/` (no such dir) | docker / Build demo image | docker |
| `docker-base` | `FROM python:3.12.99-slim` | docker / Build demo image | docker |
| `timeout` | integration test sleeps 400 s | integration / Run integration tests (3 min limit) | flaky |
| `oom` | unit test allocates 1 GiB | docker / Run unit tests inside the image (exit 137) | infrastructure |
| `db` | `DEMO_DB_PORT: "5433"` | integration / Run integration tests | infrastructure |
| `permission` | `gh api -X POST .../labels` with `contents: read` | ops / Read repository metadata (HTTP 403) | secret_permission |
| `secret` | drops `--optional-token` | ops / Publish dry-run | secret_permission |
| `yaml` | invalid YAML after the `yaml` anchor | none: startup failure | config |

Rules the tool follows:

* It matches exact anchor text (`# pd-break-anchor: <mode>` in the workflow, plain code lines
  in the demo). If the anchor moved, it stops with "anchor text not found" instead of guessing.
* It is idempotent and refuses to run on `main`.
* New files come from `patches/<mode>/*.txt` so the breakage is readable before it is applied.

Note on `yaml`: a workflow that fails to parse produces a run with **no jobs**
(`startup_failure`). PipelineDoctor then diagnoses from run metadata only.
