#!/usr/bin/env bash
# Daily run on the Fedora host, next to Trofey.
#
# Sequence matters: fold first (history), then normalise (derived fields), then
# check (quality), then publish. A blocking DQ failure stops before publication —
# a stale report is better than a wrong one on a public page.
#
# Cron (Europe/Kyiv). The source publishes more than once a day, so 06:20 picks
# up the previous day's final state:
#   20 6 * * * /opt/wantedmt/scripts/daily.sh >> /var/log/wantedmt.log 2>&1

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/wantedmt}"
cd "$APP_DIR"

# Never `set -a; . .env` — a value containing parentheses or spaces is a syntax
# error for sh, the export silently does not happen, and the run continues
# without credentials. Read the keys we need, one at a time.
if [[ -f .env ]]; then
  for key in R2_ACCOUNT_ID R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_PUBLIC_BASE; do
    value="$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)" || true
    [[ -n "${value:-}" ]] && export "${key}=${value}"
  done
fi

RUN="uv run wantedmt --db data/wantedmt.duckdb"
TAC_DB="${TAC_DB:-vendor/tac_full.csv}"

echo "[$(date -Is)] fold latest snapshots"
# Exit code 3 means the portal published nothing new. That is success, and
# rebuilding a report from unchanged data would only move its date.
set +e
$RUN daily --source npu --recent 3
folded=$?
set -e
if [[ $folded -eq 3 ]]; then
  echo "[$(date -Is)] nothing new published; yesterday's report still stands"
  exit 0
elif [[ $folded -ne 0 ]]; then
  echo "[$(date -Is)] fold failed" >&2
  exit 1
fi

# The outside TAC list is not in git: MIT, 11.8 MB, and not ours to
# redistribute. Fetch it when absent; refresh on Mondays for new handsets. A
# failed refresh is not a failed run — the copy on disk is still good.
if [[ ! -s "$TAC_DB" || "$(date +%u)" == "1" ]]; then
  echo "[$(date -Is)] refresh the TAC reference list"
  uv run python scripts/fetch_tac_db.py \
    || echo "[$(date -Is)] refresh failed; using the copy on disk" >&2
fi

echo "[$(date -Is)] normalise"
# --external-tac on every run, not when someone remembers: build() deletes and
# re-derives the register's own TAC rows, so the models borrowed for 385_665
# records are gone at the start of each pass and have to be filled again.
# Without the flag the first nightly run would have taken model coverage from
# 96.9% to 64.5% — quietly, since the quality gate sits at 60%.
$RUN normalize --external-tac "$TAC_DB"

echo "[$(date -Is)] data quality"
if ! $RUN dq --publish --notify-review; then
  echo "[$(date -Is)] BLOCKING DQ FAILURE — export and publication skipped" >&2
  exit 1
fi

echo "[$(date -Is)] export artefacts"
$RUN export --out data/export

# Weekly on Mondays: what the brand resolver could not place, ranked by cost.
if [[ "$(date +%u)" == "1" ]]; then
  echo "[$(date -Is)] weekly unmatched review"
  $RUN unmatched --limit 300 --out docs/unmatched.md
fi

echo "[$(date -Is)] done"
