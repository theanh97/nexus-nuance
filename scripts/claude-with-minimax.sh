#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "Error: 'claude' command not found."
  echo "Install Claude Code CLI first, then run this script again."
  exit 1
fi

export ANTHROPIC_API_KEY="${CLAUDE_CODE_API_KEY:-${MINIMAX_API_KEY:-}}"
export ANTHROPIC_BASE_URL="${CLAUDE_CODE_BASE_URL:-${MINIMAX_ANTHROPIC_BASE_URL:-https://api.minimax.io/anthropic}}"
export ANTHROPIC_MODEL="${CLAUDE_CODE_MODEL:-${MINIMAX_MODEL:-MiniMax-M2.5}}"

if [ -z "${ANTHROPIC_API_KEY}" ]; then
  echo "Error: Missing API key."
  echo "Set CLAUDE_CODE_API_KEY or MINIMAX_API_KEY in .env."
  exit 1
fi

echo "Using Claude-compatible gateway via MiniMax:"
echo "  ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
echo "  ANTHROPIC_MODEL=$ANTHROPIC_MODEL"

exec claude "$@"
