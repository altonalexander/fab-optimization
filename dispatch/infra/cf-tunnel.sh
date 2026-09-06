#!/usr/bin/env bash
# Cloudflare Tunnel from the command line, so adding a hostname is one call.
#
# Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment
# (token permissions: Account/Cloudflare Tunnel:Edit, Zone/Zone:Read,
# Zone/DNS:Edit). With SOPS on the homelab box:
#
#   sops exec-env ~/src/homelab/secrets/cloudflare.enc.env './cf-tunnel.sh status'
#
#   cf-tunnel.sh status                      tunnels in the account + their routes
#   cf-tunnel.sh create <name>               new tunnel; writes CLOUDFLARE_TUNNEL_TOKEN
#                                            and TUNNEL_ID into ./.env
#   cf-tunnel.sh route <hostname> <service>  add/replace an ingress rule on the
#                                            tunnel in ./.env and create the CNAME
#                                            e.g. route fab.example.com http://ui:80
#   cf-tunnel.sh unroute <hostname>          remove the rule and the CNAME
#
# Routes are REMOTELY managed (stored at Cloudflare), so the running
# cloudflared picks them up live; no restart, no local config.yml.
set -euo pipefail
: "${CLOUDFLARE_API_TOKEN:?}"
CF=https://api.cloudflare.com/client/v4
# Account id: from the environment, else the account owning the first zone
# the token can see (a zone-scoped token cannot list /accounts directly).
A=${CLOUDFLARE_ACCOUNT_ID:-}
if [ -z "$A" ]; then
  A=$(curl -sS -m 30 -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$CF/zones?per_page=1" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["account"]["id"])')
fi
ENV_FILE="$(dirname "$0")/.env"

api() { # method path [json]
  curl -sS -m 30 -X "$1" -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
       -H 'Content-Type: application/json' "$CF$2" ${3:+--data "$3"}
}
ok() { python3 -c 'import sys,json;d=json.load(sys.stdin);
if not d.get("success"): sys.exit("cloudflare: "+"; ".join(e["message"] for e in d["errors"]))
json.dump(d["result"],sys.stdout)'; }

tunnel_id() { grep -oE '^TUNNEL_ID=.*' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true; }
zone_for() { # hostname -> zone id (longest matching zone)
  local h=$1
  local z=$h
  while [[ $z == *.* ]]; do
    id=$(api GET "/zones?name=$z" | ok | python3 -c 'import sys,json;r=json.load(sys.stdin);print(r[0]["id"] if r else "")')
    [ -n "$id" ] && { echo "$id"; return; }
    z=${z#*.}
  done
  echo "no zone in this account for $h" >&2; return 1
}
set_env() { # key value -> ./.env
  touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
  grep -vE "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
  echo "$1=$2" >> "$ENV_FILE.tmp"; mv "$ENV_FILE.tmp" "$ENV_FILE"
}
get_config() { api GET "/accounts/$A/cfd_tunnel/$1/configurations" | ok; }
put_config() { api PUT "/accounts/$A/cfd_tunnel/$1/configurations" "{\"config\":$2}" | ok >/dev/null; }

case "${1:-}" in
  status)
    api GET "/accounts/$A/cfd_tunnel?is_deleted=false" | ok | python3 -c '
import sys,json
for t in json.load(sys.stdin):
    print(f"{t["name"]:24} {t["id"]}  {t.get("status","?"):10} {len(t.get("connections") or [])} connector(s)")'
    tid=$(tunnel_id); if [ -n "$tid" ]; then
      echo "--- routes on $tid (this .env)"
      get_config "$tid" | python3 -c '
import sys,json;c=json.load(sys.stdin).get("config") or {}
for r in c.get("ingress",[]): print(f"  {r.get("hostname","<catch-all>"):40} -> {r["service"]}")'
    fi ;;
  create)
    name=${2:?name}
    secret=$(openssl rand -base64 32)
    res=$(api POST "/accounts/$A/cfd_tunnel" "$(printf '{"name":"%s","tunnel_secret":"%s","config_src":"cloudflare"}' "$name" "$secret")" | ok)
    tid=$(echo "$res" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
    token=$(api GET "/accounts/$A/cfd_tunnel/$tid/token" | ok | python3 -c 'import sys,json;print(json.load(sys.stdin))')
    put_config "$tid" '{"ingress":[{"service":"http_status:404"}]}'
    set_env TUNNEL_ID "$tid"; set_env CLOUDFLARE_TUNNEL_TOKEN "$token"
    echo "created tunnel $name ($tid); token written to $ENV_FILE" ;;
  route|unroute)
    host=${2:?hostname}; svc=${3:-}; tid=$(tunnel_id); [ -n "$tid" ] || { echo "no TUNNEL_ID in $ENV_FILE; run create first" >&2; exit 1; }
    [ "$1" = route ] && [ -z "$svc" ] && { echo "usage: route <hostname> <service>" >&2; exit 1; }
    cfg=$(get_config "$tid" | python3 -c '
import sys,json;c=json.load(sys.stdin).get("config") or {}
ing=[r for r in c.get("ingress",[]) if r.get("hostname") and r["hostname"]!=sys.argv[2]]
if sys.argv[1]=="route": ing.append({"hostname":sys.argv[2],"service":sys.argv[3]})
ing.append({"service":"http_status:404"}); c["ingress"]=ing; print(json.dumps(c))' "$1" "$host" "$svc")
    put_config "$tid" "$cfg"
    zid=$(zone_for "$host")
    rid=$(api GET "/zones/$zid/dns_records?name=$host" | ok | python3 -c 'import sys,json;r=json.load(sys.stdin);print(r[0]["id"] if r else "")')
    if [ "$1" = route ]; then
      body=$(printf '{"type":"CNAME","name":"%s","content":"%s.cfargotunnel.com","proxied":true,"ttl":1,"comment":"cloudflared tunnel (cf-tunnel.sh)"}' "$host" "$tid")
      if [ -n "$rid" ]; then api PUT "/zones/$zid/dns_records/$rid" "$body" | ok >/dev/null; else api POST "/zones/$zid/dns_records" "$body" | ok >/dev/null; fi
      echo "routed https://$host -> $svc (tunnel $tid)"
    else
      [ -n "$rid" ] && api DELETE "/zones/$zid/dns_records/$rid" | ok >/dev/null
      echo "removed $host"
    fi ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
