# OpenClaw Automation Handover

Date: 2026-02-17
Owner: Codex (OpenClaw automation focus)
Scope: Stop meaningless OpenClaw action loops, improve full-auto reliability, and keep Orion/Monitor running without manual friction.

## 1) Problem diagnosed

Primary issue was not model quality (GLM-5 vs MiniMax 2.5). It was control-loop behavior:
- repeated `open`/`refresh` actions due missing debounce + idempotency + cooldowns,
- UI opened new tabs even when OpenClaw dashboard health endpoint returned failure,
- self-heal fallback could trigger repeatedly when runtime state was not ready.

Observed symptoms:
- many tabs at `127.0.0.1:5050`,
- repeated blank/error pages,
- perceived "automation running" but ineffective progress.

## 2) Implemented changes

### Backend (`monitor/app.py`)
- Added anti-loop env knobs:
  - `OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC`
  - `OPENCLAW_FLOW_OPEN_COOLDOWN_SEC`
  - `OPENCLAW_ACTION_DEBOUNCE_SEC`
  - `OPENCLAW_AUTO_IDEMPOTENCY_ENABLED`
  - `OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC`
- Added helpers:
  - loopback URL detection,
  - lightweight HTTP probe for local dashboard reachability,
  - per-action debounce signature cache.
- Self-heal hardening:
  - fallback now has cooldown guard (`last_fallback`),
  - status endpoint now exposes fallback/open/debounce telemetry.
- Flow hardening:
  - `open` step guarded by cooldown lock,
  - probe local dashboard before open,
  - on cooldown, emits `open_skipped` instead of opening another tab.
- Browser action hardening:
  - `open/navigate/hard_refresh` debounced,
  - local dashboard unreachable -> fail-fast `DASHBOARD_UNREACHABLE` (no blind spam open).
- Queue idempotency:
  - auto-derived idempotency key for repeated `flow` and selected browser actions (`open/navigate/hard_refresh`) in a short window.

### Frontend (`monitor/templates/dashboard.html`)
- Added click-in-flight lock for `openOpenClaw()`.
- Added toast dedupe cooldown (`showOpenClawToastOnce`).
- Prevents `window.open(...)` when `/api/openclaw/dashboard?refresh=1` is not healthy.
- Adds short open-window debounce to avoid duplicate tabs from rapid clicks.

### Docs/config
- `docs/COMPUTER_CONTROL.md`: added new OpenClaw anti-loop env flags.
- `.env.example`: documented practical defaults for anti-loop controls.

## 3) Validation done

- Syntax checks:
  - `python3 -m py_compile monitor/app.py run_system.py` passed.
- Unit-like direct checks (python snippets):
  - second identical `flow` call skips open via cooldown,
  - second identical `browser open` call is debounced.
- Runtime smoke:
  - launched `run_system.py --host 127.0.0.1 --port 5050 --force-stale-lock`,
  - confirmed Flask + Orion loop runs and OpenClaw metrics endpoints are served.

## 4) How to operate now

Recommended flags:
- `OPENCLAW_SELF_HEAL_OPEN_DASHBOARD=false`
- `OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC=300`
- `OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC=90`
- `OPENCLAW_FLOW_OPEN_COOLDOWN_SEC=20`
- `OPENCLAW_ACTION_DEBOUNCE_SEC=6`
- `OPENCLAW_AUTO_IDEMPOTENCY_ENABLED=true`
- `OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC=8`

## 5) Remaining risks / next work for dev team

- Add API-level integration tests for repeated `open/flow` burst scenarios.
- Add explicit UI test for `openOpenClaw()` to assert no tab open when dashboard health fails.
- Add Prometheus alerting on:
  - high `flow_failures`,
  - high `cli_failures`,
  - high `queue_idempotent_hit` (indicates noisy caller).
- If still seeing loops in production, inspect callers to `/api/openclaw/flow/dashboard` and `/api/openclaw/browser` for external spam sources.

## 6) Model note (important)

Changing model (GLM-5/MiniMax 2.5) is optional tuning for cost/quality. It does not solve this loop class. The fix is orchestration guardrails implemented above.
