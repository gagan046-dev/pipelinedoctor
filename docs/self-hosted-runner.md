# Self-hosted runner on the laptop

Production is the laptop, so the production deploy job must run *on* the laptop.
A self-hosted GitHub Actions runner does that: it opens an outbound HTTPS long-poll to GitHub,
no inbound port and no public server needed.

## 1. Decision: a Linux runner in a container

| Option | Verdict |
|---|---|
| Native Windows runner service | jobs would run in PowerShell/Windows paths, unlike every hosted job; the service account cannot reach Docker Desktop's engine |
| **Linux runner container on Docker Desktop** (chosen) | jobs run bash on Linux like hosted runners, the same `bluegreen.sh` works everywhere, the runner is a versioned, disposable image |

Image: `runner/Dockerfile` = `ghcr.io/actions/actions-runner:2.337.0` (pinned by digest) plus the
`docker compose` plugin v5.5.1, whose checksum is verified against the release's
`checksums.txt` during the build. The image already contains git, curl, jq and the docker CLI.

## 2. Setup (PowerShell, from the repo root)

```powershell
.\runner\register.ps1          # builds pd-runner:local, asks you to paste a registration token
# or let gh create the token (needs admin on the repo; token never printed):
.\runner\register.ps1 -UseGh
.\runner\start.ps1             # starts the container, shows GitHub's view of the runner
```

Where the token comes from: GitHub -> repo **Settings -> Actions -> Runners -> New self-hosted
runner**, the value after `--token`. It is valid for one hour and only for registering.
`register.ps1` reads it with `Read-Host -AsSecureString`, puts it in the environment of one
`docker compose run --rm` container (`-e RUNNER_TOKEN` copies the variable without putting it
on a command line) and clears it afterwards. Nothing is written to the repo.

Check:

```powershell
gh api repos/gagan046-dev/pipelinedoctor/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'
```

Expected: `pd-laptop`, `online`, labels `self-hosted, Linux, X64, pd-prod`.

Day to day:

```powershell
.\runner\stop.ps1      # nothing can run on the laptop while it is stopped
.\runner\start.ps1
.\runner\remove.ps1 -UseGh -DeleteVolume   # unregister and delete its credentials
docker logs pd-runner --tail 50
```

## 3. How it is wired (`runner/compose.yml`)

* `pd-runner-home:/home/runner` - named volume, first filled from the image. Keeps the
  registration (`.runner`, `.credentials`), the `_work` checkouts and the runner's self-updates.
  Consequence: a newer `pd-runner:local` image does not replace the runner binaries already in
  the volume (the runner updates itself); `remove.ps1 -DeleteVolume` + `register.ps1` starts clean.
* `/var/run/docker.sock` - jobs drive Docker Desktop's engine. Containers they start are
  **siblings** of the runner, not children; that is why the deploy uses no bind mounts
  (see `docs/deployment.md`).
* `group_add: ["0"]` - on Docker Desktop the socket appears as `root:root 0660` inside
  containers, so the `runner` user (uid 1001) gets group 0. Verified: `docker version` works
  inside. If it ever does not, the documented fallback is `user: root` plus
  `RUNNER_ALLOW_RUNASROOT: "1"`.
* `no-new-privileges` - the base image gives `runner` passwordless sudo; this flag makes sudo
  fail ("no new privileges flag is set"). Our deploy jobs never need root.
* `restart: "no"`, no ports: after a reboot the runner stays offline until `start.ps1`.
* The entrypoint refuses to start `run.sh` before registration (exit 78) and only accepts the
  token from the `RUNNER_TOKEN` variable.

Job labels: `runs-on: [self-hosted, pd-prod]`. GitHub adds `self-hosted`, `Linux`, `X64`
automatically; `pd-prod` is ours.

## 4. Security: a self-hosted runner on a PUBLIC repo

**The risk.** Anyone can fork a public repo and open a pull request that edits a workflow to
`runs-on: [self-hosted, pd-prod]`. If such a job ran, it would execute arbitrary code on the
laptop with the Docker socket, which is root on the Docker Desktop VM: it could read every
container, volume and image, start privileged containers, or wait for the next deploy.

**Mitigations, layered:**

1. Only `deploy.yml` job `production` and `rollback.yml` job `rollback` may use `self-hosted`.
   Neither workflow has a `pull_request`, `pull_request_target`, `issue_comment` or
   `workflow_run` trigger. `scripts/check_workflows.py` enforces this in the required
   `workflow-policy` check, so such a change can never be **merged**.
   **But a check does not stop a job from being queued.** A pull request that ADDS a workflow
   with `on: pull_request` + `runs-on: [self-hosted, pd-prod]` runs that workflow from the PR
   branch, at the same time as `workflow-policy` fails, and the environment gate does not apply
   (the attacker's job simply declares no environment). The guards that actually stop it are
   number 3 (fork PRs wait for your approval) and number 6 (an offline runner picks up nothing).
2. Both jobs use `environment: production`: GitHub holds the job until gagan046-dev approves it,
   and the environment only accepts `main` and `v*` tags (`scripts/setup-environments.ps1`).
3. Fork PR workflows require approval for all external contributors
   (`scripts/repo-settings.ps1`, applied before the repository received its first PR), so a
   stranger's PR runs nothing until you click approve - read the diff of `.github/` before you do.
4. The job checks out trusted `main` (`persist-credentials: false`) and deploys digests that
   were cosign-verified and attestation-verified on a GitHub-hosted runner first.
5. `docker/login-action` logs out in its post step, so no GHCR credential stays in the volume.
6. Stop the runner when you are not deploying (`stop.ps1`): an offline runner cannot be abused,
   and queued production jobs simply wait. `runner/compose.yml` uses `restart: "no"`, so a
   reboot never brings it back on its own.

**Residual risk (accepted, documented):** anything that does reach the runner owns Docker
Desktop. Never add a label that other workflows use, never remove the environment protection,
and keep the runner stopped by default.

**Next hardening step (not done yet):** register the runner as *ephemeral* (`--ephemeral`, or a
JIT config from `POST repos/{repo}/actions/runners/generate-jitconfig`) and re-register it in
`start.ps1`, so each registration runs at most one job and nothing persists between jobs.

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| Job stays "Waiting for a runner to pick up this job" | `.\runner\start.ps1`; labels must include `pd-prod` |
| Job waits on "Review deployments" | approve it in the run page (environment `production`) |
| `pd-runner: not registered yet` and the container restarts | run `.\runner\register.ps1`, then `start.ps1` |
| `Cannot connect to the Docker daemon` inside a job | Docker Desktop not running, or apply the `user: root` fallback above |
| Registration "token expired" | tokens live one hour; run `register.ps1` again |
| Runner shows offline after a laptop reboot | expected (`restart: "no"`): run `.\runner\start.ps1` when you want to deploy |
