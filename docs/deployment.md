# Deployment: staging, blue-green production, rollback

This page explains how the PipelineDoctor Console gets from a signed image in GHCR to
`http://127.0.0.1:8090` on the laptop, and what happens when a release is bad.
Workflows: `.github/workflows/deploy.yml`, `.github/workflows/rollback.yml`.
Scripts (this page): `deploy/`.

```
deploy/
  compose.staging.yml     ephemeral staging (GitHub-hosted runner)
  compose.prod.yml        production: proxy + console-blue + console-green (project pd-prod)
  proxy/                  nginx-unprivileged image that does the colour switch
  scripts/bluegreen.sh    deploy | rollback | status
  scripts/smoke.sh        runs the console's smoke module inside the compose network
  scripts/annotate-deployment.sh   writes the DORA status on the GitHub Deployment
  local/deploy-local.ps1  the same blue-green flow on your laptop, without GitHub
```

## 1. The path of a release

| Job | Where | What it proves |
|---|---|---|
| `resolve` | GitHub-hosted | the tag resolves to a digest, the digest is cosign-signed by *our* workflow and has SLSA provenance |
| `staging` | GitHub-hosted | the exact digests start, become healthy and pass the smoke test behind the real proxy |
| `production` | laptop (self-hosted, `pd-prod`) | after your approval: blue-green switch, smoke, soak, automatic rollback on failure |

Everything after `resolve` uses `name@sha256:...` references, never tags: what was tested in
staging is byte-for-byte what runs in production.

## 2. Staging (ephemeral)

```bash
PD_CONSOLE_IMAGE=ghcr.io/gagan046-dev/pipelinedoctor-console@sha256:... \
PD_PROXY_IMAGE=ghcr.io/gagan046-dev/pipelinedoctor-proxy@sha256:... \
  docker compose -p pd-staging -f deploy/compose.staging.yml up -d --wait

bash deploy/scripts/smoke.sh --network pd-staging_default --base-url http://pd-proxy:8080 \
  --image "$PD_CONSOLE_IMAGE" --expect-version 0.2.0 --expect-color staging --require-github

docker compose -p pd-staging -f deploy/compose.staging.yml down -v   # same two env vars needed
```

* `${PD_CONSOLE_IMAGE:?...}` makes compose refuse to start without an explicit image: staging
  must never silently fall back to `latest`.
* `up --wait` returns only when every service with a healthcheck is `healthy`.
* The proxy has the network alias `pd-proxy`, so staging and production smoke tests use the
  same URL. The smoke test runs **inside** the compose network (`docker run --network ...`),
  so it does not care which host ports are free.

## 3. Production: blue-green on the laptop

```
              127.0.0.1:8090
                    |
             +-------------+   /etc/nginx/pd (volume pd-proxy-state)
             |  pd-proxy   |   active-color = green
             +------+------+   upstream.conf = set $pd_backend http://console-green:8000;
          live |         . idle (stopped, previous version)
     +---------v--+   +--v---------+
     |console-green|  |console-blue|
     +------------+   +------------+
```

`bash deploy/scripts/bluegreen.sh deploy --version V --console-image REF --proxy-image REF`:

1. Make sure the proxy runs. It is recreated **only** when its fingerprint
   (`/etc/nginx/pd-build.sha256`: nginx build + config + scripts) differs, because every release
   has a new digest just from its version label and recreating the proxy is the one moment the
   live colour is briefly unreachable.
2. Read the live colour from the proxy volume; the target is the other one.
3. Recreate only `console-<target>` with the new image (`--no-deps`, one service named): the live
   container is never touched.
4. Wait up to `PD_HEALTH_TIMEOUT_S` (90 s) for Docker's healthcheck to say `healthy`.
   Not healthy -> **`failed_before_switch`**, production never saw the new version.
5. `docker exec pd-proxy switch-color.sh <target>`: rewrite `upstream.conf`, `nginx -t`,
   then `nginx -s reload`. A reload is graceful: new workers take new connections while the old
   ones finish their requests. (Measured locally: 207 of 207 requests answered during a switch.)
6. Smoke test through the proxy (expects the new version **and** colour), then a soak of
   `PD_SOAK_PROBES` x `PD_SOAK_INTERVAL_S` (3 x 10 s) `/healthz` probes.
7. Failure -> **automatic rollback**: switch back, verify `/version` reports the old colour,
   exit 1 (`auto_rolled_back`).
8. Success -> stop (not remove) the old colour: `deployed`. Instant manual rollback stays possible.

After a failed deploy the idle slot is restored to the image it held before (created, not
started), so "roll back to the previous version" still means the last good one.

Outputs for the workflow (`$GITHUB_OUTPUT`): `active_color`, `previous_color`, `outcome`.
A table is appended to the job summary.

### Why no bind mounts

The deploy job runs **inside** the runner container and talks to Docker Desktop through the
socket. A bind mount like `./deploy/proxy/pd.conf:/etc/nginx/...` would be resolved by the Docker
engine on *its* filesystem (the Docker Desktop VM), where the job's checkout does not exist.
So config is baked into the proxy image and state lives in named volumes.

### Hardening of the running containers

`read_only: true` root filesystems with a `tmpfs` for `/tmp`, `cap_drop: [ALL]`,
`no-new-privileges`, non-root users (nginx uid 101, console uid 10001), only the proxy publishes
a port and only on `127.0.0.1`.

## 4. Rollback

| You run | What happens | Outcome |
|---|---|---|
| `gh workflow run rollback.yml -f reason="..."` | start the stopped colour, health, switch, smoke, stop the other | `manual_rollback` |
| `gh workflow run rollback.yml -f version=0.2.0 -f reason="..."` | verify that release (resolve-release.yml), then blue-green deploy it by digest | `manual_rollback` |
| (automatic) smoke/soak fails after a switch | switch back immediately | `auto_rolled_back` |

Both need the `production` environment approval again.

## 5. DORA records

`environment: production` makes GitHub create a Deployment for the job. Afterwards
`annotate-deployment.sh` adds one more status whose description is machine-readable:

```
pd:kind=deploy;version=0.2.0;color=green;outcome=auto_rolled_back
```

The Console's DORA page reads only these lines (see `docs/console.md`). Check them with:

```powershell
gh api "repos/gagan046-dev/pipelinedoctor/deployments?environment=production" --jq '.[0].id'
gh api repos/gagan046-dev/pipelinedoctor/deployments/<id>/statuses --jq '.[] | {state, description, created_at}'
```

## 6. Try it locally (no GitHub involved)

```powershell
.\deploy\local\deploy-local.ps1 -Version dev                 # builds console + proxy, deploys
.\deploy\local\deploy-local.ps1 -Version dev2                # watch it switch colour
.\deploy\local\deploy-local.ps1 -Version dev3 -ExpectVersion nope   # smoke fails -> auto rollback
.\deploy\local\deploy-local.ps1 -Action rollback             # back to the stopped colour
.\deploy\local\deploy-local.ps1 -Action status
.\deploy\local\deploy-local.ps1 -Action down -RemoveVolumes
```

Open http://127.0.0.1:8090 or `Invoke-RestMethod http://127.0.0.1:8090/version`; every
response carries an `X-PD-Color` header. Useful while experimenting:

```powershell
docker exec pd-proxy cat /etc/nginx/pd/active-color
docker exec pd-proxy cat /etc/nginx/pd/upstream.conf
docker logs pd-proxy --tail 20      # access log shows color=... per request
```

The local stack and the laptop's real production share the project name `pd-prod` - they are
the same thing. Local deploys simply skip signing, staging and approval.

## 7. Knobs

| Variable | Default | Used by |
|---|---|---|
| `PD_HEALTH_TIMEOUT_S` | 90 | bluegreen.sh: wait for the new colour |
| `PD_SOAK_PROBES` / `PD_SOAK_INTERVAL_S` | 3 / 10 | bluegreen.sh: post-switch soak |
| `PD_FORCE_PROXY_RECREATE` | 0 | bluegreen.sh: recreate the proxy even if unchanged |
| `PD_GITHUB_TOKEN` | empty | console containers (token for the GitHub API) |
| `PD_CONSOLE_REPO` | gagan046-dev/pipelinedoctor | console containers |
