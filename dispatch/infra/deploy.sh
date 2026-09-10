#!/usr/bin/env bash
# Deploy (or redeploy) the production stack on the homelab box.
#
#   ./deploy.sh              # build what changed, (re)create, keep the rest
#   ./deploy.sh api ui       # only these services
#
# Per-app settings come from ./.env. Shared secrets (the ones several apps
# on the box use) come from the homelab SOPS store and are injected into
# compose's environment for this one command via `sops exec-env`, so they
# never sit in plaintext on disk. Add a file to SHARED to pull it in; a file
# that does not exist is skipped, so a fresh box without Mailgun still
# deploys (magic links stay off until it appears).
set -euo pipefail
cd "$(dirname "$0")"
HOMELAB_SECRETS=${HOMELAB_SECRETS:-$HOME/src/homelab/secrets}
SHARED=(mailgun.enc.env)

cmd=(docker compose -f docker-compose.yml -f docker-compose.prod.yml
     --profile all --profile tunnel up -d --build "$@")
run="${cmd[*]}"
for f in "${SHARED[@]}"; do
  if [ -f "$HOMELAB_SECRETS/$f" ]; then
    run="sops exec-env '$HOMELAB_SECRETS/$f' \"$run\""
  else
    echo "  (no $HOMELAB_SECRETS/$f -- deploying without it)" >&2
  fi
done
eval "$run"
