#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
STATE_DIR="$ROOT_DIR/data/state"
LOG_DIR="$ROOT_DIR/logs"
CLAUDE_DIR="$ROOT_DIR/.claude"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$CLAUDE_DIR"

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

SETUP_ONLY=0
if [ "${1:-}" = "--setup-only" ]; then
  SETUP_ONLY=1
  shift
fi

normalize_provider() {
  printf "%s" "$1" | tr '[:upper:]' '[:lower:]' | tr '-' '_'
}

find_litellm_cmd() {
  if command -v litellm >/dev/null 2>&1; then
    command -v litellm
    return
  fi

  local user_base
  user_base="$(python3 -m site --user-base)"
  if [ -x "$user_base/bin/litellm" ]; then
    printf "%s\n" "$user_base/bin/litellm"
    return
  fi

  if [ -x "$HOME/Library/Python/3.9/bin/litellm" ]; then
    printf "%s\n" "$HOME/Library/Python/3.9/bin/litellm"
    return
  fi

  echo ""
}

is_truthy() {
  case "${1:-}" in
    1|true|yes|on|TRUE|YES|ON) return 0 ;;
    *) return 1 ;;
  esac
}

gateway_healthcheck() {
  local base_url="$1"
  curl -fsS --max-time 2 "$base_url/health/readiness" >/dev/null 2>&1 || \
  curl -fsS --max-time 2 "$base_url/health/liveliness" >/dev/null 2>&1 || \
  curl -fsS --max-time 2 "$base_url/health" >/dev/null 2>&1
}

supports_anthropic_messages() {
  local base_url="$1"
  local api_key="$2"
  local trimmed="${base_url%/}"
  local endpoint
  local code

  for endpoint in "$trimmed/messages" "$trimmed/v1/messages"; do
    code="$(
      curl -sS --max-time 8 -o /dev/null -w "%{http_code}" "$endpoint" \
        -H "Authorization: Bearer $api_key" \
        -H "x-api-key: $api_key" \
        -H "anthropic-version: 2023-06-01" \
        -H "content-type: application/json" \
        -d '{"model":"ping","max_tokens":8,"messages":[{"role":"user","content":"ping"}]}' \
      || true
    )"
    case "$code" in
      200|400|401|403|405|422)
        ANTHROPIC_DETECTED_BASE_URL="${endpoint%/messages}"
        return 0
        ;;
    esac
  done

  return 1
}

ensure_litellm() {
  local needs_install=0

  LITELLM_CMD="$(find_litellm_cmd)"
  if [ -z "$LITELLM_CMD" ] && python3 -c "import litellm" >/dev/null 2>&1; then
    LITELLM_CMD="$(find_litellm_cmd)"
  fi

  if [ -z "$LITELLM_CMD" ]; then
    needs_install=1
  fi

  if [ "$needs_install" -eq 1 ]; then
    echo "LiteLLM not found. Installing litellm[proxy]..."
    python3 -m pip install --user 'litellm[proxy]'

    LITELLM_CMD="$(find_litellm_cmd)"
    if [ -z "$LITELLM_CMD" ]; then
      echo "Error: Failed to install LiteLLM."
      exit 1
    fi
  fi

  if ! python3 -c "import multipart" >/dev/null 2>&1; then
    echo "Installing python-multipart dependency..."
    python3 -m pip install --user python-multipart
  fi
}

start_litellm_gateway() {
  local config_path="$1"
  local host="${CLAUDE_CODE_GATEWAY_HOST:-127.0.0.1}"
  local port="${CLAUDE_CODE_GATEWAY_PORT:-4000}"
  local pid_file="$STATE_DIR/litellm-openai-compatible.pid"
  local log_file="$LOG_DIR/litellm-openai-compatible.log"
  local gateway_url="http://$host:$port"
  local wait_steps="${CLAUDE_CODE_GATEWAY_WAIT_STEPS:-30}"
  local i

  if gateway_healthcheck "$gateway_url"; then
    return
  fi

  if [ -f "$pid_file" ]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" >/dev/null 2>&1; then
      kill "$old_pid" >/dev/null 2>&1 || true
      sleep 1
    fi
    rm -f "$pid_file"
  fi

  if [ -z "${CODEX_COMPAT_API_KEY:-}" ]; then
    echo "Error: CODEX_COMPAT_API_KEY is empty."
    exit 1
  fi

  nohup env CODEX_COMPAT_API_KEY="$CODEX_COMPAT_API_KEY" \
    "$LITELLM_CMD" \
    --config "$config_path" \
    --host "$host" \
    --port "$port" \
    >"$log_file" 2>&1 &
  echo "$!" > "$pid_file"

  for ((i = 0; i < wait_steps; i += 1)); do
    if gateway_healthcheck "$gateway_url"; then
      return
    fi
    sleep 1
  done

  echo "Error: LiteLLM gateway failed to start."
  echo "Check log: $log_file"
  tail -n 80 "$log_file" || true
  exit 1
}

run_direct_anthropic() {
  export ANTHROPIC_API_KEY="${CLAUDE_CODE_API_KEY:-${MINIMAX_API_KEY:-${ANTHROPIC_API_KEY:-}}}"
  export ANTHROPIC_BASE_URL="${CLAUDE_CODE_BASE_URL:-${MINIMAX_ANTHROPIC_BASE_URL:-https://api.minimax.io/anthropic}}"
  export ANTHROPIC_MODEL="${CLAUDE_CODE_MODEL:-${MINIMAX_MODEL:-MiniMax-M2.5}}"
  export ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_API_KEY"

  if [ -z "${ANTHROPIC_API_KEY}" ]; then
    echo "Error: Missing API key."
    echo "Set CLAUDE_CODE_API_KEY (or MINIMAX_API_KEY) in .env."
    exit 1
  fi

  echo "Using direct Anthropic-compatible profile:"
  echo "  ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
  echo "  ANTHROPIC_MODEL=$ANTHROPIC_MODEL"

  if [ "$SETUP_ONLY" -eq 1 ]; then
    echo "Setup only mode enabled. Gateway/profile prepared."
    return
  fi

  exec claude "$@"
}

run_openai_compatible() {
  local host="${CLAUDE_CODE_GATEWAY_HOST:-127.0.0.1}"
  local port="${CLAUDE_CODE_GATEWAY_PORT:-4000}"
  local gateway_token="${CLAUDE_CODE_GATEWAY_TOKEN:-sk-litellm-local}"
  local config_path="$CLAUDE_DIR/litellm-openai-compatible.yaml"
  local compat_api_key
  local compat_base_url
  local sonnet_model
  local opus_model
  local haiku_model
  local model_list_yaml=""
  
  echo "PROVIDER is: |${PROVIDER:-}|"
  echo "CODAXER_MODELS is: |${CODAXER_MODELS:-}|"

  if [ "${PROVIDER:-}" = "codaxer" ]; then
    echo "Entering codaxer block"
    compat_api_key="${CODAXER_API_KEY:-${CLAUDE_CODE_API_KEY:-}}"
    compat_base_url="${CODAXER_BASE_URL:-${CLAUDE_CODE_BASE_URL:-}}"
    if [ -n "${CODAXER_MODELS:-}" ]; then
      echo "CODAXER_MODELS is not empty"
      # Remove quotes from the model list for correct parsing
      local models_list="${CODAXER_MODELS//\"/}"
      for model_name in $models_list; do
        model_list_yaml+=$(cat <<EOF
  - model_name: "$model_name"
    litellm_params:
      model: "openai/$model_name"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
EOF
)
      done
    else
      sonnet_model="${CODAXER_MODEL_SONNET:-${CODAXER_MODEL:-${CLAUDE_CODE_MODEL_SONNET:-${CLAUDE_CODE_MODEL:-gpt-5.1-codex}}}}"
      opus_model="${CODAXER_MODEL_OPUS:-${CLAUDE_CODE_MODEL_OPUS:-gpt-5.1-codex-max}}"
      haiku_model="${CODAXER_MODEL_HAIKU:-${CLAUDE_CODE_MODEL_HAIKU:-gpt-5.1-codex-mini}}"
      model_list_yaml=$(cat <<EOF
  - model_name: "sonnet"
    litellm_params:
      model: "openai/${sonnet_model}"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"

  - model_name: "opus"
    litellm_params:
      model: "openai/${opus_model}"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"

  - model_name: "haiku"
    litellm_params:
      model: "openai/${haiku_model}"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
EOF
)
    fi
  else
    compat_api_key="${CLAUDE_CODE_API_KEY:-${CODAXER_API_KEY:-}}"
    compat_base_url="${CLAUDE_CODE_BASE_URL:-${CODAXER_BASE_URL:-}}"
    sonnet_model="${CLAUDE_CODE_MODEL_SONNET:-${CLAUDE_CODE_MODEL:-${CODAXER_MODEL_SONNET:-${CODAXER_MODEL:-gpt-5.1-codex}}}}"
    opus_model="${CLAUDE_CODE_MODEL_OPUS:-${CODAXER_MODEL_OPUS:-gpt-5.1-codex-max}}"
    haiku_model="${CLAUDE_CODE_MODEL_HAIKU:-${CLAUDE_CODE_MODEL_HAIKU:-gpt-5.1-codex-mini}}"
    model_list_yaml=$(cat <<EOF
  - model_name: "sonnet"
    litellm_params:
      model: "openai/${sonnet_model}"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"

  - model_name: "opus"
    litellm_params:
      model: "openai/${opus_model}"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"

  - model_name: "haiku"
    litellm_params:
      model: "openai/${haiku_model}"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
EOF
)
  fi

  if [ -z "$compat_api_key" ] || [ -z "$compat_base_url" ]; then
    echo "Error: Missing OpenAI-compatible profile."
    echo "Set CLAUDE_CODE_API_KEY + CLAUDE_CODE_BASE_URL, or CODAXER_API_KEY + CODAXER_BASE_URL."
    exit 1
  fi

  export CODEX_COMPAT_API_KEY="$compat_api_key"

  ensure_litellm

  cat > "$config_path" <<EOF
general_settings:
  master_key: "$gateway_token"

model_list:
$model_list_yaml
EOF

  start_litellm_gateway "$config_path"

  export ANTHROPIC_BASE_URL="http://$host:$port"
  export ANTHROPIC_API_KEY="$gateway_token"
  export ANTHROPIC_AUTH_TOKEN="$gateway_token"
  export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-sonnet}"
  export ANTHROPIC_DEFAULT_SONNET_MODEL="${ANTHROPIC_DEFAULT_SONNET_MODEL:-sonnet}"
  export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-opus}"
  export ANTHROPIC_DEFAULT_HAIKU_MODEL="${ANTHROPIC_DEFAULT_HAIKU_MODEL:-haiku}"
  export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="${CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS:-1}"

  echo "Using OpenAI-compatible backend via local LiteLLM gateway:"
  echo "  Gateway: $ANTHROPIC_BASE_URL"
  if [ -n "${CODAXER_MODELS:-}" ] && [ "${PROVIDER:-}" = "codaxer" ]; then
    echo "  Models: ${CODAXER_MODELS}"
  else
    echo "  Sonnet:  ${sonnet_model}"
    echo "  Opus:    ${opus_model}"
    echo "  Haiku:   ${haiku_model}"
  fi

  if [ "$SETUP_ONLY" -eq 1 ]; then
    echo "Setup only mode enabled. Gateway/profile prepared."
    return
  fi

  exec claude "$@"
}


PROVIDER="$(normalize_provider "${CLAUDE_CODE_PROVIDER:-minimax}")"
if [ "$PROVIDER" = "auto" ]; then
  AUTO_API_KEY="${CLAUDE_CODE_API_KEY:-${CODAXER_API_KEY:-}}"
  AUTO_BASE_URL="${CLAUDE_CODE_BASE_URL:-${CODAXER_BASE_URL:-}}"
  if [ -z "$AUTO_API_KEY" ] || [ -z "$AUTO_BASE_URL" ]; then
    echo "Error: auto provider needs CLAUDE_CODE_* or CODAXER_* base URL + API key."
    exit 1
  fi

  if supports_anthropic_messages "$AUTO_BASE_URL" "$AUTO_API_KEY"; then
    export CLAUDE_CODE_BASE_URL="$ANTHROPIC_DETECTED_BASE_URL"
    PROVIDER="anthropic_compatible"
  else
    PROVIDER="openai_compatible"
  fi
fi

if is_truthy "${CLAUDE_CODE_FORCE_GATEWAY:-false}"; then
  PROVIDER="openai_compatible"
fi

case "$PROVIDER" in
  minimax|anthropic|anthropic_compatible|direct)
    run_direct_anthropic "$@"
    ;;
  openai|openai_compatible|openai-compatible|codaxer)
    run_openai_compatible "$@"
    ;;
  *)
    echo "Error: Unsupported CLAUDE_CODE_PROVIDER=$PROVIDER"
    echo "Use one of: minimax, anthropic_compatible, openai_compatible, codaxer, auto"
    exit 1
    ;;
esac
