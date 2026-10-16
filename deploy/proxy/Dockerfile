# syntax=docker/dockerfile:1
# PipelineDoctor blue-green switch: nginx that forwards to console-<active colour>.
# The active colour lives in a named volume mounted at /etc/nginx/pd, so it survives
# proxy restarts and image upgrades.
FROM nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07

USER root
COPY --chmod=0644 nginx.conf /etc/nginx/nginx.conf
COPY --chmod=0644 pd.conf /etc/nginx/conf.d/default.conf
COPY --chmod=0755 switch-color.sh /usr/local/bin/switch-color.sh
COPY --chmod=0755 entrypoint.sh /usr/local/bin/pd-proxy-entrypoint.sh
# A named volume is initialised from the image's directory (including ownership) the first
# time it is mounted, so creating it here as uid 101 makes the volume writable by nginx.
# pd-build.sha256 fingerprints what actually changes proxy behaviour (nginx build, base OS,
# config, scripts). Every release gets a new image digest because of its version label;
# bluegreen.sh compares this fingerprint instead and only recreates the proxy (a short
# blip for the live colour) when it really differs.
# apk upgrade: the pinned base can lag behind Alpine security fixes (Trivy found fixable HIGH
# CVEs in libexpat, pcre2 and tiff); the trade-off is a less reproducible layer.
RUN apk upgrade --no-cache \
 && mkdir -p /etc/nginx/pd \
 && chown 101:101 /etc/nginx/pd \
 && { nginx -V 2>&1; cat /etc/alpine-release; apk info -v | sort; \
      sha256sum /etc/nginx/nginx.conf /etc/nginx/conf.d/default.conf /usr/local/bin/switch-color.sh \
                /usr/local/bin/pd-proxy-entrypoint.sh; } | sha256sum | cut -d' ' -f1 > /etc/nginx/pd-build.sha256
USER 101

EXPOSE 8080
VOLUME ["/etc/nginx/pd"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/proxy-healthz >/dev/null || exit 1
STOPSIGNAL SIGQUIT
ENTRYPOINT ["/usr/local/bin/pd-proxy-entrypoint.sh"]

# Last on purpose: build args invalidate the cache of every later instruction, so keeping
# them at the end lets a version bump reuse all layers above.
ARG VERSION=0.0.0-dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="pipelinedoctor-proxy" \
      org.opencontainers.image.description="nginx blue-green switch in front of the PipelineDoctor Console" \
      org.opencontainers.image.source="https://github.com/gagan046-dev/pipelinedoctor" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"
