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
# The ops group hears about staleness, not about ticks (#9, #13). A systemd oneshot that
# fails is a red line in `systemctl status` and nowhere else, and the failure this guards
# against is precisely the silent one: the registry stops moving, the page keeps answering
# «не в розшуку» with yesterday's confidence, and nobody has a reason to look.
#
# But one failed tick is not that failure. The timer is hourly and the next tick re-reads
# the same asset, so a transient blip costs nothing — and alerting on it teaches the group
# to scroll past red. On 17.08 a GitHub 504 sent «прод відповідає старими даними» three
# hours AFTER that very slice had been delivered: an alert that is wrong about the
# consequence is worse than silence, because the day the registry really does stop, the
# message will look the same. So a network-class failure is counted, not announced, and
# what it announces when the streak reaches $ALERT_AFTER is the slice production actually
# serves — read from meta.json, never assumed.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/wantedmt}"
cd "$APP_DIR"

REPO="${WANTEDMT_REPO:-mykhailoklimnyk/ua-stolen-phones-data-quality}"
STATE_TAG="${STATE_TAG:-state}"
STATE_ASSET="${STATE_ASSET:-state.duckdb}"
DB="data/wantedmt.duckdb"
STAMP="data/.delivered-asset"
#: Consecutive network-class failures. Lives beside the stamp so `systemctl status` and the
#: ops group cannot disagree about how long this has been going on.
FAILS="data/.deliver-failures"
#: Three hours of not getting through. Below that the hourly retry is the fix, and the
#: registry itself only moves about once a day (see the timer unit).
ALERT_AFTER="${ALERT_AFTER:-3}"
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

#: The slice production is answering with — a fact, read from what the last delivery wrote,
#: rather than the guess the alert used to make. «невідомо» only before the first delivery.
delivered_as_of() {
  python3 -c 'import json
try:
    print(json.load(open("data/lookup/meta.json"))["as_of"])
except Exception:
    print("невідомо")' 2>/dev/null || echo "невідомо"
}

#: Counted, and announced only once the streak means something. Success clears the count,
#: so the number in the message is «hours in a row», not «times since install».
fail_streak() {
  local n=0
  [[ -f "$FAILS" ]] && n="$(tr -cd '0-9' < "$FAILS")"
  echo $(( ${n:-0} + 1 )) > "$FAILS"
  cat "$FAILS"
}

clear_streak() { rm -f "$FAILS"; }

# Anything that falls over — a dead network, a broken duckdb, a container that is not
# there — lands here, including the failures no branch below anticipates.
on_error() {
  local line=$1 n
  n="$(fail_streak)"
  say "tick failed at line $line (consecutive: $n)" >&2
  if (( n >= ALERT_AFTER )); then
    notify "🔴 Trofey: доставка реєстру розшуку не проходить ${n} год поспіль (deliver.sh, рядок $line). Прод відповідає зрізом $(delivered_as_of) — новіші заяви в перевірку IMEI не потрапляють."
  fi
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
#
# Retried, because the answer to «has it moved» is not worth a failed run: on 17.08 the API
# returned a single 504 and the tick died with a JSON traceback about an empty stdin (#13).
# --retry-all-errors covers the connection-reset case too, which -f alone does not see.
remote="$(curl -fsSL --max-time 30 --retry 3 --retry-delay 5 --retry-all-errors \
  "https://api.github.com/repos/$REPO/releases/tags/$STATE_TAG" \
  | python3 -c 'import json,sys
rel = json.load(sys.stdin)
asset = next((a for a in rel["assets"] if a["name"] == sys.argv[1]), None)
print(asset["updated_at"] if asset else "")' "$STATE_ASSET")"

# A missing asset is usually not a missing asset: Actions publishes with
# `gh release upload --clobber`, which deletes the old one before pushing 270 MB back, and a
# tick that lands in that window sees a release with no asset at all. Same rule as the trap
# — count it, and only speak up once it has outlived any plausible upload.
if [[ -z "$remote" ]]; then
  n="$(fail_streak)"
  say "the $STATE_TAG release has no $STATE_ASSET asset — nothing to deliver (consecutive: $n)" >&2
  if (( n >= ALERT_AFTER )); then
    notify "🔴 Trofey: у релізі $STATE_TAG ${n} год поспіль немає асета $STATE_ASSET — доставляти нічого. Прод відповідає зрізом $(delivered_as_of)."
  fi
  exit 1
fi

if [[ -f "$STAMP" && "$(cat "$STAMP")" == "$remote" && -s "$DB" ]]; then
  say "store unchanged since $remote; yesterday's delivery still stands"
  clear_streak
  exit 0
fi

# 2. Downloaded beside the live file and moved into place only when whole: a store
# truncated by a dropped connection is still a file, and duckdb would open it and answer
# fewer rows rather than fail.
# No --max-time here: 270 MB on a bad night is slow, not broken. What is broken is a
# transfer that stopped moving, and that is what --speed-time catches.
say "fetching the published store ($remote)"
curl -fsSL -o "$DB.new" --retry 3 --retry-delay 10 --retry-all-errors \
  --speed-limit 10240 --speed-time 60 \
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
  clear_streak
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
