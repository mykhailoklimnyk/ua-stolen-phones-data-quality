#!/usr/bin/env bash
# Nightly on the Fedora host, next to Trofey (#4). Takes the store that GitHub Actions
# already folded and published, and hands it to the two local consumers: Trofey's Postgres
# (the IMEI check) and the site build (the quality report).
#
# It does NOT fold anything. The fold runs in Actions on a measured schedule, and doing it
# here as well would mean two histories of the same source drifting apart — plus this host
# would need the R2 credentials and the 11 MB TAC catalogue for no gain. `daily.sh` remains
# the script for a host that owns the pipeline; this one is for a host that consumes it.
#
# Cron (Europe/Kyiv), via trofey-wantedmt.timer:
#   04:00 — after the last Actions fold of the day (22:00 UTC) and before the site build.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/wantedmt}"
cd "$APP_DIR"

REPO="${WANTEDMT_REPO:-mykhailoklimnyk/ua-stolen-phones-data-quality}"
STATE_TAG="${STATE_TAG:-state}"
STATE_ASSET="${STATE_ASSET:-state.duckdb}"
DB="data/wantedmt.duckdb"
STAMP="data/.delivered-asset"
CONTAINER="${TROFEY_CONTAINER:-trofey-ingest-1}"
RUN="uv run wantedmt --db $DB"

say() { echo "[$(date -Is)] $*"; }

# 1. Has the published store moved since the last delivery? The asset is 270 MB, and on a
# day the portal publishes nothing it is byte-identical to yesterday's. The API answer is
# a few hundred bytes, so asking is free and downloading is not.
remote="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/tags/$STATE_TAG" \
  | python3 -c 'import json,sys
rel = json.load(sys.stdin)
asset = next((a for a in rel["assets"] if a["name"] == sys.argv[1]), None)
print(asset["updated_at"] if asset else "")' "$STATE_ASSET")"

if [[ -z "$remote" ]]; then
  say "the $STATE_TAG release has no $STATE_ASSET asset — nothing to deliver" >&2
  exit 1
fi

if [[ -f "$STAMP" && "$(cat "$STAMP")" == "$remote" && -s "$DB" ]]; then
  say "store unchanged since $remote; yesterday's delivery still stands"
  exit 0
fi

# 2. Downloaded beside the live file and moved into place only when whole: a store
# truncated by a dropped connection is still a file, and duckdb would open it and answer
# fewer rows rather than fail.
say "fetching the published store ($remote)"
curl -fsSL -o "$DB.new" \
  "https://github.com/$REPO/releases/download/$STATE_TAG/$STATE_ASSET"
mv "$DB.new" "$DB"

# 3. A second opinion on the same data, before any of it reaches production. Actions runs
# this gate too, before publishing — this is the copy that decides whether the numbers in
# front of a buyer get replaced. A blocking failure leaves yesterday's registry answering,
# which the Trofey watchdog reports as staleness (Trofey#675) rather than as silence.
#
# The report it renders is also what the morning site build serves at
# /data-quality/wantedmt/latest.html (Trofey#680), so it is written even on a red run —
# an honest report about a bad day beats a fresh-looking one about nothing.
say "data quality"
if ! $RUN dq --out dq/reports; then
  say "BLOCKING DQ FAILURE — nothing delivered, production keeps yesterday's registry" >&2
  exit 1
fi

say "lookup export"
$RUN lookup-export --out data/lookup

# 4. Into the database, through the container that owns the schema. The literal name is
# deliberate: a hash-prefixed one is a known way for this host to break a service silently
# (Trofey docs/deploy.md), so a missing container fails here loudly instead of skipping a
# day of freshness without saying so.
say "delivering to $CONTAINER"
docker exec "$CONTAINER" rm -rf /tmp/imei-lookup
if docker cp data/lookup "$CONTAINER:/tmp/imei-lookup" \
   && docker exec "$CONTAINER" python scripts/imei_load.py /tmp/imei-lookup; then
  echo "$remote" > "$STAMP"
  say "delivered"
else
  say "DELIVERY FAILED — production keeps yesterday's registry" >&2
  exit 1
fi
