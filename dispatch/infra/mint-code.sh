#!/usr/bin/env bash
# Mint an access code from the box (bootstrap: the first admin needs a way in
# before any admin exists). Usage: ./mint-code.sh "note" [email]
# An email from the admin domain makes the resulting session an admin one.
set -euo pipefail
cd "$(dirname "$0")"
note=${1:-bootstrap}; email=${2:-}
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile all exec -T api python3 - "$note" "$email" <<'PY'
import sys, auth
from flask import Flask
app = Flask(__name__)
with app.test_request_context():
    code = auth._mint(1, sys.argv[1], "mint-code.sh", email=sys.argv[2] or None)[0]
print(code)
PY
