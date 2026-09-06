#!/usr/bin/env bash
# Create or update a user in infra/auth/htpasswd for the dashboard's basic
# auth. Needs only openssl. Prompts for the password; never echoes it.
#
#   ./make-htpasswd.sh alice
set -euo pipefail
cd "$(dirname "$0")"
user="${1:?usage: make-htpasswd.sh <username>}"
read -rsp "password for $user: " pw; echo
read -rsp "again: " pw2; echo
[ "$pw" = "$pw2" ] || { echo "passwords differ" >&2; exit 1; }
hash=$(openssl passwd -apr1 "$pw")
touch auth/htpasswd
grep -v "^${user}:" auth/htpasswd > auth/htpasswd.tmp || true
echo "${user}:${hash}" >> auth/htpasswd.tmp
mv auth/htpasswd.tmp auth/htpasswd
chmod 644 auth/htpasswd
echo "  $(wc -l < auth/htpasswd) user(s) in auth/htpasswd -- restart the ui container to apply"
