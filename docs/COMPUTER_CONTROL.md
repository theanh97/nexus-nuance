# Computer Control (Monitor/Guardian)

Updated: 2026-02-15

## Mục tiêu

- Cho phép Monitor/Guardian thực hiện thao tác input/accept/deny ở mức máy tính cho các prompt điều khiển.
- Ưu tiên an toàn: chỉ expose action allowlist.

## Backend

- Status: `GET /api/computer-control/status`
- Action: `POST /api/computer-control/action`
- Toggle: `POST /api/computer-control/toggle`
- Event digest (lọc nhiễu): `GET /api/events/digest`
- OpenClaw browser (headless):
  - `POST /api/openclaw/browser` (start/status/open/snapshot/click/type/press/hard_refresh)
  - `POST /api/openclaw/flow/dashboard` (auto open + hard refresh + approve/deny + chat)
  - `POST /api/openclaw/extension/attach` (auto install + attach extension)

## Action hỗ trợ

- `approve_prompt` (Y + Enter)
- `deny_prompt` (ESC)
- `continue_prompt` (Enter)
- `press` (allowlist key)
- `type_text` (khi backend pyautogui khả dụng)

## Env flags

- `MONITOR_COMPUTER_CONTROL_ENABLED=true|false`
- `MONITOR_AUTO_APPROVE_SAFE_DECISIONS=true|false`
- `MONITOR_AUTO_APPROVE_ALL_DECISIONS=true|false` (tự duyệt toàn bộ command intervention)
- `MONITOR_AUTO_EXECUTE_COMMANDS=true|false` (tự chạy command khi đã duyệt)
- `MONITOR_ALLOW_UNSAFE_COMMANDS=true|false` (bỏ allowlist, rủi ro cao)
- `GUARDIAN_FORCE_APPROVE_ALL=true|false` (Guardian tự duyệt tất cả)
- `OPENCLAW_BROWSER_AUTO_START=true|false`
- `OPENCLAW_BROWSER_TIMEOUT_MS=30000`
- `OPENCLAW_DASHBOARD_URL=http://127.0.0.1:5050/`
- `OPENCLAW_AUTO_ATTACH_ENABLED=true|false`
- `OPENCLAW_AUTO_ATTACH_RETRIES=2`
- `OPENCLAW_AUTO_ATTACH_DELAY_SEC=1.2`
- `OPENCLAW_AUTO_ATTACH_HEURISTIC=true|false`
- `OPENCLAW_AUTO_ATTACH_ICON_MATCH=true|false`
- `OPENCLAW_CHROME_APP="Google Chrome"`
- `OPENCLAW_LAUNCH_WITH_EXTENSION=true|false`
- `OPENCLAW_FORCE_NEW_CHROME_INSTANCE=true|false`

## Backend provider

- Primary: `pyautogui`
- Fallback macOS: `osascript` (key code)

## Lưu ý quyền hệ thống (macOS)

- Cần cấp Accessibility cho terminal/process chạy dashboard để keyboard automation hoạt động ổn định.
