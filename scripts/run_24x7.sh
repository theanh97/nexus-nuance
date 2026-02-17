#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-5050}"
LOCK_PATH="${AUTODEV_RUNTIME_LOCK_PATH:-data/state/autodev_runtime.lock}"
LOG_PATH="${AUTODEV_24X7_LOG_PATH:-logs/run_system_24x7.log}"
STOP_FLAG="${AUTODEV_24X7_STOP_FLAG:-data/state/autodev_runtime.stop}"
HEALTH_PATH="${AUTODEV_24X7_HEALTH_PATH:-/api/status}"
HEALTH_TIMEOUT_SEC="${AUTODEV_24X7_HEALTH_TIMEOUT_SEC:-6}"
RESTART_DELAY_SEC="${AUTODEV_24X7_RESTART_DELAY_SEC:-3}"
EXTRA_ARGS="${AUTODEV_24X7_EXTRA_ARGS:-}"

if [ "${AUTODEV_FORCE_STALE_LOCK_CLEANUP:-false}" = "true" ] || \
   [ "${AUTODEV_FORCE_STALE_LOCK_CLEANUP:-false}" = "1" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --force-stale-lock"
fi

mkdir -p "$(dirname "$LOG_PATH")" "$(dirname "$LOCK_PATH")"

cleanup_stale_lock() {
  if [ ! -f "$LOCK_PATH" ]; then
    return 0
  fi
  lock_pid="$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$LOCK_PATH" | head -n 1 || true)"
  if [ -z "$lock_pid" ]; then
    rm -f "$LOCK_PATH"
    return 0
  fi
  if kill -0 "$lock_pid" 2>/dev/null; then
    return 0
  fi
  rm -f "$LOCK_PATH"
}

health_probe() {
  curl -fsS --max-time "$HEALTH_TIMEOUT_SEC" "http://$DASHBOARD_HOST:$DASHBOARD_PORT$HEALTH_PATH" >/dev/null 2>&1
}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] autodev 24x7 supervisor started" >> "$LOG_PATH"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] host=$DASHBOARD_HOST port=$DASHBOARD_PORT" >> "$LOG_PATH"

while :; do
  if [ -f "$STOP_FLAG" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] stop flag detected -> exit supervisor" >> "$LOG_PATH"
    rm -f "$STOP_FLAG"
    exit 0
  fi

  cleanup_stale_lock
  DASHBOARD_HOST="$DASHBOARD_HOST" DASHBOARD_PORT="$DASHBOARD_PORT" \
    "$PYTHON_BIN" run_system.py --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" $EXTRA_ARGS >> "$LOG_PATH" 2>&1 || true

  if health_probe; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] process exited but health probe is still OK (another runner alive)" >> "$LOG_PATH"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] runtime exited, restarting in ${RESTART_DELAY_SEC}s" >> "$LOG_PATH"
  fi
  sleep "$RESTART_DELAY_SEC"
done
