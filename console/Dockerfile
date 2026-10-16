# syntax=docker/dockerfile:1
# PipelineDoctor Console image. Three stages:
#   fonts   - Node copies the self-hosted @fontsource files (build-time only, never shipped)
#   builder - uv installs the locked dependencies + the console into /app/.venv
#   runtime - slim Python, non-root user, only the virtualenv

# --platform=$BUILDPLATFORM: font files are architecture-independent, so this stage runs natively
# once instead of under QEMU for every target platform of a multi-arch build.
FROM --platform=$BUILDPLATFORM node:22.23.3-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS fonts
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY scripts/copy-fonts.mjs scripts/
RUN node scripts/copy-fonts.mjs

FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY --from=fonts /build/src/pipelinedoctor_console/static/fonts ./src/pipelinedoctor_console/static/fonts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime
ARG VERSION=0.0.0-dev
ARG GIT_SHA=unknown
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PD_CONSOLE_VERSION=$VERSION \
    PD_GIT_SHA=$GIT_SHA \
    PD_CONSOLE_CACHE_DIR=/var/cache/pd
RUN groupadd --system --gid 10001 console \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin console \
    && mkdir -p /var/cache/pd \
    && chown 10001:10001 /var/cache/pd
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"]
CMD ["uvicorn", "pipelinedoctor_console.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
