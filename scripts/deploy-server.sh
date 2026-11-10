#!/usr/bin/env bash
# Invoked through SSH stdin; all runtime credentials remain on the server.
set -euo pipefail
umask 077
cd "$1"
image=$2
exec 9>.deploy.lock
flock -w 600 9
exec >deploy.log 2>&1
[[ "$image" =~ ^ghcr.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]
test -f .env
test -f compose.next.yml
chmod 600 .env
export ONW_IMAGE="$image"
compose=(docker compose --project-name open-net-worth --env-file .env -f compose.next.yml)
"${compose[@]}" config --quiet
"${compose[@]}" config --format json | python3 -c '
import json, sys
env = json.load(sys.stdin)["services"]["app"]["environment"]
if any(env.get(key) for key in ("ONW_DATABASE_URL", "DATA_DIR", "CACHE_DIR", "LOGS_DIR")):
    sys.exit("Deployment supports the default SQLite instance only; remove path/database overrides.")
'
"${compose[@]}" pull app

# Never merge a deployment into a differently named/external existing installation.
# An existing install must be deliberately migrated to this project and these volumes first.
existing=$(docker ps -aq --filter 'name=^/open-net-worth$')
if [ -n "$existing" ]; then
  project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$existing")
  [ "$project" = open-net-worth ] || { echo 'Migrate the existing Compose project first.'; exit 1; }
  for mapping in 'open-net-worth-data /home/onw/.open-net-worth' \
    'open-net-worth-codex /home/onw/.codex' 'open-net-worth-claude /home/onw/.claude' \
    'open-net-worth-opencode /home/onw/.local/share/opencode'; do
    docker inspect --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}' "$existing" \
      | grep -Fx -- "$mapping" >/dev/null || { echo 'Migrate the existing volumes first.'; exit 1; }
  done
fi
# Stop writers before snapshotting SQLite, attachments and CLI credentials together.
# A failed update stays stopped/unhealthy; never auto-downgrade a migrated database.
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
if [ -n "$existing" ]; then
  docker inspect --format '{{.Config.Image}}' "$existing" > previous-image
  docker stop "$existing" >/dev/null
fi
printf 'snapshotting\n' > backup-status
for volume in open-net-worth-data open-net-worth-codex open-net-worth-claude open-net-worth-opencode; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    docker run --rm --user 0 --entrypoint tar \
      -v "$volume:/source:ro" "$image" -C /source -czf - . > "backups/$stamp-$volume.tgz"
  fi
done
printf 'complete %s\n' "$stamp" > backup-status
if [ -f compose.yml ]; then cp compose.yml compose.previous.yml; fi
mv compose.next.yml compose.yml
printf '%s\n' "$image" > desired-image
compose=(docker compose --project-name open-net-worth --env-file .env -f compose.yml)
"${compose[@]}" up -d --no-build --wait --wait-timeout 180 app
printf '%s\n' "$image" > current-image
