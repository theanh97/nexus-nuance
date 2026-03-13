#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
STATE_DIR="$ROOT_DIR/data/state"
LOG_DIR="$ROOT_DIR/logs"
CLAUDE_DIR="$ROOT_DIR/.claude"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$CLAUDE_DIR"

# Source environment variables
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

supports_modern_typing() {
  local py="$1"
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1
}

pick_litellm_python() {
  local override="${CLAUDE_CODE_LITELLM_PYTHON:-}"
  if [ -n "$override" ]; then
    printf "%s\n" "$override"
    return
  fi

  local candidates=(
    "/opt/homebrew/bin/python3.11"
    "python3.11"
    "/opt/homebrew/bin/python3"
    "python3"
  )

  local c
  for c in "${candidates[@]}"; do
    if command -v "$c" >/dev/null 2>&1 && supports_modern_typing "$c"; then
      command -v "$c"
      return
    fi
  done

  command -v python3
}

is_truthy() {
  case "${1:-}" in
    1|true|yes|on|TRUE|YES|ON) return 0 ;;
    *) return 1 ;;
  esac
}

gateway_healthcheck() {
  local base_url="$1"
  local code
  for endpoint in "$base_url/health/readiness" "$base_url/health/liveliness" "$base_url/health"; do
    code="$(curl -s --max-time 2 -o /dev/null -w "%{http_code}" "$endpoint" 2>/dev/null || echo "000")"
    case "$code" in
      200|401|403) return 0 ;;
    esac
  done
  return 1
}

file_sha256() {
  local file="$1"

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" 2>/dev/null | awk '{print $1}'
    return 0
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" 2>/dev/null | awk '{print $1}'
    return 0
  fi

  python3 - "$file" 2>/dev/null <<'PY' || true
import hashlib
import sys

path = sys.argv[1]
h = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
print(h.hexdigest())
PY
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
  LITELLM_PY="$(pick_litellm_python)"

  local user_base
  user_base="$("$LITELLM_PY" -m site --user-base 2>/dev/null || true)"
  if [ -z "$user_base" ]; then
    echo "Error: Unable to determine Python user base for LiteLLM."
    exit 1
  fi

  LITELLM_CMD="$user_base/bin/litellm"

  if [ ! -x "$LITELLM_CMD" ]; then
    echo "LiteLLM not found for $LITELLM_PY. Installing litellm[proxy]..."
    "$LITELLM_PY" -m pip install --user 'litellm[proxy]'
  fi

  if [ ! -x "$LITELLM_CMD" ]; then
    # Fallback: respect a global `litellm` if present
    if command -v litellm >/dev/null 2>&1; then
      LITELLM_CMD="$(command -v litellm)"
    else
      echo "Error: Failed to install LiteLLM."
      exit 1
    fi
  fi

  if ! "$LITELLM_PY" -c "import multipart" >/dev/null 2>&1; then
    echo "Installing python-multipart dependency..."
    "$LITELLM_PY" -m pip install --user python-multipart
  fi
}

start_litellm_gateway() {
  local config_path="$1"
  local host="${CLAUDE_CODE_GATEWAY_HOST:-127.0.0.1}"
  local port="${CLAUDE_CODE_GATEWAY_PORT:-4000}"
  local pid_file="$STATE_DIR/litellm-openai-compatible.pid"
  local hash_file="$STATE_DIR/litellm-openai-compatible.config.sha256"
  local log_file="$LOG_DIR/litellm-openai-compatible.log"
  local gateway_url="http://$host:$port"
  local wait_steps="${CLAUDE_CODE_GATEWAY_WAIT_STEPS:-30}"
  local i

  local desired_hash=""
  local current_hash=""
  if [ -f "$config_path" ]; then
    desired_hash="$(file_sha256 "$config_path" 2>/dev/null || true)"
  fi
  if [ -f "$hash_file" ]; then
    current_hash="$(cat "$hash_file" 2>/dev/null || true)"
  fi

  if gateway_healthcheck "$gateway_url"; then
    if [ -n "$desired_hash" ] && [ "$desired_hash" = "$current_hash" ]; then
      return
    fi
    if [ -z "$desired_hash" ]; then
      return
    fi
    echo "LiteLLM gateway is running but config changed. Restarting..."
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
      if [ -n "$desired_hash" ]; then
        echo "$desired_hash" > "$hash_file"
      fi
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
  # Prioritize MINIMAX_* variables when provider is minimax
  if [ "$PROVIDER" = "minimax" ]; then
    export ANTHROPIC_API_KEY="${MINIMAX_API_KEY:-${CLAUDE_CODE_API_KEY:-${ANTHROPIC_API_KEY:-}}}"
    export ANTHROPIC_BASE_URL="${MINIMAX_ANTHROPIC_BASE_URL:-${CLAUDE_CODE_BASE_URL:-https://api.minimax.io/anthropic}}"
    export ANTHROPIC_MODEL="${MINIMAX_MODEL:-${CLAUDE_CODE_MODEL:-MiniMax-M2.5}}"
  else
    export ANTHROPIC_API_KEY="${CLAUDE_CODE_API_KEY:-${MINIMAX_API_KEY:-${ANTHROPIC_API_KEY:-}}}"
    export ANTHROPIC_BASE_URL="${CLAUDE_CODE_BASE_URL:-${MINIMAX_ANTHROPIC_BASE_URL:-https://api.minimax.io/anthropic}}"
    export ANTHROPIC_MODEL="${CLAUDE_CODE_MODEL:-${MINIMAX_MODEL:-MiniMax-M2.5}}"
  fi
  
  # Avoid auth conflict by using only one token type
  export ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_API_KEY"
  unset ANTHROPIC_API_KEY

  if [ -z "${ANTHROPIC_AUTH_TOKEN}" ]; then
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
  local model_list_yaml=""
  local extra_models=""
  local s_alias="${CLAUDE_CODE_MODEL_ALIAS_SONNET:-}"
  local o_alias="${CLAUDE_CODE_MODEL_ALIAS_OPUS:-}"
  local h_alias="${CLAUDE_CODE_MODEL_ALIAS_HAIKU:-}"

  # Define the core mappings for Claude UI
  local s_model
  local o_model
  local h_model

  if [ "${PROVIDER:-}" = "deepseek" ]; then
    s_model="${DEEPSEEK_MODEL_SONNET:-deepseek-chat}"
    o_model="${DEEPSEEK_MODEL_OPUS:-deepseek-reasoner}"
    h_model="${DEEPSEEK_MODEL_HAIKU:-deepseek-chat}"
    compat_api_key="${DEEPSEEK_API_KEY:-}"
    compat_base_url="${DEEPSEEK_BASE_URL:-https://api.deepseek.com/v1}"
    extra_models="${DEEPSEEK_MODELS:-}"
  elif [ "${PROVIDER:-}" = "codaxer" ]; then
    s_model="${CODAXER_MODEL_SONNET:-${CLAUDE_CODE_MODEL_SONNET:-gpt-5.1-codex}}"
    o_model="${CODAXER_MODEL_OPUS:-${CLAUDE_CODE_MODEL_OPUS:-gpt-5.3-codex}}"
    h_model="${CODAXER_MODEL_HAIKU:-${CLAUDE_CODE_MODEL_HAIKU:-gpt-5.1-codex-mini}}"
    compat_api_key="${CODAXER_API_KEY:-${CLAUDE_CODE_API_KEY:-}}"
    compat_base_url="${CODAXER_BASE_URL:-${CLAUDE_CODE_BASE_URL:-}}"
    extra_models="${CODAXER_MODELS:-}"
  else
    s_model="${CLAUDE_CODE_MODEL_SONNET:-${CODAXER_MODEL_SONNET:-gpt-5.1-codex}}"
    o_model="${CLAUDE_CODE_MODEL_OPUS:-${CODAXER_MODEL_OPUS:-gpt-5.3-codex}}"
    h_model="${CLAUDE_CODE_MODEL_HAIKU:-${CODAXER_MODEL_HAIKU:-gpt-5.1-codex-mini}}"
    compat_api_key="${CLAUDE_CODE_API_KEY:-${CODAXER_API_KEY:-}}"
    compat_base_url="${CLAUDE_CODE_BASE_URL:-${CODAXER_BASE_URL:-}}"
    extra_models="${CLAUDE_CODE_MODELS:-${CODAXER_MODELS:-}}"
  fi

  if [ -z "$compat_api_key" ] || [ -z "$compat_base_url" ]; then
    echo "Error: Missing OpenAI-compatible profile."
    echo "Set CLAUDE_CODE_API_KEY + CLAUDE_CODE_BASE_URL, or CODAXER_API_KEY + CODAXER_BASE_URL."
    exit 1
  fi

  # For DeepSeek: use native provider prefix (no api_base needed, enables proper Responses API translation)
  # For others: use openai/ prefix with custom api_base
  local litellm_provider_prefix="openai"
  local litellm_api_base_line="      api_base: \"${compat_base_url}\""
  if [ "${PROVIDER:-}" = "deepseek" ]; then
    litellm_provider_prefix="deepseek"
    litellm_api_base_line=""
  fi

  # For DeepSeek: cap max_output_tokens at 8192 (DeepSeek V3/R1 limit)
  local model_info_block=""
  if [ "${PROVIDER:-}" = "deepseek" ]; then
    model_info_block="    model_info:
      max_output_tokens: 8192"
  fi

  # Pre-compute per-model yaml lines (avoids $'\n' inside heredoc expansion bug)
  local s_model_params o_model_params h_model_params
  s_model_params="      model: \"${litellm_provider_prefix}/${s_model}\""
  o_model_params="      model: \"${litellm_provider_prefix}/${o_model}\""
  h_model_params="      model: \"${litellm_provider_prefix}/${h_model}\""
  if [ -n "$litellm_api_base_line" ]; then
    s_model_params="${s_model_params}
${litellm_api_base_line}"
    o_model_params="${o_model_params}
${litellm_api_base_line}"
    h_model_params="${h_model_params}
${litellm_api_base_line}"
  fi

  # Start building config with explicit mappings for Claude Code UI
  model_list_yaml=$(cat <<EOF
  - model_name: "sonnet"
    litellm_params:
${s_model_params}
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
${model_info_block}
  - model_name: "opus"
    litellm_params:
${o_model_params}
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
${model_info_block}
  - model_name: "haiku"
    litellm_params:
${h_model_params}
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
${model_info_block}
EOF
)

  append_model_yaml() {
    local name="$1"
    local target_model="$2"
    if [[ "$model_list_yaml" == *"model_name: \"$name\""* ]]; then
      return
    fi
    model_list_yaml+=$(cat <<EOF

  - model_name: "$name"
    litellm_params:
      model: "openai/$target_model"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
EOF
)
  }

  # Also expose the "real" model names, so Claude Code can use/show them directly
  append_model_yaml "$s_model" "$s_model"
  append_model_yaml "$o_model" "$o_model"
  append_model_yaml "$h_model" "$h_model"

  # Optional friendly aliases (e.g. "monica") -> real model names
  if [ -n "$s_alias" ]; then append_model_yaml "$s_alias" "$s_model"; fi
  if [ -n "$o_alias" ]; then append_model_yaml "$o_alias" "$o_model"; fi
  if [ -n "$h_alias" ]; then append_model_yaml "$h_alias" "$h_model"; fi

  # Add extra models as searchable models
  if [ -n "${extra_models:-}" ]; then
    local clean_models="${extra_models//\"/}"
    for m in $clean_models; do
      # Avoid duplicate definitions if m is already one of the defaults
      if [ "$m" != "$s_model" ] && [ "$m" != "$o_model" ] && [ "$m" != "$h_model" ]; then
        model_list_yaml+=$(cat <<EOF

  - model_name: "$m"
    litellm_params:
      model: "openai/$m"
      api_base: "${compat_base_url}"
      api_key: "os.environ/CODEX_COMPAT_API_KEY"
EOF
)
      fi
    done
  fi

  export CODEX_COMPAT_API_KEY="$compat_api_key"

  ensure_litellm

  cat > "$config_path" <<EOF
general_settings:
  master_key: "$gateway_token"

litellm_settings:
  drop_params: true

model_list:
$model_list_yaml
EOF

  start_litellm_gateway "$config_path"

  export ANTHROPIC_BASE_URL="http://$host:$port"
  # Use AUTH_TOKEN and unset API_KEY to avoid Claude Code CLI conflict warning
  export ANTHROPIC_AUTH_TOKEN="$gateway_token"
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then unset ANTHROPIC_API_KEY; fi
  
  if is_truthy "${CLAUDE_CODE_UI_SHOW_REAL_MODELS:-false}"; then
    export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-$s_model}"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$s_model"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="$o_model"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$h_model"
  elif [ -n "$s_alias" ] || [ -n "$o_alias" ] || [ -n "$h_alias" ]; then
    export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-${s_alias:-sonnet}}"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="${s_alias:-sonnet}"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="${o_alias:-opus}"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="${h_alias:-haiku}"
  else
    export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-sonnet}"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="sonnet"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="opus"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="haiku"
  fi
  export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="${CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS:-1}"

  echo "------------------------------------------------"
  echo "🛠  OPENAI-COMPATIBLE MODE (via LiteLLM Gateway)"
  echo "  Gateway: $ANTHROPIC_BASE_URL"
  echo "  Upstream: ${compat_base_url}"
  echo ""
  echo "  Claude Code UI (/model) labels are fixed:"
  echo "  ❯ Sonnet / Opus / Haiku"
  echo ""
  echo "  Gateway mapping:"
  echo "  ❯ Sonnet -> ${ANTHROPIC_DEFAULT_SONNET_MODEL} (OpenAI: $s_model)"
  echo "  ❯ Opus   -> ${ANTHROPIC_DEFAULT_OPUS_MODEL} (OpenAI: $o_model)"
  echo "  ❯ Haiku  -> ${ANTHROPIC_DEFAULT_HAIKU_MODEL} (OpenAI: $h_model)"
  echo ""
  echo "  Current active model: $ANTHROPIC_MODEL"
  echo "------------------------------------------------"

  if [ "$SETUP_ONLY" -eq 1 ]; then
    echo "Setup only mode enabled. Gateway/profile prepared."
    return
  fi

  exec claude "$@"
}

PROVIDER="$(normalize_provider "${CLAUDE_CODE_PROVIDER_OVERRIDE:-${CLAUDE_CODE_PROVIDER:-minimax}}")"

# Auto-detect if base URL supports Anthropic format
if [ "$PROVIDER" = "auto" ]; then
  AUTO_API_KEY="${CLAUDE_CODE_API_KEY:-${CODAXER_API_KEY:-}}"
  AUTO_BASE_URL="${CLAUDE_CODE_BASE_URL:-${CODAXER_BASE_URL:-}}"
  if [ -n "$AUTO_API_KEY" ] && [ -n "$AUTO_BASE_URL" ]; then
    if supports_anthropic_messages "$AUTO_BASE_URL" "$AUTO_API_KEY"; then
      export CLAUDE_CODE_BASE_URL="$ANTHROPIC_DETECTED_BASE_URL"
      PROVIDER="anthropic_compatible"
    else
      PROVIDER="openai_compatible"
    fi
  else
    PROVIDER="minimax"
  fi
fi

if is_truthy "${CLAUDE_CODE_FORCE_GATEWAY:-false}"; then
  PROVIDER="openai_compatible"
fi

case "$PROVIDER" in
  minimax|anthropic|anthropic_compatible|direct)
    run_direct_anthropic "$@"
    ;;
  openai|openai_compatible|openai-compatible|codaxer|deepseek)
    run_openai_compatible "$@"
    ;;
  *)
    echo "Error: Unsupported CLAUDE_CODE_PROVIDER=$PROVIDER"
    exit 1
    ;;
esac
