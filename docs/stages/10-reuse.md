# Stage 10 - Reuse

**Tag:** `stage-10` · **Files:** `.github/workflows/reusable-python-ci.yml`,
`.github/actions/setup-python-env/action.yml`, final `ci.yml` and `demo-ci.yml`,
`.github/CODEOWNERS`, `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/**`

## Two kinds of reuse

| | Composite action | Reusable workflow |
|---|---|---|
| File | `.github/actions/setup-python-env/action.yml` | `.github/workflows/reusable-python-ci.yml` |
| Called with | a **step**: `- uses: ./.github/actions/setup-python-env` | a **job**: `jobs.engine.uses: ./.github/workflows/reusable-python-ci.yml` |
| Runs | inside the caller's job (same VM) | its own jobs, own runners, own matrix |
| Good for | "set up Python + deps with caching" | "this is what Python CI means here" |
| Inputs | `inputs:` strings | typed `inputs:` (string/number/boolean), `secrets:`, `outputs:` |

The reusable workflow serves three projects with different tools: the engine and the console
(uv, `uv.lock`) and the demo (pip, `requirements-dev.txt`), each with its own matrix passed as
a JSON string and turned into a matrix with `fromJSON(inputs.matrix)`.

Things worth noticing:

* A caller job with `uses:` has no `steps` and no `timeout-minutes`; the called jobs have them.
* The called workflow can never get more `permissions` than the calling job grants.
* Check names become `engine / test (ubuntu-latest, 3.12)`. Required checks therefore point at
  the stable aggregate `ci-ok`, not at matrix legs.
* Matrix jobs share **one** output; only one leg (`ubuntu-latest` + `3.12`) writes `coverage`.
* `./` references resolve against the caller's checkout, so the composite action needs
  `actions/checkout` first.

## Repository templates

* `CODEOWNERS` - `* @gagan046-dev` requests the owner's review on every PR.
* `pull_request_template.md` - Conventional Commit title reminder + checklist (prompt
  immutability, SHA pinning, no secrets).
* `ISSUE_TEMPLATE/bug.yml` (issue form), `config.yml` (no blank issues, private security reports).

## After this stage (integration)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\repo-settings.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup-environments.ps1
powershell -ExecutionPolicy Bypass -File scripts\apply-ruleset.ps1
gh api repos/gagan046-dev/pipelinedoctor/rules/branches/main --jq '.[].type'
```
