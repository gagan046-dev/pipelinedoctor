# Stage 01 - Hello workflow

**Tag:** `stage-01` · **File:** `.github/workflows/hello.yml`

## What you learn

| Concept | Where in `hello.yml` |
|---|---|
| **Event** - what starts a run | `on: push` (main) and `on: workflow_dispatch` (button / `gh workflow run`) with an input |
| **Workflow -> jobs -> steps** | two jobs; each job = a fresh VM; each step = a shell process on that VM |
| **Runner** | `runs-on: ubuntu-latest` (GitHub-hosted, deleted after the job) |
| **Contexts and expressions** | `${{ github.event_name }}`, `${{ runner.os }}`, `${{ inputs.who }}` |
| **Workflow commands** | `::group::`, `::notice::` printed to stdout |
| **Step outputs** | `echo "greeting=..." >> "$GITHUB_OUTPUT"` |
| **Job outputs + `needs`** | `report` waits for `greet` and reads `needs.greet.outputs.greeting` |
| **Step summary** | Markdown appended to `$GITHUB_STEP_SUMMARY` appears on the run page |
| **Least privilege** | `permissions: {}`: the token of this workflow can do nothing |

## Things that surprise beginners

* Jobs do **not** share a file system. Data moves between jobs as outputs (small strings) or
  artifacts (files).
* `${{ }}` is expanded **before** the shell runs. Putting event data straight into a `run:`
  script is a script-injection hole; this repo always passes it through `env:` (rule R6 of the
  workflow policy, stage 05).
* `workflow_dispatch` only appears in the UI once the workflow file is on the default branch.

## Commands

```powershell
gh workflow list
gh workflow run hello.yml -f who=Philips
gh run list --workflow hello.yml --limit 5
gh run watch                       # pick the run, follow it live
gh run view <run-id> --log         # full logs
gh run view <run-id> --json jobs --jq '.jobs[] | {name, conclusion}'
```

Locally, without pushing: `act workflow_dispatch -W .github/workflows/hello.yml --input who=me`
(see [local-act.md](../local-act.md)).

## Exercise

1. Add a third job `shout` that `needs: greet` and prints the greeting in upper case.
2. Make `report` need both jobs. Watch the run graph change in the Actions UI.
3. Trigger the workflow with `gh workflow run` and find your input in the summary table.
