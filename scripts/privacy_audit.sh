#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

FAIL=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "== Privacy Audit =="
echo "Workspace: $ROOT_DIR"
echo

echo "[1/5] Secret-like tokens in repository"
if rg -n --hidden \
  --glob '!.git/**' \
  --glob '!.venv/**' \
  --glob '!venv/**' \
  --glob '!__pycache__/**' \
  '(sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{8,})' \
  . \
  --replace '[REDACTED_TOKEN]' > "$TMP_DIR/secret_hits.txt"; then
  echo -e "${RED}Found potential secrets:${NC}"
  cat "$TMP_DIR/secret_hits.txt"
  FAIL=1
else
  echo -e "${GREEN}No obvious token patterns found.${NC}"
fi
echo

echo "[2/5] Real keys in .env (local only, must never publish)"
if [[ -f .env ]]; then
  awk -F= '
    BEGIN { found=0 }
    /^(GLM_API_KEY|GOOGLE_API_KEY|MINIMAX_API_KEY|CLAUDE_CODE_API_KEY|CODAXER_API_KEY|CLAUDE_CODE_GATEWAY_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY)=/ {
      v=$2
      if (v != "" && v !~ /^(your_|placeholder|<|CHANGEME)/) {
        print $1
        found=1
      }
    }
    END { if (found==0) exit 1 }
  ' .env > "$TMP_DIR/env_real_keys.txt" && {
    echo -e "${YELLOW}.env contains real credentials for:${NC}"
    cat "$TMP_DIR/env_real_keys.txt"
    echo "Keep .env local only."
  } || echo -e "${GREEN}.env has no obvious real credentials.${NC}"
else
  echo "No .env file found."
fi
echo

echo "[3/5] Absolute local paths leaking identity"
if rg -n --hidden \
  --glob '!.git/**' \
  --glob '!.venv/**' \
  --glob '!venv/**' \
  --glob '!__pycache__/**' \
  --glob '!scripts/privacy_audit.sh' \
  --glob '!scripts/prepare_public_release.sh' \
  '/Users/[^/]+|C:\\\\Users\\\\[^\\\\]+' \
  . > "$TMP_DIR/path_hits.txt"; then
  echo -e "${YELLOW}Found absolute path references (review before public release):${NC}"
  head -n 80 "$TMP_DIR/path_hits.txt"
else
  echo -e "${GREEN}No absolute path leaks detected.${NC}"
fi
echo

echo "[4/5] Personal emails in workspace"
if rg -n --hidden \
  --glob '!.git/**' \
  --glob '!.venv/**' \
  --glob '!venv/**' \
  --glob '!__pycache__/**' \
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
  . > "$TMP_DIR/email_hits.txt"; then
  echo -e "${YELLOW}Found emails (could be personal or upstream metadata):${NC}"
  head -n 80 "$TMP_DIR/email_hits.txt"
else
  echo -e "${GREEN}No email addresses found.${NC}"
fi
echo

echo "[5/5] Private key blocks"
if rg -n --hidden \
  --glob '!.git/**' \
  --glob '!.venv/**' \
  --glob '!venv/**' \
  --glob '!__pycache__/**' \
  --glob '!scripts/privacy_audit.sh' \
  'BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY|PRIVATE KEY-----' \
  . > "$TMP_DIR/private_key_hits.txt"; then
  echo -e "${RED}Found private key material signatures:${NC}"
  cat "$TMP_DIR/private_key_hits.txt"
  FAIL=1
else
  echo -e "${GREEN}No private key blocks detected.${NC}"
fi
echo

if [[ "$FAIL" -eq 0 ]]; then
  echo -e "${GREEN}Privacy audit completed: PASS (with warnings possible).${NC}"
  exit 0
fi

echo -e "${RED}Privacy audit completed: FAIL.${NC}"
echo "Resolve the findings above before publishing."
exit 2
