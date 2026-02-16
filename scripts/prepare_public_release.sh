#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${1:-$ROOT_DIR/public-release-$TIMESTAMP}"

mkdir -p "$OUT_DIR"

echo "Preparing public release package..."
echo "Source: $ROOT_DIR"
echo "Target: $OUT_DIR"

rsync -a "$ROOT_DIR/" "$OUT_DIR/" \
  --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.claude/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude 'logs/' \
  --exclude 'data/' \
  --exclude 'memory/' \
  --exclude 'output/' \
  --exclude 'projects/' \
  --exclude 'app-output/' \
  --exclude 'screenshots/' \
  --exclude '*.out' \
  --exclude '*.bak' \
  --exclude 'public-release-*' \
  --exclude 'research/sica/.git/' \
  --exclude 'research/sica/results/'

mkdir -p "$OUT_DIR/data" "$OUT_DIR/logs" "$OUT_DIR/memory"
touch "$OUT_DIR/data/.gitkeep" "$OUT_DIR/logs/.gitkeep" "$OUT_DIR/memory/.gitkeep"

if [[ -f "$OUT_DIR/config/mcp_config.json" ]]; then
  perl -i -pe 's#/Users/[^"]+#\${WORKSPACE_ROOT}#g' "$OUT_DIR/config/mcp_config.json"
fi

if [[ -f "$OUT_DIR/.env.example" && ! -f "$OUT_DIR/.env" ]]; then
  cp "$OUT_DIR/.env.example" "$OUT_DIR/.env"
fi

echo
echo "Running privacy audit on release package..."
bash "$OUT_DIR/scripts/privacy_audit.sh" || true

echo
echo "Public release package created at:"
echo "  $OUT_DIR"
echo
echo "Before publish:"
echo "  1) Review .env (should be placeholders only)"
echo "  2) Review config/ for local path placeholders"
echo "  3) Run tests in release folder"
