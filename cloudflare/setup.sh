#!/usr/bin/env bash
# One-shot setup for the xinliqiu encrypted-object store.
# Requires: node 18+, python3, a Cloudflare account, and `npx wrangler login`.
set -euo pipefail
cd "$(dirname "$0")"

fail() { printf '\n!! %s\n' "$1" >&2; exit 1; }

command -v node >/dev/null 2>&1 || fail "node 18+ is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

if ! npx wrangler whoami >/dev/null 2>&1; then
  fail "not logged in. Run: npx wrangler login"
fi

echo "==> installing wrangler"
npm install --silent

echo "==> ensuring R2 bucket"
if ! npx wrangler r2 bucket list 2>/dev/null | grep -q 'xinliqiu-objects'; then
  npx wrangler r2 bucket create xinliqiu-objects
else
  echo "    bucket already exists"
fi

echo "==> resolving D1 database"
DB_ID=""
# Try to create; if it already exists, fall back to `d1 list`.
CREATE_OUT=$(npx wrangler d1 create xinliqiu-index 2>&1 || true)
DB_ID=$(printf '%s' "$CREATE_OUT" | sed -n 's/.*database_id = "\([^"]*\)".*/\1/p' | head -1)
if [ -z "${DB_ID:-}" ]; then
  DB_ID=$(npx wrangler d1 list --json 2>/dev/null \
    | python3 -c 'import json,sys
rows=json.load(sys.stdin)
for r in rows:
    if r.get("name")=="xinliqiu-index":
        print(r.get("uuid") or r.get("database_id") or "")
        break' 2>/dev/null || true)
fi
[ -n "${DB_ID:-}" ] || fail "could not determine the D1 database id. Create it with 'npx wrangler d1 create xinliqiu-index' and paste the id into wrangler.toml."

echo "==> writing database_id into wrangler.toml"
python3 - "$DB_ID" <<'PY'
import re, sys
path = 'wrangler.toml'
text = open(path, encoding='utf-8').read()
text = re.sub(r'database_id = "[^"]*"', 'database_id = "%s"' % sys.argv[1], text)
open(path, 'w', encoding='utf-8').write(text)
print('    database_id =', sys.argv[1])
PY

echo "==> applying schema"
npx wrangler d1 execute xinliqiu-index --file=./migrations/0001_init.sql --remote

echo "==> generating a bearer token"
TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
printf '%s' "$TOKEN" | npx wrangler secret put XINLIQIU_TOKEN >/dev/null

cat <<MSG

Done. Next steps:

  1. deploy:            npm run deploy
  2. copy the URL wrangler prints, e.g. https://xinliqiu-store.<subdomain>.workers.dev
  3. export in your client environment:

       XINLIQIU_STORE=cloudflare
       XINLIQIU_STORE_URL=<that url>
       XINLIQIU_STORE_TOKEN=$TOKEN

  4. verify:  curl -H "Authorization: Bearer $TOKEN" <that url>/inventory

Keep the token secret. Rotate it any time with: npx wrangler secret put XINLIQIU_TOKEN
MSG
