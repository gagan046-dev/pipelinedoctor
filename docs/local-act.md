# Running workflows locally with act

[`act`](https://github.com/nektos/act) reads `.github/workflows/*.yml` and runs the jobs in
Docker containers on your machine. It is great for the fast "did I break the YAML / the
script?" loop, without a commit and push for every try.

## Install (Windows)

Pinned version: **v0.2.89** (released 2026-06-01).

```powershell
winget install --id nektos.act --version 0.2.89 -e
# or
choco install act-cli --version 0.2.89 -y
act --version
```

Docker Desktop must be running. On the first start act asks for a default image size: choose
**Medium** (`catthehacker/ubuntu:act-latest`, about 1.5 GB). The answer is stored in
`%LOCALAPPDATA%\act\actrc`; to pin it per repository create `.actrc` in the repo root:

```
-P ubuntu-latest=catthehacker/ubuntu:act-latest
--container-architecture linux/amd64
```

## Commands

```powershell
act -l                                                   # list jobs act can see, per event
act workflow_dispatch -W .github/workflows/hello.yml --input who=me
act push -W .github/workflows/hello.yml -j greet        # one job
act pull_request -W .github/workflows/ci.yml -j ci-ok   # ci-ok and everything it needs
act pull_request -W .github/workflows/ci.yml -n         # dry run: show the plan only
```

Secrets (never put real ones in the repo): `act --secret-file $env:USERPROFILE\.act-secrets`
where that file outside the repo contains `OPENROUTER_API_KEY=...`. Without it, workflows in
this repo degrade gracefully exactly like on a fork PR.

Event payloads: `act pull_request -e pr-event.json` with a small JSON file such as
`{"pull_request": {"number": 1, "head": {"ref": "x"}, "base": {"ref": "main"}}}`.

## What act cannot do (here)

| Feature | Why |
|---|---|
| `doctor.yml` (`workflow_run`) | needs a real completed run and its logs from the GitHub API |
| OIDC (`id-token: write`): cosign keyless, attestations | no GitHub OIDC provider locally |
| `deploy.yml` production / `rollback.yml` | self-hosted runner + environment approvals |
| `actions/cache`, artifacts between jobs | partially emulated; behaviour differs from GitHub |
| `windows-latest` matrix legs | act runs Linux containers only - skip with `--matrix os:ubuntu-latest` |
| CodeQL, dependency-review, SARIF upload | need GitHub's code scanning APIs |

So: use act for `hello.yml`, `ci.yml`, `demo-ci.yml` (minus Windows), and the shell scripts;
use real runs (`gh run watch`) for everything else.

```powershell
act pull_request -W .github/workflows/ci.yml --matrix os:ubuntu-latest
```
