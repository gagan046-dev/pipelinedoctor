#!/usr/bin/env bash
# Adds an explicit status to the GitHub Deployment that `environment:` created for this run.
# Its description carries the machine-readable outcome the Console's DORA metrics read:
#   pd:kind=<deploy|manual_rollback>;version=<v>;color=<c>;outcome=<...>
#
# Env: GH_TOKEN, ENVIRONMENT, KIND, VERSION, OUTCOME, COLOR (+ GITHUB_REPOSITORY, GITHUB_RUN_ID,
#      GITHUB_SERVER_URL, GITHUB_API_URL, GITHUB_SHA provided by Actions).
# Requires curl and jq (present on GitHub-hosted runners and in the actions-runner image).
set -euo pipefail

for tool in curl jq; do
  command -v "$tool" >/dev/null || { echo "::error::$tool is required"; exit 1; }
done
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
ENVIRONMENT="${ENVIRONMENT:-production}"
KIND="${KIND:-deploy}"
VERSION="${VERSION:-unknown}"
VERSION="${VERSION#v}"
OUTCOME="${OUTCOME:-}"
COLOR="${COLOR:-}"
api="${GITHUB_API_URL:-https://api.github.com}"
server="${GITHUB_SERVER_URL:-https://github.com}"
run_url="$server/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"

# An empty outcome means bluegreen.sh never ran (e.g. the job failed before it).
[[ -n "$OUTCOME" ]] || OUTCOME="failed_before_switch"
[[ -n "$COLOR" ]] || COLOR="none"

case "$OUTCOME" in
  deployed|manual_rollback) state=success ;;
  auto_rolled_back|failed_before_switch) state=failure ;;
  *) echo "::error::unknown OUTCOME '$OUTCOME'"; exit 1 ;;
esac

gh_api() {
  curl -fsS -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" "$@"
}

deployments=$(gh_api "$api/repos/$GITHUB_REPOSITORY/deployments?environment=$ENVIRONMENT&per_page=10")

deployment_id=""
for id in $(jq -r '.[].id' <<<"$deployments"); do
  # GitHub's own statuses for an environment job link back to the run that created them.
  if gh_api "$api/repos/$GITHUB_REPOSITORY/deployments/$id/statuses?per_page=20" \
      | jq -e --arg run "/runs/$GITHUB_RUN_ID" 'any(.[]; (.log_url // .target_url // "") | contains($run))' >/dev/null; then
    deployment_id="$id"
    break
  fi
done

if [[ -z "$deployment_id" && -n "${GITHUB_SHA:-}" ]]; then
  deployment_id=$(jq -r --arg sha "$GITHUB_SHA" '[.[] | select(.sha == $sha)][0].id // empty' <<<"$deployments")
  [[ -n "$deployment_id" ]] && echo "::warning::matched deployment $deployment_id by sha (no status linked to this run)"
fi

if [[ -z "$deployment_id" ]]; then
  echo "::warning::no '$ENVIRONMENT' deployment found for run $GITHUB_RUN_ID; DORA will fall back to GitHub's own status"
  exit 0
fi

description="pd:kind=$KIND;version=$VERSION;color=$COLOR;outcome=$OUTCOME"
description="${description:0:140}"

body=$(jq -n --arg state "$state" --arg description "$description" --arg log_url "$run_url" \
  --arg env "$ENVIRONMENT" \
  '{state: $state, description: $description, log_url: $log_url, environment: $env,
    environment_url: "http://127.0.0.1:8090", auto_inactive: true}')

gh_api -X POST "$api/repos/$GITHUB_REPOSITORY/deployments/$deployment_id/statuses" \
  -d "$body" | jq -r '"annotated deployment \(.url | split("/") | .[-3]): \(.state) \(.description)"'

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Deployment record"
    echo ""
    echo "| deployment | state | description |"
    echo "|---|---|---|"
    echo "| $deployment_id | $state | \`$description\` |"
  } >>"$GITHUB_STEP_SUMMARY"
fi
