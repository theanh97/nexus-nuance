# Production Public Checklist

Use this checklist before publishing to the community.

## 1) Privacy-first release flow

1. Keep local secrets in `.env` only (never publish raw `.env`).
2. Run privacy audit:

```bash
make privacy-audit
```

3. Build a sanitized release bundle:

```bash
make public-release
```

4. Publish from the generated `public-release-*` folder, not from your live workspace.

## 2) Data that stays private locally

- `data/` (runtime memory, learning logs, discovery cache, patches)
- `logs/` (agent logs may contain prompts, paths, and user context)
- `memory/`
- `.claude/`
- `.env`

These are intentionally ignored in `.gitignore` to reduce accidental leaks.

## 3) Security hardening for client-facing production

1. Put dashboard/API behind authentication (session + RBAC).
   - Quick baseline in this project: set `DASHBOARD_ACCESS_TOKEN` env var.
2. Add HTTPS (reverse proxy or managed ingress).
3. Enable rate limiting on control endpoints:
   - `/api/agent/command`
   - `/api/providers/profile`
   - `/api/chat/ask`
4. Add audit logging with user id + action + target agent.
5. Encrypt secrets at rest (Vault/KMS) instead of plain JSON.
6. Rotate provider keys periodically.

## 4) Runtime safety requirements

1. Keep `guardian` autopilot on by default.
2. Keep cost guard mode at `balanced` or `economy` for public traffic.
3. Add hard timeouts and queue limits per command.
4. Restrict shell-level actions to allowlisted commands only.

## 5) Release acceptance gates

Run from the release folder:

```bash
python3 -m py_compile run_system.py src/core/provider_profile.py monitor/app.py
pytest -q
```

Publish only when:
- privacy audit has no critical findings
- tests pass
- dashboard controls work without terminal access
