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
# Runs hourly via trofey-wantedmt.timer (#7).
#
# Every exit that is not success says so in the ops group (#9). A systemd oneshot that
# fails is a red line in `systemctl status` and nowhere else, and the failure this guards
# against is precisely the silent one: the registry stops moving, the page keeps answering
# «не в розшуку» with yesterday's confidence, and nobody has a reason to look.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/wantedmt}"
cd "$APP_DIR"

REPO="${WANTEDMT_REPO:-mykhailoklimnyk/ua-stolen-phones-data-quality}"
STATE_TAG="${STATE_TAG:-state}"
STATE_ASSET="${STATE_ASSET:-state.duckdb}"
DB="data/wantedmt.duckdb"
STAMP="data/.delivered-asset"
CONTAINER="${TROFEY_CONTAINER:-trofey-ingest-1}"
# Resolved, not assumed. systemd hands a unit a minimal PATH with no ~/.local/bin, while
# an interactive ssh gets one from the profile — so «works when I run it» and «works at
# 13:00» are different claims, and #11 was the difference.
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
RUN="$UV run wantedmt --db $DB"

say() { echo "[$(date -Is)] $*"; }

#: A command that will not start is not an opinion about the data (#11). Kept separate so
#: the ops group is never sent to read a quality report about a missing binary.
NOT_RUNNABLE=127

# The ops group, reached the way the site build reaches it: pointed greps into Trofey's
# .env, never `set -a; . .env` — a value with parentheses is a syntax error for sh, the
# export silently does not happen, and the run carries on without credentials.
ENVF="${TROFEY_ENV:-/opt/trofey/.env}"
envval() { grep "^$1=" "$ENVF" 2>/dev/null | head -1 | cut -d= -f2-; }

notify() {
  local tok chat
  tok="$(envval BOT_TOKEN)"; chat="$(envval OPS_CHAT_ID)"
  [[ -z "$tok" || -z "$chat" ]] && return 0
  curl -s -o /dev/null --max-time 20     --data-urlencode "chat_id=$chat" --data-urlencode "text=$1"     "https://api.telegram.org/bot${tok}/sendMessage" || true
}

# Anything that falls over — a dead network, a broken duckdb, a container that is not
# there — lands here, including the failures no branch below anticipates.
on_error() {
  local line=$1
  notify "🔴 Trofey: доставка реєстру розшуку впала (deliver.sh, рядок $line). Прод лишається на попередньому зрізі; перевірка IMEI відповідає старими даними."
}
trap 'on_error $LINENO' ERR

if [[ ! -x "$UV" ]] && ! command -v "$UV" >/dev/null 2>&1; then
  say "uv not found at $UV" >&2
  notify "🔴 Trofey: доставка реєстру не запустилась — не знайдено uv ($UV). Це середовище, не дані: прод лишився на попередньому зрізі."
  exit 1
fi

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
  notify "🔴 Trofey: у релізі $STATE_TAG немає асета $STATE_ASSET — доставляти нічого. Реєстр розшуку не оновлюється."
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
set +e
$RUN dq --out dq/reports
dq_status=$?
set -e

if [[ $dq_status -eq $NOT_RUNNABLE ]]; then
  say "cannot run $UV — nothing delivered" >&2
  notify "🔴 Trofey: доставка реєстру не запустилась — не знайдено uv ($UV). Це середовище, не дані: прод лишився на попередньому зрізі."
  exit 1
fi

if [[ $dq_status -ne 0 ]]; then
  say "BLOCKING DQ FAILURE — nothing delivered, production keeps yesterday's registry" >&2
  notify "🟠 Trofey: блокуючий DQ-фейл на реєстрі розшуку — у прод НЕ доставлено, лишився попередній зріз. Звіт: https://trofey.app/data-quality/wantedmt/latest.html"
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
  # One line per REAL delivery — roughly one a day, since the asset moves about as often as
  # the portal publishes. It is the heartbeat that makes silence meaningful: an ops group
  # that only ever speaks on failure cannot tell «all good» from «the timer died».
  # no f-string here: quoting it through the shell is how the success path stayed broken
  # until the first real delivery would have run it (#11)
  read -r stamp rows < <(python3 -c 'import json
m = json.load(open("data/lookup/meta.json"))
print(m["as_of"], format(m["imei_rows"], ",").replace(",", " "))')
  notify "🟢 Trofey: реєстр розшуку оновлено — зріз $stamp, $rows номерів."
else
  say "DELIVERY FAILED — production keeps yesterday's registry" >&2
  notify "🔴 Trofey: не вдалось залити реєстр розшуку в прод (docker cp / imei_load). Лишився попередній зріз."
  exit 1
fi
