#!/usr/bin/env bash
# Blue-green deployment of the PipelineDoctor Console (compose project pd-prod).
#
#   bluegreen.sh deploy   --version V --console-image REF [--proxy-image REF]
#   bluegreen.sh rollback [--version V --console-image REF]
#   bluegreen.sh status
#
# deploy: start the idle colour with the new image -> wait healthy -> switch the proxy ->
#         smoke + soak through the proxy -> on failure switch back automatically.
# The live colour is never touched until the new one has proven healthy, and the old colour
# is only stopped (not removed), so a manual rollback is one `docker start` + switch away.
#
# Outputs (when $GITHUB_OUTPUT is set): active_color, previous_color, outcome
#   outcome = deployed | auto_rolled_back | failed_before_switch | manual_rollback
#
# Tunables (env): PD_HEALTH_TIMEOUT_S (90), PD_SOAK_PROBES (3), PD_SOAK_INTERVAL_S (10),
#                 PD_COMPOSE_FILE (deploy/compose.prod.yml relative to the repo root).
set -euo pipefail
# Git Bash on Windows would rewrite /etc/... arguments into C:/Program Files/Git/etc/...
export MSYS_NO_PATHCONV=1

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
# ...which is also why paths handed to docker.exe must be Windows-style (C:/...) there.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  repo_root="$(cd "$repo_root" && pwd -W)"
fi
PROJECT=pd-prod
NETWORK="${PROJECT}_default"
PROXY=pd-proxy
COMPOSE_FILE="${PD_COMPOSE_FILE:-$repo_root/deploy/compose.prod.yml}"
HEALTH_TIMEOUT_S="${PD_HEALTH_TIMEOUT_S:-90}"
SOAK_PROBES="${PD_SOAK_PROBES:-3}"
SOAK_INTERVAL_S="${PD_SOAK_INTERVAL_S:-10}"

active_color=""
previous_color=""
outcome=""
mode=""
version=""
console_image=""

log() { printf '[bluegreen] %s\n' "$*"; }
err() { printf '[bluegreen] ERROR: %s\n' "$*" >&2; }
compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }
other_color() { [[ "$1" == blue ]] && echo green || echo blue; }
container_of() { echo "${PROJECT}-console-$1-1"; }

# docker inspect prints an empty line before failing on a missing container, so capture
# first and fall back explicitly.
inspect_console() {
  local out
  out=$(docker inspect -f "$2" "$(container_of "$1")" 2>/dev/null) || out=""
  printf '%s' "$out"
}

container_state() { local s; s=$(inspect_console "$1" '{{.State.Status}}'); echo "${s:-missing}"; }

container_image() { local i; i=$(inspect_console "$1" '{{.Config.Image}}'); echo "${i:--}"; }

container_version() {
  inspect_console "$1" '{{range .Config.Env}}{{println .}}{{end}}' |
    sed -n 's/^PD_CONSOLE_VERSION=//p' | head -1
}

write_outputs() {
  local rc=$?
  if [[ -n "$mode" && "$mode" != status ]]; then
    [[ -n "$outcome" ]] || outcome=failed_before_switch
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
      {
        echo "active_color=$active_color"
        echo "previous_color=$previous_color"
        echo "outcome=$outcome"
      } >>"$GITHUB_OUTPUT"
    fi
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
      {
        echo "### Blue-green $mode"
        echo ""
        echo "| outcome | live colour | previous colour | version | image |"
        echo "|---|---|---|---|---|"
        echo "| \`$outcome\` | $active_color | $previous_color | ${version:-n/a} | \`${console_image:-n/a}\` |"
      } >>"$GITHUB_STEP_SUMMARY"
    fi
    log "outcome=$outcome active_color=$active_color previous_color=$previous_color (exit $rc)"
  fi
}
trap write_outputs EXIT

wait_healthy() {
  local name="$1" timeout="$2" waited=0 status
  while ((waited < timeout)); do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$name" 2>/dev/null || echo missing)
    case "$status" in
      healthy) log "$name is healthy after ${waited}s"; return 0 ;;
      # "unhealthy" = the HEALTHCHECK already failed its retries; waiting longer won't help.
      unhealthy | exited | dead | missing) err "$name is $status after ${waited}s"; return 1 ;;
    esac
    sleep 2
    waited=$((waited + 2))
  done
  err "$name not healthy after ${timeout}s (last status: $status)"
  return 1
}

# Requests through the proxy from inside its own container: exercises the live route
# without depending on the host port.
via_proxy() { docker exec "$PROXY" wget -qO- -T 5 "http://127.0.0.1:8080$1"; }

read_active_color() { docker exec "$PROXY" cat /etc/nginx/pd/active-color | tr -d '[:space:]'; }

switch_to() { docker exec "$PROXY" switch-color.sh "$1"; }

served_color_is() { via_proxy /version 2>/dev/null | grep -Eq "\"color\": ?\"$1\""; }

proxy_fingerprint_of_image() {
  docker run --rm --entrypoint cat "$1" /etc/nginx/pd-build.sha256 2>/dev/null || echo "unknown-image"
}

# The proxy is the one piece that is not doubled, so recreating it costs the live colour a
# second or two. Do it only when it is missing or its fingerprint (see deploy/proxy/Dockerfile)
# differs from the requested image's; PD_FORCE_PROXY_RECREATE=1 overrides.
ensure_proxy() {
  local running want have
  running=$(docker inspect -f '{{.State.Running}}' "$PROXY" 2>/dev/null || true)
  if [[ "$running" != true ]]; then
    log "proxy not running - starting it"
    PD_PROXY_IMAGE="${PROXY_IMAGE:-${PD_PROXY_IMAGE:-}}" compose up -d --no-deps proxy
  elif [[ -n "${PROXY_IMAGE:-}" ]]; then
    want=$(proxy_fingerprint_of_image "$PROXY_IMAGE")
    have=$(docker exec "$PROXY" cat /etc/nginx/pd-build.sha256 2>/dev/null || echo "unknown-running")
    if [[ "$want" != "$have" || "${PD_FORCE_PROXY_RECREATE:-0}" == 1 ]]; then
      log "proxy changed ($have -> $want) - recreating it (active colour is kept in its volume)"
      PD_PROXY_IMAGE="$PROXY_IMAGE" compose up -d --no-deps proxy
    else
      log "proxy unchanged (fingerprint $have) - keeping the running container"
    fi
  fi
  wait_healthy "$PROXY" 60
}

# $3 = compose-up mode: -d (start) or --no-start (only create the container).
start_color() {
  local color="$1" image="$2" mode="${3:--d}" var
  var="PD_IMAGE_$(tr '[:lower:]' '[:upper:]' <<<"$color")"
  log "creating console-$color with $image ($mode)"
  # --no-deps + naming one service: compose touches only this colour, never the live one.
  env "$var=$image" docker compose -p "$PROJECT" -f "$COMPOSE_FILE" \
    up "$mode" --no-deps --force-recreate "console-$color"
}

# A failed deploy used the idle slot, which held the last known-good version. Put that
# version back (created, not started) so `bluegreen.sh rollback` still has it.
restore_idle() {
  local color="$1" image="$2"
  if [[ "$image" == "-" ]]; then
    compose rm -sf "console-$color" >/dev/null 2>&1 || true
    return 0
  fi
  start_color "$color" "$image" --no-start || err "could not restore console-$color to $image"
}

smoke_and_soak() {
  local color="$1" image="$2" expect_version="${3:-}"
  local args=(--network "$NETWORK" --base-url "http://$PROXY:8080" --image "$image" --expect-color "$color")
  [[ -n "$expect_version" ]] && args+=(--expect-version "$expect_version")
  bash "$script_dir/smoke.sh" "${args[@]}" || return 1

  local i
  for ((i = 1; i <= SOAK_PROBES; i++)); do
    sleep "$SOAK_INTERVAL_S"
    if ! via_proxy /healthz >/dev/null; then
      err "soak probe $i/$SOAK_PROBES failed"
      return 1
    fi
    log "soak probe $i/$SOAK_PROBES ok"
  done
}

# Shared by deploy and versioned rollback. $1 = outcome on success.
release() {
  local success_outcome="$1"
  ensure_proxy

  local live target
  live=$(read_active_color)
  target=$(other_color "$live")
  previous_color="$live"
  active_color="$live"
  if [[ "$(container_state "$live")" != running ]]; then
    log "WARNING: live colour $live is not running (first deploy?) - nothing to roll back to"
  fi
  local idle_image
  idle_image=$(container_image "$target")
  log "live=$live ($(container_image "$live")) target=$target (idle slot held $idle_image)"

  start_color "$target" "$console_image"
  if ! wait_healthy "$(container_of "$target")" "$HEALTH_TIMEOUT_S"; then
    docker logs --tail 50 "$(container_of "$target")" >&2 || true
    restore_idle "$target" "$idle_image"
    outcome=failed_before_switch
    err "new version never became healthy; production still on $live"
    return 1
  fi

  switch_to "$target"
  active_color="$target"

  if ! smoke_and_soak "$target" "$console_image" "$version"; then
    err "post-switch checks failed - automatic rollback to $live"
    switch_to "$live"
    active_color="$live"
    if [[ "$(container_state "$live")" == running ]]; then
      if served_color_is "$live"; then
        log "verified: /version through the proxy reports $live again"
      else
        err "rollback switched the proxy but /version does not report $live"
      fi
    fi
    restore_idle "$target" "$idle_image"
    outcome=auto_rolled_back
    return 1
  fi

  if [[ "$(container_state "$live")" == running ]]; then
    compose stop "console-$live"
  fi
  outcome="$success_outcome"
  log "live colour is now $target"
}

rollback_to_idle() {
  ensure_proxy
  local live idle
  live=$(read_active_color)
  idle=$(other_color "$live")
  previous_color="$live"
  active_color="$live"
  if [[ "$(container_state "$idle")" == missing ]]; then
    err "no $idle container to roll back to; pass --version and --console-image"
    return 1
  fi
  log "rolling back from $live to $idle ($(container_image "$idle"))"
  docker start "$(container_of "$idle")" >/dev/null
  if ! wait_healthy "$(container_of "$idle")" "$HEALTH_TIMEOUT_S"; then
    docker stop "$(container_of "$idle")" >/dev/null || true
    outcome=failed_before_switch
    return 1
  fi
  switch_to "$idle"
  active_color="$idle"
  console_image=$(container_image "$idle")
  version=$(container_version "$idle")
  if ! smoke_and_soak "$idle" "$console_image"; then
    err "rolled-back colour failed its checks - switching back to $live"
    switch_to "$live"
    active_color="$live"
    docker stop "$(container_of "$idle")" >/dev/null || true
    outcome=auto_rolled_back
    return 1
  fi
  docker stop "$(container_of "$live")" >/dev/null || true
  outcome=manual_rollback
}

status() {
  local live="(proxy not running)"
  if [[ "$(docker inspect -f '{{.State.Running}}' "$PROXY" 2>/dev/null)" == true ]]; then
    live=$(read_active_color)
  fi
  echo "live colour: $live"
  printf '%-6s %-9s %-10s %s\n' COLOUR STATE VERSION IMAGE
  local c
  for c in blue green; do
    printf '%-6s %-9s %-10s %s\n' "$c" "$(container_state "$c")" "$(container_version "$c")" \
      "$(container_image "$c")"
  done
}

usage() {
  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 64
}

mode="${1:-}"
[[ -n "$mode" ]] || usage
shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) version="${2#v}"; shift 2 ;;
    --console-image) console_image="$2"; shift 2 ;;
    --proxy-image) PROXY_IMAGE="$2"; shift 2 ;;
    -h | --help) usage ;;
    *) err "unknown argument: $1"; usage ;;
  esac
done

case "$mode" in
  deploy)
    [[ -n "$version" && -n "$console_image" ]] || { err "deploy needs --version and --console-image"; exit 64; }
    release deployed
    ;;
  rollback)
    if [[ -n "$console_image" ]]; then
      [[ -n "$version" ]] || { err "--console-image needs --version"; exit 64; }
      release manual_rollback
    else
      rollback_to_idle
    fi
    ;;
  status)
    status
    ;;
  *) usage ;;
esac
