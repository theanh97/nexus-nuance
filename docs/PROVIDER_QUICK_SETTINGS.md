# Provider Quick Settings (MiniMax & Codex)

Last updated: 2026-02-16

## Muc tieu

- Su dung song song nhieu backend cho Claude Code.
- Tanh viec phai sua `.env` lien tuc khi doi backend.

## Lenh su dung hang ngay

### 1. Dung MiniMax 2.5 (Backend goc)
- Lệnh: `./mm`
- Đặc điểm: Chạy trực tiếp qua API MiniMax Anthropic-compatible.

### 2. Dung Codex GPT-5 (Backend moi)
- Lệnh: `./mc` (MiniMax Codex)
- Đặc điểm: Chạy qua LiteLLM gateway, hỗ trợ gpt-5.3, gpt-5.1-max, spark...
- Setup lần đầu (hoặc khi đổi key): `./mc --setup-only`

## Co che hoat dong

- Cả 2 lệnh đều dùng chung script logic tại `scripts/claude-with-minimax.sh`.
- Lệnh `./mm` ép biến `CLAUDE_CODE_PROVIDER=minimax`.
- Lệnh `./mc` ép biến `CLAUDE_CODE_PROVIDER=codaxer`.

## Bien moi truong quan trong

### Cho MiniMax (`./mm`)
- `MINIMAX_API_KEY`: Key của MiniMax.
- `MINIMAX_MODEL`: Model mặc định (MiniMax-M2.5).

### Cho Codex (`./mc`)
- `CODAXER_API_KEY`: Key mới nhất của Codex.
- `CODAXER_BASE_URL`: Endpoint của Codex.
- `CODAXER_MODELS`: Danh sách model khả dụng.
- Ánh xạ tầng: `CODAXER_MODEL_SONNET`, `CODAXER_MODEL_OPUS`, `CODAXER_MODEL_HAIKU`.

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
   - `CODAXER_MODELS="gpt-5.1-codex-max gpt-5 gpt-5.1-codex ..."`
2. Set `CLAUDE_CODE_PROVIDER=codaxer` de uu tien profile Codaxer.
3. Neu can, dat model map (fallback khi `CODAXER_MODELS` trong):
   - `CODAXER_MODEL_SONNET=...`
   - `CODAXER_MODEL_OPUS=...`
   - `CODAXER_MODEL_HAIKU=...`
4. Chay:
   - `mm --setup-only`
   - `mm`

## Luu y bao mat

- Khong commit `.env`.
- Neu key tung duoc chia se cong khai, nen rotate key tren MiniMax dashboard.
