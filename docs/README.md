# Auto Dev Loop - The Dream Team

> **"KHÔNG CHỈ TỐT. KHÔNG CHỈ XUẤT SẮC. PHẢI LÀ ĐỈNH CAO."**

A self-learning, self-improving AI development system with world-class agents working together 24/7.

---

## The Dream Team

| Agent | Role | Model | Specialty |
|-------|------|-------|-----------|
| **ORION** | Supreme PM | GLM-5 | Best project manager on Earth |
| **PIXEL** | UI/UX Visionary | GLM-5 / GLM-4.6V | Beats Jony Ive in design |
| **NOVA** | Code Architect | GLM-5 | 10x engineer, system design master |
| **CIPHER** | Security Master | GLM-5 | OWASP expert, veto power |
| **ECHO** | QA Engineer | GLM-5 | Test automation master |
| **FLUX** | DevOps | GLM-5 | SRE master, CI/CD architect |

> **Note**: Can run with just GLM-5 for all tasks, or use specialized models for specific capabilities.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     GLM 5.0 (ORION)                         │
│              Central Controller & Decision Maker            │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   ┌─────────┐      ┌──────────┐      ┌──────────┐
   │  NOVA   │      │  PIXEL   │      │  CIPHER  │
   │ (Code)  │      │ (UI/UX)  │      │(Security)│
   └────┬────┘      └────┬─────┘      └────┬─────┘
        │                │                 │
        └────────────────┼─────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │    ECHO + FLUX      │
              │   (QA + Deploy)     │
              └──────────┬──────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
         ▼                               ▼
    [ IMPROVE ]                    [ COMPLETE ]
         │                               │
         └───────────────────────────────┘
                         │
                    (LOOP BACK)
```

---

## Project Structure

```
auto-dev-loop/
│
├── src/                    # Source code
│   ├── core/               # Core framework
│   ├── agents/             # Dream Team agents
│   └── memory/             # Learning system
│
├── data/                   # Data storage
│   ├── memory/             # Knowledge store
│   ├── logs/               # System logs
│   └── state/              # Learning state
│
├── docs/                   # Documentation
│   └── memory/             # Learning principles
│
├── dashboards/             # HTML dashboards
├── scripts/                # Utility scripts
├── tests/                  # Test suite
│
├── pyproject.toml          # Project metadata
├── Makefile                # Common commands
└── requirements.txt        # Dependencies
```

---

## Quick Start

```bash
# Install dependencies
make install

# Start the learning system
make start

# Check status
make status

# Run knowledge scan
make scan
```

---

## Learning System

Implements **human learning principles**:

1. **Daily Cycle**: Collect → Digest → Filter → Apply
2. **Spaced Repetition**: Review at optimal intervals
3. **Active Recall**: Test knowledge retrieval
4. **Deliberate Practice**: Focus on weaknesses
5. **Metacognition**: Self-awareness of learning
6. **Solution Options**: Always produce feasible next-step options + recommended path
7. **Portfolio Pivot**: Rotate focus area when one track hits diminishing returns

See `docs/memory/learning-principles.md` for details.

---

## Configuration

Edit `config/settings.yaml`:

```yaml
# Single model mode (recommended)
model: "glm-5"

# Or multi-model mode
agents:
  orion: "glm-5"
  pixel: "glm-4.6v"  # For vision tasks
  nova: "glm-5"
  cipher: "glm-5"
  echo: "glm-5"
  flux: "glm-5"
```

---

## API Keys

| Service | Purpose | Required |
|---------|---------|----------|
| GLM (z.ai) | All operations | ✅ Yes |
| Google AI | Optional: Fast tasks | ❌ No |
| Anthropic | Optional: Vision | ❌ No |

---

## Runtime Safety

To avoid stuck/conflicts when many flows run in parallel, runtime singletons are enabled:

- Learning runtime lock: `LEARNING_RUNTIME_LOCK_PATH` (default `data/state/learning_runtime.lock`)
- AutoDev runtime lock: `AUTODEV_RUNTIME_LOCK_PATH` (default `data/state/autodev_runtime.lock`)
- Knowledge scan operation lock: `SCAN_OPERATION_LOCK_PATH` (default `data/state/knowledge_scan.lock`)
- Improvement apply operation lock: `IMPROVEMENT_OPERATION_LOCK_PATH` (default `data/state/improvement_apply.lock`)
- Daily self-learning operation lock: `DAILY_CYCLE_OPERATION_LOCK_PATH` (default `data/state/daily_self_learning.lock`)

Stagnation safety (to avoid full stall when there are no improvements for many iterations):

- `ENABLE_STAGNATION_UNBLOCK=true`
- `UNBLOCK_MIN_PROPOSAL_SCORE=7.2`
- `UNBLOCK_MAX_AUTO_APPROVALS_PER_ITERATION=1`

Useful checks:

```bash
# Includes lock diagnostics + self-learning status
python3 scripts/start.py --status
```

```bash
# Dashboard API lock snapshot
curl -s http://localhost:5000/api/runtime/locks
```

```bash
# CLI lock snapshot / stale cleanup (runtime + operations)
python3 scripts/runtime_locks.py
python3 scripts/runtime_locks.py --cleanup-stale
```

If a second conflicting runtime is started, it will now fail fast instead of hanging.

---

## Gemini Subscription CLI

Gemini API and Gemini CLI subscription are handled separately:

- API path: `GOOGLE_API_KEY` (runtime-callable by default, used in normal chains)
- Subscription CLI path: `GEMINI_SUBSCRIPTION_AVAILABLE`, `GEMINI_CLI_CMD`, `GEMINI_CLI_ARGS`

Default subscription models in policy:

- `GEMINI_SUBSCRIPTION_FLASH_MODEL=gemini-3.0-flash`
- `GEMINI_SUBSCRIPTION_PRO_MODEL=gemini-3.0-pro`

To prioritize subscription CLIs in routing (including Gemini CLI) for cost-sensitive work:

```bash
ENABLE_SUBSCRIPTION_PRIMARY_ROUTING=true
SUBSCRIPTION_PRIMARY_COST_ONLY=true
```

Then run:

```bash
python3 scripts/start.py --sync-models
```

---

## The Sacred Philosophy

```
╔═════════════════════════════════════════════════════════════════════╗
║                                                                     ║
║   1. TRẢI NGHIỆM ĐỈNH CAO → Vượt Steve Jobs, Jony Ive             ║
║   2. TƯỢNG HÓA MẠNH MẼ → Visualize everything with power          ║
║   3. WORLD-CLASS ENGINEERS → Top 0.1% trong lĩnh vực               ║
║                                                                     ║
║   "WE DON'T SETTLE. WE TRANSCEND."                                  ║
║                                                                     ║
╚═════════════════════════════════════════════════════════════════════╝
```

---

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Project Brain](PROJECT_BRAIN.md)
- [Provider Quick Settings (GLM + MiniMax)](PROVIDER_QUICK_SETTINGS.md)
- [Learning Principles](memory/learning-principles.md)
- [Handover Notes](HANDOVER_2026-02-15.md)

---

*The Dream Team - Building the future, one iteration at a time.*
