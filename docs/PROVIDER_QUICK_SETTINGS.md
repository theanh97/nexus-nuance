# Provider Quick Settings (GLM + MiniMax/Claude Code)

Last updated: 2026-02-15

## Muc tieu

- Giu luong GLM nhu cu.
- Them luong MiniMax 2.5 cho Claude Code qua lenh ngan `mm`.
- Tap trung vao cac diem can sua nhanh khi doi key/model/base URL.

## Lenh su dung hang ngay

- Claude Code qua MiniMax 2.5: `mm`
- Kiem tra CLI MiniMax profile: `mm --version`
- Chay loop hien tai cua project: `python3 scripts/main_loop.py --app "A beautiful weather dashboard with 7-day forecast" --max-iterations 20 --target-score 8.0`

## Co che dang dung

- `glm`: giu nguyen co che GLM ban dang dung.
- `mm`: goi Claude Code CLI, nhung map env sang MiniMax Anthropic-compatible gateway.
- Script map env: `scripts/claude-with-minimax.sh`

## Noi can sua khi thay doi cau hinh

- Bien moi truong chinh: `.env`
- Mau bien moi truong cho team: `.env.example`
- Mapping provider trong config: `config/settings.yaml`
- Wrapper chay MiniMax cho Claude Code: `scripts/claude-with-minimax.sh`
- Shortcut shell `mm`: `~/.zshrc`

## Bien moi truong quan trong

- `GLM_API_KEY`: key cho luong GLM hien tai.
- `GLM_API_BASE`: base URL GLM (khuyen nghi: `https://api.z.ai/api/openai/v1`).
- `MINIMAX_API_KEY`: key MiniMax.
- `MINIMAX_MODEL`: model MiniMax (hien tai: `MiniMax-M2.5`).
- `MINIMAX_ANTHROPIC_BASE_URL`: gateway Anthropic-compatible (hien tai: `https://api.minimax.io/anthropic`).
- `CLAUDE_CODE_API_KEY`: key profile cho `mm` (co the dung chung voi `MINIMAX_API_KEY`).
- `CLAUDE_CODE_BASE_URL`: base URL profile cho `mm`.
- `CLAUDE_CODE_MODEL`: model profile cho `mm`.

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

## Luu y bao mat

- Khong commit `.env`.
- Neu key tung duoc chia se cong khai, nen rotate key tren MiniMax dashboard.
