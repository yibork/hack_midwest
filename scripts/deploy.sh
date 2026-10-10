#!/usr/bin/env bash
# Deploy origin/main on the production VM. Runs as the deploy user (GitHub Actions or by hand):
#
#   ~/closure-call/scripts/deploy.sh
#
# Builds and restarts the stack, then waits for /api/health. If the new version doesn't come up
# healthy within 120 s it goes back to the previous commit and exits 1.
set -euo pipefail

# Everything is inside main() so bash parses the whole file before `git reset` can rewrite it.
main() {
  local app_dir="$HOME/closure-call" compose="docker compose -f compose.prod.yml" log="$HOME/deploys.log"
  cd "$app_dir"
  [[ -f .env ]] || { echo "missing $app_dir/.env (copy it with scp, chmod 600)" >&2; exit 1; }

  local previous
  previous=$(git rev-parse HEAD)
  git fetch origin main
  git reset --hard origin/main
  local commit
  commit=$(git rev-parse HEAD)
  echo "deploying $(git log -1 --format='%h %s') (previous ${previous:0:7})"

  if $compose up -d --build --remove-orphans && wait_healthy; then
    docker image prune -f >/dev/null
    echo "$(date -u +%FT%TZ) $commit ok" >> "$log"
    echo "DEPLOYED $commit"
    return 0
  fi

  echo "new version unhealthy; rolling back to $previous" >&2
  $compose logs --tail 50 app >&2 || true
  git reset --hard "$previous"
  local result="rolled-back"
  $compose up -d --build --remove-orphans && wait_healthy || result="rollback-unhealthy"
  echo "$(date -u +%FT%TZ) $commit failed; $result to $previous" >> "$log"
  echo "ROLLED BACK to $previous ($result)"
  exit 1
}

# 200 from /api/health (through Caddy) with inference configured. The JSON holds no secrets.
wait_healthy() {
  local body="" deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    if body=$(curl -fsS --max-time 5 http://localhost/api/health 2>/dev/null) &&
       python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("inference") != "configured")' <<<"$body"; then
      echo "healthy: $body"
      return 0
    fi
    sleep 2
  done
  echo "health check failed after 120 s; last response: ${body:-none}" >&2
  return 1
}

main "$@"
