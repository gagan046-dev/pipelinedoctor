#!/usr/bin/env bash
# pd-runner entrypoint.
#   register    configure the runner (token read from $RUNNER_TOKEN, never from argv)
#   unregister  remove the registration (token from $RUNNER_TOKEN)
#   anything else is executed as-is (default: ./run.sh)
set -euo pipefail

REPO_URL="${RUNNER_REPO_URL:-https://github.com/gagan046-dev/pipelinedoctor}"
RUNNER_NAME="${RUNNER_NAME:-pd-laptop}"
RUNNER_LABELS="${RUNNER_LABELS:-pd-prod}"

if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  echo "pd-runner: WARNING cannot reach the Docker engine through /var/run/docker.sock." >&2
  echo "pd-runner: see runner/README.md (socket group / user: root fallback)." >&2
fi

case "${1:-}" in
  register)
    : "${RUNNER_TOKEN:?RUNNER_TOKEN must be set (registration token)}"
    # --replace keeps the same runner name when re-registering after a volume reset.
    exec ./config.sh --url "$REPO_URL" --token "$RUNNER_TOKEN" --name "$RUNNER_NAME" \
      --labels "$RUNNER_LABELS" --work _work --unattended --replace
    ;;
  unregister)
    : "${RUNNER_TOKEN:?RUNNER_TOKEN must be set (removal token)}"
    exec ./config.sh remove --token "$RUNNER_TOKEN"
    ;;
  ./run.sh | run.sh)
    if [[ ! -f .runner ]]; then
      echo "pd-runner: not registered yet - run runner/register.ps1 first." >&2
      exit 78
    fi
    exec ./run.sh
    ;;
  *)
    exec "$@"
    ;;
esac
