# Computer Control (Monitor/Guardian)

Updated: 2026-02-16

## Mục tiêu

- Cho phép Monitor/Guardian thực hiện thao tác input/accept/deny ở mức máy tính cho các prompt điều khiển.
- Ưu tiên an toàn: chỉ expose action allowlist.

## Backend

- Status: `GET /api/computer-control/status`
- Action: `POST /api/computer-control/action`
- Toggle: `POST /api/computer-control/toggle`
- Event digest (lọc nhiễu): `GET /api/events/digest`
- OpenClaw browser (headless):
  - `POST /api/openclaw/browser` (start/status/open/snapshot/click/type/press/hard_refresh) — returns structured `error_code` fields and includes `token_info` health metadata.
  - `POST /api/openclaw/flow/dashboard` (auto open + hard refresh + approve/deny + chat) — short-circuits on token/health failure and returns step-by-step status metadata.
  - `POST /api/openclaw/extension/attach` (auto install + attach extension) — rate-limited via `OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC`, exposes `method` and `cooldown_until` when throttled.
  - `POST /api/openclaw/commands` (unified command dispatch with `risk_level`, policy decision, worker routing metadata).
  - `GET /api/openclaw/commands/<request_id>` (command lifecycle/status, including risk/decision/worker/failure class).
  - `GET /api/openclaw/queue` (queue snapshot + control-plane workers/circuits).
  - `GET /api/openclaw/metrics` (JSON counters + attach/token snapshot; optional `?events=1&limit=20`).
  - `GET /api/openclaw/metrics/prometheus` (Prometheus exposition format, ready for scrape).
- Control-plane:
  - `GET /api/control-plane/workers` (registered workers, alive workers, circuit states, blocked failure classes).
  - `POST /api/control-plane/workers/register`
  - `POST /api/control-plane/workers/heartbeat`
- Guardian control:
  - `GET /api/guardian/autopilot/status`
  - `POST /api/guardian/control` (`pause_autopilot`, `resume_autopilot`, `shutdown`; requires `reason`, optional token gate).
    - In `AUTONOMY_PROFILE=full_auto`, `pause_autopilot` creates a temporary maintenance lease (`lease_sec`) instead of infinite OFF.
- Autonomy profile:
  - `GET /api/autonomy/profile`
  - `POST /api/autonomy/profile` (`safe|balanced|full_auto`, auto-sync to runtime instances).
- SLO:
  - `GET /api/system/slo` (detailed)
  - `GET /api/slo/summary` (balanced summary + targets/current values)

### OpenClaw metrics chính

- `token_missing`: số lần command/browser flow thiếu token.
- `health_error`: số lần gateway health fail trong quá trình gọi.
- `attach_attempts`, `attach_success`, `attach_failed`, `attach_throttled`: theo dõi auto-attach chất lượng.
- `cli_failures`: số lần OpenClaw CLI fail sau retry.
- `flow_runs`, `flow_success`, `flow_failures`: tỷ lệ thành công luồng `flow/dashboard`.
- `policy_auto_approved`, `policy_manual_required`, `policy_denied`: quyết định policy tự động/manual/deny.
- `dispatch_remote`, `dispatch_failover`: số lần dispatch sang worker khác hoặc failover.
- `circuit_opened`, `circuit_blocked`: trạng thái bảo vệ circuit-breaker theo worker/failure class.

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
- `OPENCLAW_SELF_HEAL_OPEN_DASHBOARD=true|false`
- `OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC=300`
- `OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC=90`
- `OPENCLAW_FLOW_OPEN_COOLDOWN_SEC=20`
- `OPENCLAW_ACTION_DEBOUNCE_SEC=6`
- `OPENCLAW_AUTO_IDEMPOTENCY_ENABLED=true|false`
- `OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC=8`
- `OPENCLAW_AUTO_ATTACH_ENABLED=true|false`
- `OPENCLAW_AUTO_ATTACH_RETRIES=2`
- `OPENCLAW_AUTO_ATTACH_DELAY_SEC=1.2`
- `OPENCLAW_AUTO_ATTACH_HEURISTIC=true|false`
- `OPENCLAW_AUTO_ATTACH_ICON_MATCH=true|false`
- `OPENCLAW_CHROME_APP="Google Chrome"`
- `OPENCLAW_LAUNCH_WITH_EXTENSION=true|false`
- `OPENCLAW_FORCE_NEW_CHROME_INSTANCE=true|false`
- `OPENCLAW_POLICY_MOSTLY_AUTO=true|false`
- `OPENCLAW_CIRCUIT_TRIGGER_COUNT=3`
- `OPENCLAW_CIRCUIT_OPEN_SEC=60`
- `OPENCLAW_CIRCUIT_CLASS_BLOCK_SEC=300`
- `OPENCLAW_CIRCUIT_ESCALATE_AFTER=2`
- `OPENCLAW_HIGH_RISK_ALLOWLIST=status,start,snapshot,...`
- `OPENCLAW_CRITICAL_KEYWORDS=shutdown,rm -rf,...`
- `OPENCLAW_HARD_DENY_KEYWORDS=shutdown,rm -rf,...`
- `CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC=35`
- `CONTROL_PLANE_MAX_WORKERS=120`
- `GUARDIAN_CONTROL_TOKEN=<optional>`
- `MONITOR_AUTOPILOT_ENABLED=true|false`
- `AUTONOMY_PROFILE=safe|balanced|full_auto`
- `MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC=240`
- `MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC=180`

## Backend provider

- Primary: `pyautogui`
- Fallback macOS: `osascript` (key code)

## Lưu ý quyền hệ thống (macOS)

- Cần cấp Accessibility cho terminal/process chạy dashboard để keyboard automation hoạt động ổn định.
