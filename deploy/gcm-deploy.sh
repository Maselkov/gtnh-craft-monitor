#!/bin/sh
# Forced command for the CI deploy keys on the deployment host. Install it
# by hand (not from CI) so the runner can never change what it runs:
#
#   install -m 755 gcm-deploy.sh /usr/local/bin/gcm-deploy
#
# and pin each key in the deploy user's ~/.ssh/authorized_keys, the
# instance named by the forced command, never by the caller:
#
#   restrict,from="<runner ip>",command="/usr/local/bin/gcm-deploy" ssh-ed25519 AAAA... gcm-ci
#   restrict,from="<runner ip>",command="/usr/local/bin/gcm-deploy testing" ssh-ed25519 AAAA... gcm-ci-testing
#
# production deploys releases (x.y.z) into /srv/gtnh-craft-monitor;
# testing deploys main builds (sha-<commit>) into
# /srv/gtnh-craft-monitor-testing. So neither key can deploy the other's
# images, or to the other's instance.
#
# DEPLOY_DIR holds a copy of deploy/docker-compose.yml, .env and data/.
# Each deploy sets GCM_TAG in .env to the version it deployed.
# testing's .env needs its own container name and port, and leaves out
# production's webhooks:
#
#   GCM_CONTAINER_NAME=gtnh-craft-monitor-testing
#   GCM_PORT=8422
#
# Expects "deploy <tag>" as the SSH command, and the GHCR user and
# token on stdin, one per line.
set -eu

instance=${1:-production}
case $instance in
    production)
        default_dir=/srv/gtnh-craft-monitor
        default_url=http://127.0.0.1:8420/
        tag_pattern='[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?'
        usage='deploy <x.y.z>'
        ;;
    testing)
        default_dir=/srv/gtnh-craft-monitor-testing
        default_url=http://127.0.0.1:8422/
        tag_pattern='sha-[0-9a-f]{7,40}'
        usage='deploy sha-<commit>'
        ;;
    *)
        echo "unknown instance: $instance" >&2
        exit 2
        ;;
esac
DEPLOY_DIR=${DEPLOY_DIR:-$default_dir}
HEALTH_URL=${HEALTH_URL:-$default_url}

version=${SSH_ORIGINAL_COMMAND:-}
version=${version#deploy }
# grep matches line by line, so a version with a newline in it would pass
# on one good line and write the rest into .env.
case $version in
    *"
"*)
        echo "usage: $usage" >&2
        exit 2
        ;;
esac
if ! printf '%s\n' "$version" | grep -Eqx "$tag_pattern"; then
    echo "usage: $usage" >&2
    exit 2
fi

IFS= read -r registry_user
IFS= read -r registry_token

cd "$DEPLOY_DIR"
trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
printf '%s' "$registry_token" |
    docker login ghcr.io -u "$registry_user" --password-stdin >/dev/null

# Kept in .env, not only exported, so a plain `docker compose up -d` on the
# host keeps the deployed version instead of falling back to latest.
# Rewritten in place, with no temp file, to keep .env's owner and mode and
# never leave a copy of its secrets behind.
touch .env
kept=$(grep -v '^GCM_TAG=' .env || true)
{ [ -z "$kept" ] || printf '%s\n' "$kept"; echo "GCM_TAG=$version"; } > .env
docker compose pull
docker compose up -d --remove-orphans
# Only this project's images, and only those no container uses: each
# testing deploy leaves the previous sha-* image behind, which a plain
# prune (dangling images only) would keep.
docker image prune -af \
    --filter label=org.opencontainers.image.source=https://github.com/Maselkov/gtnh-craft-monitor

# --retry-all-errors: right after start the port proxy accepts and then
# resets connections until the app is listening, which plain --retry skips.
curl -fsS -o /dev/null --retry 15 --retry-all-errors --retry-delay 2 "$HEALTH_URL"
echo "deployed $version to $instance"
