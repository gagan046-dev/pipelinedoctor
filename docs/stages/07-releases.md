# Stage 07 - Releases and supply-chain security

**Tag:** `stage-07` · **Files:** `release.yml`, `pr-title.yml`, `container.yml` (final),
`release-please-config.json`, `.release-please-manifest.json`

## Conventional Commits -> versions

`pr-title.yml` enforces `type(scope)!: summary` on PR titles. Squash merges use the title as
the commit message, and release-please reads those messages:

| Commit | Bump (before 1.0, `bump-minor-pre-major`) |
|---|---|
| `fix: ...` | patch 0.1.0 -> 0.1.1 |
| `feat: ...` | minor 0.1.0 -> 0.2.0 |
| `feat!: ...` / `BREAKING CHANGE:` | minor before 1.0, major after |
| `docs:`, `ci:`, `chore:` | no release |

## release-please flow

1. Every push to `main` -> release-please opens/updates a **release PR** that bumps the version
   in `pyproject.toml`, `console/pyproject.toml`, both `__init__.py`, `action.yml` (the lines
   marked `# x-release-please-version`) and writes `CHANGELOG.md`.
2. Merging the release PR -> tag `vX.Y.Z` + GitHub Release; output `release_created=true`.
3. `images` calls `container.yml` (reusable) with `version`, `push: true`,
   `platforms: linux/amd64,linux/arm64`, `sign: true`.
4. `release-assets` attaches the SPDX SBOMs to the release; from stage 09 on, `deploy` calls
   `deploy.yml` (the stage-07 commit has no `deploy` job yet: a `uses:` of a workflow file that
   does not exist is a *startup failure* of the whole Release run).
5. release-please also bumps the project version recorded in `uv.lock` and `console/uv.lock`
   (`extra-files` with a TOML jsonpath), so `uv lock --check` stays clean after a release.

**Concurrency and approvals.** Only the `release-please` job has a concurrency group. A
workflow-level group would also cover the `deploy` job, which can wait days for the production
reviewer; meanwhile every later push to `main` would queue behind it, and GitHub keeps only ONE
pending run per group (the others are cancelled). Production deploys are serialised by
`deploy.yml`'s own `deploy-production` group instead.

**Release PR checks need one click.** The release PR is opened by `github-actions[bot]` with
`GITHUB_TOKEN`. On this repo its `pull_request` runs are created but **held** with the status
*action_required* (seen on the first release PR, under the "approval for all external
contributors" setting from `scripts/repo-settings.ps1`), so the required checks stay pending
until someone approves them:

```powershell
$sha = gh pr view <n> --json headRefOid --jq .headRefOid
$runs = gh run list --branch release-please--branches--main--components--pipelinedoctor `
  --json databaseId,headSha,conclusion | ConvertFrom-Json
$runs | Where-Object { $_.headSha -eq $sha -and $_.conclusion -eq 'action_required' } |
  ForEach-Object { gh api -X POST "repos/gagan046-dev/pipelinedoctor/actions/runs/$($_.databaseId)/approve" }
```

(or "Approve and run workflows" on the PR page). Alternatives: merge as admin
(`gh pr merge <n> --squash --admin`, ruleset bypass), or create a fine-grained PAT (this repo
only; Contents + Pull requests: read/write) stored as `RELEASE_PLEASE_TOKEN`, so the PR is opened
by you and its checks start on their own.

## Supply chain, step by step (container.yml with `sign: true`)

| Step | Tool | Proves |
|---|---|---|
| Multi-arch build | QEMU + Buildx | one tag, amd64 + arm64 manifests |
| SBOM | syft (`anchore/sbom-action`), SPDX JSON | what is inside the image |
| Signature | `cosign sign` **keyless** | the image was built by `container.yml` on `main` of this repo |
| SBOM attestation | `cosign attest --type spdxjson` | the SBOM belongs to exactly this digest |
| SLSA provenance | `actions/attest-build-provenance` | how/where it was built (workflow, commit, runner) |
| Verify | `cosign verify`, `gh attestation verify` | consumers can check all of the above |

**Keyless** = `id-token: write` lets the job request an OIDC token from GitHub; Sigstore's
Fulcio exchanges it for a 10-minute certificate whose identity is the workflow path + ref. No
private key exists. Release builds do not *read* the GHA layer cache, so a poisoned cache entry
can never end up inside a signed image.

```powershell
$img = 'ghcr.io/gagan046-dev/pipelinedoctor-console:0.2.0'
cosign verify $img --certificate-identity-regexp '^https://github\.com/gagan046-dev/pipelinedoctor/\.github/workflows/container\.yml@refs/heads/main$' --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify "oci://$img" -R gagan046-dev/pipelinedoctor
docker buildx imagetools inspect $img        # both platforms
gh release view v0.2.0                       # notes + SBOM assets
```
