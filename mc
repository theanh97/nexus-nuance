#!/usr/bin/env bash
# Wrapper dedicated to Codex GPT-5
export CLAUDE_CODE_PROVIDER_OVERRIDE=codaxer
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT_DIR/scripts/claude-with-minimax.sh" "$@"
