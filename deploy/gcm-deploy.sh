#!/bin/sh
# Forced command for the CI deploy key on the deployment host. Install it
# by hand (not from CI) so the runner can never change what it runs:
#
#   install -m 755 gcm-deploy.sh /usr/local/bin/gcm-deploy
#
# and pin the key in the deploy user's ~/.ssh/authorized_keys:
#
#   restrict,from="<runner ip>",command="/usr/local/bin/gcm-deploy" ssh-ed25519 AAAA... gcm-ci
#
# Expects "deploy <version>" as the SSH command, and the GHCR user and
# token on stdin, one per line.
set -eu

DEPLOY_DIR=${DEPLOY_DIR:-/srv/gtnh-craft-monitor}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8420/}

version=${SSH_ORIGINAL_COMMAND:-}
version=${version#deploy }
if ! printf '%s\n' "$version" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?'; then
    echo "usage: deploy <x.y.z>" >&2
    exit 2
fi

IFS= read -r registry_user
IFS= read -r registry_token

cd "$DEPLOY_DIR"
trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
printf '%s' "$registry_token" |
    docker login ghcr.io -u "$registry_user" --password-stdin >/dev/null

export GCM_TAG="$version"
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f

# --retry-all-errors: right after start the port proxy accepts and then
# resets connections until the app is listening, which plain --retry skips.
curl -fsS -o /dev/null --retry 15 --retry-all-errors --retry-delay 2 "$HEALTH_URL"
echo "deployed $version"
