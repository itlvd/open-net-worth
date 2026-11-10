#!/usr/bin/env bash
set -euo pipefail
umask 077

for name in DEPLOY_HOST DEPLOY_USER DEPLOY_PATH DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS IMAGE_DIGEST; do
  if [ -z "${!name:-}" ]; then
    echo "::error::Missing deployment secret/input: $name"
    exit 1
  fi
done
DEPLOY_PORT=${DEPLOY_PORT:-22}
[[ "$DEPLOY_PORT" =~ ^[0-9]+$ && "$DEPLOY_PORT" -ge 1 && "$DEPLOY_PORT" -le 65535 ]]
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.:-]*$ ]]
[[ "$DEPLOY_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]
[[ "$DEPLOY_PATH" =~ ^/[a-zA-Z0-9_/-]+$ && "$DEPLOY_PATH" != / ]]
[[ "$IMAGE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]]
image="ghcr.io/${GITHUB_REPOSITORY,,}@$IMAGE_DIGEST"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$work/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$work/known_hosts"
opts=(-i "$work/key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
      -o "UserKnownHostsFile=$work/known_hosts" -o ConnectTimeout=15 -o LogLevel=ERROR)
# OpenSSH diagnostics can contain the host/address. Keep them out of public logs.
if ! scp -q -P "$DEPLOY_PORT" "${opts[@]}" deploy/production/compose.yml \
  "$DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_PATH/compose.next.yml" >"$work/output" 2>&1; then
  echo '::error::Compose transfer failed. Check SSH secrets and server connectivity privately.'
  exit 1
fi
if ! ssh -p "$DEPLOY_PORT" "${opts[@]}" "$DEPLOY_USER@$DEPLOY_HOST" \
  "bash -s -- '$DEPLOY_PATH' '$image'" < scripts/deploy-server.sh >"$work/output" 2>&1; then
  echo '::error::Deployment failed. Inspect deploy.log and backup-status on the server privately.'
  exit 1
fi
echo 'Deployment completed and passed the container health check.'
