# syntax=docker/dockerfile:1
# Self-hosted GitHub Actions runner for the production deploy job (label pd-prod).
# The official image already ships the runner, git, curl, jq and the docker CLI; we add the
# docker compose plugin, verified against the release's published checksums.
FROM ghcr.io/actions/actions-runner:2.338.0@sha256:4ffadc0002b2581327e06101fc8c06cd189232baf79fe561fac9caeb76f5e807

ARG COMPOSE_VERSION=v5.5.1
ARG TARGETARCH=amd64

LABEL org.opencontainers.image.title="pd-runner" \
      org.opencontainers.image.description="actions-runner 2.337.0 + docker compose for PipelineDoctor production deploys" \
      org.opencontainers.image.source="https://github.com/gagan046-dev/pipelinedoctor" \
      org.opencontainers.image.licenses="MIT"

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN case "$TARGETARCH" in \
      amd64) arch=x86_64 ;; \
      arm64) arch=aarch64 ;; \
      *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && file="docker-compose-linux-${arch}" \
 && base="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}" \
 && cd /tmp \
 && curl -fsSLO "${base}/${file}" \
 && curl -fsSLO "${base}/checksums.txt" \
 && grep -E "[ *]${file}\$" checksums.txt | sha256sum -c - \
 && install -D -m 0755 "${file}" /usr/local/lib/docker/cli-plugins/docker-compose \
 && rm -f "${file}" checksums.txt \
 && docker compose version

COPY --chmod=0755 entrypoint.sh /usr/local/bin/pd-runner-entrypoint.sh

USER runner
WORKDIR /home/runner
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD pgrep -f Runner.Listener >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/pd-runner-entrypoint.sh"]
CMD ["./run.sh"]
