# Provider Quick Settings (GLM + Claude Code profiles)

Last updated: 2026-02-16

## Muc tieu

- Giu luong GLM nhu cu.
- Ho tro Claude Code qua profile Anthropic-compatible hoac OpenAI-compatible.
- Tap trung vao cac diem can sua nhanh khi doi key/model/base URL.

## Lenh su dung hang ngay

- Claude Code qua profile trong `.env`: `mm`
- Setup profile va gateway (khong mo Claude): `mm --setup-only`
- Kiem tra CLI MiniMax profile: `mm --version`
- Chay loop hien tai cua project: `python3 scripts/main_loop.py --app "A beautiful weather dashboard with 7-day forecast" --max-iterations 20 --target-score 8.0`

## Co che dang dung

- `glm`: giu nguyen co che GLM ban dang dung.
- `mm`: goi Claude Code CLI, map env theo `CLAUDE_CODE_PROVIDER`.
- Script map env: `scripts/claude-with-minimax.sh`
- Neu provider la `openai_compatible`, script tu dong dung LiteLLM local gateway cho Claude Code.

## Noi can sua khi thay doi cau hinh

- Bien moi truong chinh: `.env`
- Mau bien moi truong cho team: `.env.example`
- Mapping provider trong config: `config/settings.yaml`
- Wrapper chay Claude profile: `scripts/claude-with-minimax.sh`
- Shortcut shell `mm`: `~/.zshrc`

## Bien moi truong quan trong

- `GLM_API_KEY`: key cho luong GLM hien tai.
- `GLM_API_BASE`: base URL GLM (khuyen nghi: `https://api.z.ai/api/openai/v1`).
- `MINIMAX_API_KEY`: key MiniMax.
- `MINIMAX_MODEL`: model MiniMax (hien tai: `MiniMax-M2.5`).
- `MINIMAX_ANTHROPIC_BASE_URL`: gateway Anthropic-compatible (hien tai: `https://api.minimax.io/anthropic`).
- `CLAUDE_CODE_PROVIDER`: `minimax`, `anthropic_compatible`, `openai_compatible`, `codaxer`, hoac `auto`.
- `CLAUDE_CODE_API_KEY`: key profile cho `mm`.
- `CLAUDE_CODE_BASE_URL`: base URL profile cho `mm`.
- `CLAUDE_CODE_MODEL`: model chinh profile.
- `CLAUDE_CODE_MODEL_SONNET` / `CLAUDE_CODE_MODEL_OPUS` / `CLAUDE_CODE_MODEL_HAIKU`: map model khi provider la `openai_compatible`.
- `CLAUDE_CODE_GATEWAY_HOST` / `CLAUDE_CODE_GATEWAY_PORT` / `CLAUDE_CODE_GATEWAY_TOKEN`: cau hinh LiteLLM local gateway.
- `CODAXER_API_KEY` / `CODAXER_BASE_URL`: profile fallback khi muon chuyen qua Codaxer ma khong doi script.
- `CODAXER_MODEL_SONNET` / `CODAXER_MODEL_OPUS` / `CODAXER_MODEL_HAIKU`: model map fallback cho Codaxer.

## Cach doi model MiniMax nhanh

1. Mo `.env`.
2. Sua `MINIMAX_MODEL` va `CLAUDE_CODE_MODEL`.
3. Chay lai terminal (hoac `source ~/.zshrc`).
4. Test: `mm --version`.

## Cach doi API key MiniMax nhanh

1. Mo `.env`.
2. Sua `MINIMAX_API_KEY`.
3. Neu muon profile `mm` tach rieng key, sua them `CLAUDE_CODE_API_KEY`.
4. Test: `mm --version`, sau do chay `mm`.

## OpenAI-compatible profile cho Claude Code (tu dong gateway)

1. Trong `.env`, dat:
   - `CLAUDE_CODE_PROVIDER=openai_compatible`
   - `CLAUDE_CODE_API_KEY=<your_openai_compatible_key>`
   - `CLAUDE_CODE_BASE_URL=http://<host>:<port>/v1`
2. (Khuyen nghi) Dat model map:
   - `CLAUDE_CODE_MODEL_SONNET=gpt-5.1-codex`
   - `CLAUDE_CODE_MODEL_OPUS=gpt-5.1-codex-max`
   - `CLAUDE_CODE_MODEL_HAIKU=gpt-5.1-codex-mini`
3. Chay setup:
   - `mm --setup-only`
4. Chay Claude:
   - `mm`

## Chuyen nhanh sang Codaxer neu profile chinh gap loi

1. Set fallback trong `.env`:
   - `CODAXER_API_KEY=<your_codaxer_key>`
   - `CODAXER_BASE_URL=http://<codaxer-host>:<port>/v1`
2. Set `CLAUDE_CODE_PROVIDER=codaxer` de uu tien profile Codaxer.
3. Neu can, dat model map:
   - `CODAXER_MODEL_SONNET=...`
   - `CODAXER_MODEL_OPUS=...`
   - `CODAXER_MODEL_HAIKU=...`
4. Chay:
   - `mm --setup-only`
   - `mm`

## Luu y bao mat

- Khong commit `.env`.
- Neu key tung duoc chia se cong khai, nen rotate key tren MiniMax dashboard.
