# syntax=docker/dockerfile:1
# PipelineDoctor engine image. Also the image of the Docker container action (action.yml).
#
#   docker build -t pipelinedoctor:dev .
#   docker run --rm pipelinedoctor:dev version

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# ---- builder: resolve the locked dependencies into /app/.venv -----------------------------
FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

# Dependencies first, from the lock file only: this layer is reused until uv.lock changes,
# so editing engine code does not re-download litellm and friends.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project --no-editable

# README.m[d] / LICENS[E]: the glob lets COPY succeed whether or not those files exist.
COPY pyproject.toml uv.lock README.m[d] LICENS[E] ./
COPY src ./src
COPY prompts ./prompts
# --no-editable installs the package itself into site-packages, so /src is not needed later.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime: python + the venv, nothing else -----------------------------------------------
FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS runtime

ARG VERSION=0.0.0-dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="pipelinedoctor" \
      org.opencontainers.image.description="Diagnoses failed GitHub Actions runs and posts the diagnosis on the pull request" \
      org.opencontainers.image.source="https://github.com/gagan046-dev/pipelinedoctor" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# No USER instruction on purpose: GitHub runs Docker container actions as the image's
# default user and mounts /github/workspace owned by the runner; a non-root user could not
# write the report there. (Justified in .trivyignore.yaml.)
# A one-shot CLI has no long-running process to probe, so no HEALTHCHECK.
HEALTHCHECK NONE
ENTRYPOINT ["pipelinedoctor"]
CMD ["--help"]
