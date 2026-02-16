# NEXUS - The Dream Team

> **"ONE SYSTEM. INFINITE PROJECTS. CONTINUOUS EVOLUTION."**

A self-learning, self-improving AI development system with world-class agents working together 24/7.

---

## The Dream Team

| Agent | Role | Primary Model | Fallback |
|-------|------|---------------|----------|
| **ORION** | Supreme PM | MiniMax M2.5 | GLM-5 |
| **PIXEL** | UI/UX Visionary | MiniMax M2.5 | GLM-4.6V |
| **NOVA** | Code Architect | MiniMax M2.5 | GLM-5 |
| **CIPHER** | Security Master | MiniMax M2.5 | GLM-5 |
| **ECHO** | QA Engineer | GLM-4-Flash | - |
| **FLUX** | DevOps | MiniMax M2.5 | GLM-5 |

### Computer Control
- **OpenClaw** - AI-powered computer automation (Browser, Mouse, Keyboard)
- **Model**: MiniMax M2.5

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    NEXUS v2.0 "Evolution"                   │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐  │
│   │              ORION (MiniMax M2.5)                    │  │
│   │           Central Controller & Decisions             │  │
│   └───────────────────────┬─────────────────────────────┘  │
│                           │                                 │
│        ┌──────────────────┼──────────────────┐             │
│        │                  │                  │             │
│        ▼                  ▼                  ▼             │
│   ┌─────────┐      ┌──────────┐      ┌──────────┐        │
│   │  NOVA   │      │  PIXEL   │      │  CIPHER  │        │
│   │ (Code)  │      │ (UI/UX)  │      │(Security)│        │
│   └────┬────┘      └────┬─────┘      └────┬─────┘        │
│        │                │                 │               │
│        └────────────────┼─────────────────┘               │
│                         │                                 │
│                         ▼                                 │
│              ┌─────────────────────┐                      │
│              │    ECHO + FLUX      │                      │
│              │   (QA + Deploy)     │                      │
│              └──────────┬──────────┘                      │
│                         │                                 │
│         ┌───────────────┴───────────────┐                │
│         │                               │                │
│         ▼                               ▼                │
│    [ IMPROVE ]                    [ COMPLETE ]            │
│         │                               │                │
│         └───────────────────────────────┘                │
│                         │                                 │
│                         ▼                                 │
│              ┌─────────────────────┐                      │
│   ┌─────────│    OpenClaw         │──────────┐           │
│   │         │  (Computer Control) │          │           │
│   │         │   MiniMax M2.5      │          │           │
│   │         └─────────────────────┘          │           │
│   │                                          │           │
│   ▼                                          ▼           │
│ [ Browser ]                          [ Mouse/Keyboard ]  │
└─────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
nexus-nuance/
│
├── nexus/                    # Core Brain
│   ├── agents/               # Dream Team agents
│   │   ├── orion.py          # PM Agent
│   │   ├── nova.py           # Code Architect
│   │   ├── pixel.py          # UI/UX Visionary
│   │   ├── cipher.py         # Security Master
│   │   ├── echo.py           # QA Engineer
│   │   └── flux.py           # DevOps
│   │
│   ├── memory/               # Learning & Memory System
│   │   ├── core/             # Core memory operations
│   │   ├── learning/         # Learning mechanisms
│   │   ├── feedback/         # User feedback processing
│   │   └── knowledge/        # Knowledge management
│   │
│   ├── orchestration/        # Multi-agent coordination
│   │   ├── model_router.py   # Model routing
│   │   ├── agent.py          # Base agent
│   │   └── computer_controller.py
│   │
│   └── self_improvement/     # SICA-style self-modification
│       ├── discovery.py      # Find improvement opportunities
│       ├── benchmark.py      # Evaluate performance
│       ├── patch_generator.py # Generate code patches
│       ├── safe_apply.py     # Safely apply changes
│       ├── archive.py        # Archive analysis
│       └── engine.py         # Main orchestrator
│
├── wisdom/                   # Cross-Project Learning
│   ├── patterns/             # Learned patterns
│   ├── insights/             # AI-generated insights
│   ├── experiments/          # Experiment tracking
│   └── feedback_loops/       # Cross-project feedback
│
├── projects/                 # Multi-Project Support
│   ├── _templates/           # Project templates
│   └── active/               # Active projects
│
├── infrastructure/           # Deployment & Ops
├── monitor/                  # Web Dashboard
├── scripts/                  # Utility Scripts
├── tests/                    # Test Suite
│
├── data/                     # Data Storage (gitignored)
│   ├── memory/               # Knowledge store
│   ├── logs/                 # System logs
│   └── state/                # Learning state
│
├── config/                   # Configuration
│   ├── mcp_config.json       # MCP configuration
│   └── settings.yaml         # System settings
│
├── README.md                 # Main documentation
├── ARCHITECTURE.md           # Full architecture
├── pyproject.toml            # Project metadata
├── Makefile                  # Common commands
└── requirements.txt          # Dependencies
```

---

## Quick Start

```bash
# Clone repository
git clone https://github.com/theanh97/nexus-nuance.git
cd nexus-nuance

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start the system
python3 run_system.py --host 127.0.0.1 --port 5050

# Or use Makefile
make install
make start
make status
```

---

## OpenClaw Setup (Computer Control)

```bash
# Install OpenClaw
brew install openclaw

# Verify installation
openclaw --version

# Configure MiniMax as default model
# Edit ~/.openclaw/openclaw.json:
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "minimax/MiniMax-M2.5"
      }
    }
  },
  "models": {
    "providers": {
      "minimax": {
        "baseUrl": "https://api.minimax.io/anthropic",
        "api": "openai-completions",
        "models": [
          {
            "id": "MiniMax-M2.5",
            "name": "MiniMax M2.5",
            "contextWindow": 128000
          }
        ]
      }
    }
  }
}

# Set API key
export MINIMAX_API_KEY=your_key_here

# Start OpenClaw gateway
openclaw gateway start
```

---

## Self-Improvement System

NEXUS includes a SICA-inspired self-improvement engine:

```
DISCOVER → ANALYZE → GENERATE → APPLY → MEASURE → LEARN
```

### Features:
- **Discovery**: Finds code quality, performance, security issues
- **Benchmark**: Measures system performance before/after changes
- **Patch Generation**: Creates code fixes using templates
- **Safe Apply**: Backup + automatic rollback on test failure
- **Archive Analysis**: Tracks improvement history and patterns

### Usage:

```python
from nexus.self_improvement import get_improvement_engine

engine = get_improvement_engine()
result = engine.run_cycle()

print(f"Improvements applied: {result.patches_successful}")
```

---

## Configuration

### API Keys Required

| Service | Purpose | Required |
|---------|---------|----------|
| **MiniMax** | Primary model + OpenClaw | ✅ Yes |
| GLM (z.ai) | Fallback model | ❌ Optional |
| Google AI | Fast tasks | ❌ Optional |

### Model Routing

```yaml
# Adaptive routing (default)
MODEL_ROUTING_MODE=adaptive

# Or force single model
MODEL_ROUTING_MODE=single
DEFAULT_SINGLE_MODEL=minimax/MiniMax-M2.5
```

---

## Dashboard API

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | System status |
| `POST /api/chat/ask` | Send command |
| `GET /api/computer-control/status` | OpenClaw status |
| `POST /api/openclaw/browser` | Browser control (returns structured error_code + token_info health data) |
| `POST /api/openclaw/flow/dashboard` | Auto dashboard flow (short-circuits on token/health failure and reports step-by-step steps) |
| `POST /api/openclaw/commands` | Unified OpenClaw dispatch with policy decision + worker routing |
| `GET /api/openclaw/commands/<request_id>` | OpenClaw command lifecycle/status |
| `GET /api/openclaw/metrics` | OpenClaw counters (token/health/attach/flow), supports `?events=1` |
| `GET /api/openclaw/metrics/prometheus` | Prometheus metrics for OpenClaw monitoring |
| `GET /api/control-plane/workers` | Control-plane worker registry + circuit states |
| `POST /api/control-plane/workers/register` | Register worker to control-plane |
| `POST /api/control-plane/workers/heartbeat` | Worker heartbeat + backpressure actions |
| `GET /api/guardian/autopilot/status` | Guardian autopilot + control-plane status |
| `POST /api/guardian/control` | Guardian control actions (`pause_autopilot`, `resume_autopilot`, `shutdown`) |
| `GET/POST /api/autonomy/profile` | Read/set autonomy profile (`safe`, `balanced`, `full_auto`) and sync to Orion runtime |
| `GET /api/slo/summary` | Balanced SLO summary |
| `GET /api/events/digest` | Event digest |

`full_auto` behavior:
- User pause is treated as temporary in automation context; Orion is auto-resumed after `MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC`.
- `pause_autopilot` becomes a maintenance lease in `full_auto` (not an infinite OFF) via `MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC`.

---

## Learning System

Implements **human learning principles**:

1. **Daily Cycle**: Collect → Digest → Filter → Apply
2. **Spaced Repetition**: Review at optimal intervals
3. **Active Recall**: Test knowledge retrieval
4. **Deliberate Practice**: Focus on weaknesses
5. **Metacognition**: Self-awareness of learning
6. **Cross-Project Learning**: Share wisdom across all projects

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

- [Architecture](../ARCHITECTURE.md)
- [Computer Control](COMPUTER_CONTROL.md)
- [Provider Settings](PROVIDER_QUICK_SETTINGS.md)
- [Human-Like Learning](HUMAN_LIKE_LEARNING.md)
- [Guardian Agent](GUARDIAN_AGENT.md)

---

*NEXUS - Building the future, one iteration at a time.*
