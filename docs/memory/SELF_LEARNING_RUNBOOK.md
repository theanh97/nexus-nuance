# Self-Learning V2 Runbook

## Feature Flags
- `ENABLE_PROPOSAL_V2=true`
- `ENABLE_EXPERIMENT_EXECUTOR=true`
- `EXECUTION_MODE_DEFAULT=safe`
- `ENABLE_POLICY_BANDIT=true`
- `ENABLE_WINDOWED_SELF_CHECK=true`
- `ENABLE_SYNTHETIC_OPPORTUNITIES=true`
- `SYNTHETIC_OPPORTUNITY_THRESHOLD=3`
- `VERIFICATION_RETRY_INTERVAL_SECONDS=300`
- `VERIFICATION_RETRY_MAX_ATTEMPTS=3`
- `ENABLE_NORMAL_MODE_CANARY=true`
- `NORMAL_MODE_MAX_PER_HOUR=1`
- `NORMAL_MODE_MIN_PRIORITY=0.9`
- `NORMAL_MODE_ALLOWED_RISK=low`
- `NORMAL_MODE_COOLDOWN_SECONDS=1800`
- `ENABLE_EXECUTOR_REAL_APPLY=false` (default safety)
- `EXECUTOR_REAL_APPLY_MAX_PATCHES=1`

## Operational Checks
1. `python3 scripts/start.py --status`
2. Verify sections:
- `Proposal Funnel`
- `Outcome Quality`
- `Policy State`
- `Opportunity Windows`
- Ensure `pending_recheck_runs` does not grow unbounded.
- Check `trend_24h` and `trend_7d` verdict distribution.

## Migration
- One-shot migration is executed on `LearningLoop` init.
- Manual run:
`python3 -m src.memory.migrate_proposals_v1_to_v2`

## Rollout Stages
1. Shadow: enable proposal v2, keep executor off.
2. Safe canary: executor on + `safe` mode.
3. Expanded safe: 100% safe mode.
4. Controlled normal canary:
   - keep `ENABLE_NORMAL_MODE_CANARY=true`
   - allow only `NORMAL_MODE_ALLOWED_RISK=low`
   - limit by `NORMAL_MODE_MAX_PER_HOUR`
5. If `loss` appears in normal mode:
   - guardrail auto-activates cooldown (`NORMAL_MODE_COOLDOWN_SECONDS`)
   - force subsequent runs back to `safe` until cooldown expires
5. Real apply (optional): set `ENABLE_EXECUTOR_REAL_APPLY=true` only after backup + monitoring readiness.

## Failure Response
1. Disable executor:
- set `ENABLE_EXPERIMENT_EXECUTOR=false`
2. Disable policy adaptation:
- set `ENABLE_POLICY_BANDIT=false`
3. Disable real apply immediately:
- set `ENABLE_EXECUTOR_REAL_APPLY=false`
4. Keep evidence for audit:
- `data/memory/outcome_evidence.jsonl`
- `data/experiments/experiment_runs_v2.json`

## Verification Retry Behavior
- Low-confidence/weak-signal inconclusive outcomes are marked `pending_recheck=true`.
- Proposal remains `executed` until re-check converges.
- Learning loop retries verification periodically based on:
  - `VERIFICATION_RETRY_INTERVAL_SECONDS`
  - `VERIFICATION_RETRY_MAX_ATTEMPTS`
