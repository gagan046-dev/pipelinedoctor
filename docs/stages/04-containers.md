# Stage 04 - Container builds

**Tag:** `stage-04` · **Files:** `.github/workflows/container.yml` (stage-04 version),
`Dockerfile`, `console/**`, `deploy/proxy/**`

## What you learn

* **Buildx / BuildKit** (`docker/setup-buildx-action`): parallel multi-stage builds and
  exportable caches.
* **Multi-stage Dockerfiles**: a `builder` stage with uv and build tools, a slim `runtime`
  stage that only receives the finished virtual environment.
* **GitHub Actions layer cache**: `cache-from/cache-to: type=gha,scope=<image>`; `mode=max`
  also caches intermediate stages. One scope per image so the three images do not evict each
  other.
* **Tags and OCI labels** from `docker/metadata-action`:
  * `sha-1a2b3c4` - immutable, traceable to a commit;
  * `edge` - "latest main";
  * (stage 07) `X.Y.Z`, `X.Y`, `latest` for releases.
  Labels such as `org.opencontainers.image.source` link the GHCR package to this repository.
* **Push policy**: PRs build without pushing (proves the Dockerfile works); only `main`
  pushes, with the job's own `GITHUB_TOKEN` and `packages: write` - no registry password.
* **Matrix over images** with `include:` to attach context/file/name to each entry.

## Commands

```powershell
gh run list --workflow container.yml
docker buildx imagetools inspect ghcr.io/gagan046-dev/pipelinedoctor-console:edge
docker pull ghcr.io/gagan046-dev/pipelinedoctor:edge
docker inspect ghcr.io/gagan046-dev/pipelinedoctor:edge --format '{{json .Config.Labels}}'
gh api /users/gagan046-dev/packages?package_type=container --jq '.[].name'
```

New GHCR packages are **private**. Make them public once (Package settings -> Change
visibility) so the Docker action and the laptop can pull without credentials; there is no REST
API for this.

## Exercise

Change one line in `console/` and compare the build time of the next run with a run where
nothing changed: look for `CACHED` in the build log.
