#!/usr/bin/env bash
# Runs the console's own smoke module (python -m pipelinedoctor_console.smoke) from the image
# under test, attached to the compose network. Staging and production therefore run the same
# checks, and nothing depends on a host port.
#
# Usage: smoke.sh --network NET --base-url URL --image REF [--expect-version V]
#                 [--expect-color C] [--require-github] [--attempts N]
# Exit code: 0 = all checks passed; otherwise the smoke module's exit code (number of failures).
set -euo pipefail
export MSYS_NO_PATHCONV=1

network="" base_url="" image="" attempts=2
smoke_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --network) network="$2"; shift 2 ;;
    --base-url) base_url="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --expect-version) smoke_args+=(--expect-version "${2#v}"); shift 2 ;;
    --expect-color) smoke_args+=(--expect-color "$2"); shift 2 ;;
    --require-github) smoke_args+=(--require-github); shift ;;
    --attempts) attempts="$2"; shift 2 ;;
    *) echo "smoke.sh: unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$network" && -n "$base_url" && -n "$image" ]] || {
  echo "usage: smoke.sh --network NET --base-url URL --image REF [...]" >&2; exit 64; }

rc=0
for ((i = 1; i <= attempts; i++)); do
  echo "smoke: attempt $i/$attempts against $base_url (network $network)"
  if docker run --rm --network "$network" --read-only --tmpfs /tmp \
       --entrypoint python "$image" \
       -m pipelinedoctor_console.smoke --base-url "$base_url" "${smoke_args[@]}"; then
    echo "smoke: PASS"
    exit 0
  else
    rc=$?
  fi
  # One retry absorbs the moment right after an nginx reload; a real defect fails twice.
  [[ $i -lt $attempts ]] && sleep 3
done
echo "smoke: FAIL (exit $rc)" >&2
exit "$rc"
