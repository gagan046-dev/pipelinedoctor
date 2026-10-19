# syntax=docker/dockerfile:1
# PipelineDoctor engine image. Also the image of the Docker container action (action.yml).
#
#   docker build -t pipelinedoctor:dev .
#   docker run --rm pipelinedoctor:dev version

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

# ---- builder: resolve the locked dependencies into /app/.venv -----------------------------
FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS builder
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
FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime

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
