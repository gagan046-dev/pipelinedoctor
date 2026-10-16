# syntax=docker/dockerfile:1
# Test image for the demo: the unit tests run INSIDE the container in Demo CI
# (`docker run --memory=256m ... python -m pytest`), which is what makes the OOM demo real.
# demo/break/apply.py edits the FROM lines (mode docker-base) and the anchor below (mode docker).

FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
# Same path as in the runtime stage: the editable install (-e .) records /app/src,
# so it keeps working after the venv is copied.
WORKDIR /app
COPY requirements.txt requirements-dev.txt pyproject.toml ./
COPY src ./src
# Only what the in-image test run needs: runtime deps, pytest (version taken from
# requirements-dev.txt, so there is one source of truth) and the package itself.
# Lint and type tools stay out of the image.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt "$(grep -E '^pytest==' requirements-dev.txt)" \
 && pip install --no-deps -e .

FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS runtime
ARG VERSION=0.0.0-dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="pipelinedoctor-demo" \
      org.opencontainers.image.description="Deliberately breakable demo library (invoicer) with its tests" \
      org.opencontainers.image.source="https://github.com/gagan046-dev/pipelinedoctor" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin demo
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests
# pd-break-anchor: docker

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider"
USER 10001:10001
# A test image runs once and exits; there is no service to health-check.
HEALTHCHECK NONE
CMD ["python", "-m", "pytest", "-q"]
