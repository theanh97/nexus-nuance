#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

LOG_PATH="${OPENCLAW_WATCHDOG_LOG_PATH:-logs/openclaw_watchdog.log}"
STOP_FLAG="${OPENCLAW_WATCHDOG_STOP_FLAG:-data/state/openclaw_watchdog.stop}"
INTERVAL_SEC="${OPENCLAW_WATCHDOG_INTERVAL_SEC:-30}"
RESTART_ON_FAIL="${OPENCLAW_WATCHDOG_RESTART_ON_FAIL:-true}"
FAIL_THRESHOLD="${OPENCLAW_WATCHDOG_FAIL_THRESHOLD:-2}"
RESTART_COOLDOWN_SEC="${OPENCLAW_WATCHDOG_RESTART_COOLDOWN_SEC:-120}"

mkdir -p "$(dirname "$LOG_PATH")" "$(dirname "$STOP_FLAG")"

fail_count=0
last_restart_at=0

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_PATH"
}

log "openclaw watchdog started (interval=${INTERVAL_SEC}s, restart_on_fail=${RESTART_ON_FAIL})"

while :; do
  if [ -f "$STOP_FLAG" ]; then
    rm -f "$STOP_FLAG"
    log "stop flag detected -> exit watchdog"
    exit 0
  fi

  if openclaw gateway probe >/dev/null 2>&1; then
    fail_count=0
    log "probe ok"
  else
    fail_count=$((fail_count + 1))
    log "probe failed (count=${fail_count})"
    if [ "$RESTART_ON_FAIL" = "true" ] || [ "$RESTART_ON_FAIL" = "1" ]; then
      now="$(date +%s)"
      if [ "$fail_count" -ge "$FAIL_THRESHOLD" ] && [ $((now - last_restart_at)) -ge "$RESTART_COOLDOWN_SEC" ]; then
        log "restarting gateway (cooldown ${RESTART_COOLDOWN_SEC}s)"
        openclaw gateway stop >/dev/null 2>&1 || true
        openclaw gateway start >/dev/null 2>&1 || true
        last_restart_at="$now"
        fail_count=0
      fi
    fi
  fi

  sleep "$INTERVAL_SEC"
done

