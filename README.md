# NEXUS - Self-Improving Multi-Project Automation System

> **"ONE SYSTEM. INFINITE PROJECTS. CONTINUOUS EVOLUTION."**

A self-learning, self-improving AI development system powered by **MiniMax M2.5** and **OpenClaw** for autonomous computer control.

---

## Features

- **Self-Improvement Engine** - SICA-inspired self-modification with safe rollback
- **Multi-Project Support** - Orchestrate tens to hundreds of projects simultaneously
- **Cross-Project Learning** - Wisdom hub accumulates patterns from all projects
- **AI Computer Control** - OpenClaw integration for autonomous desktop automation
- **Multi-Model Support** - MiniMax M2.5 (primary), GLM-5 (fallback)

---

## The Dream Team

| Agent | Role | Model |
|-------|------|-------|
| **ORION** | Supreme PM | MiniMax M2.5 |
| **PIXEL** | UI/UX Visionary | MiniMax M2.5 |
| **NOVA** | Code Architect | MiniMax M2.5 |
| **CIPHER** | Security Master | MiniMax M2.5 |
| **ECHO** | QA Engineer | GLM-4-Flash |
| **FLUX** | DevOps | MiniMax M2.5 |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    NEXUS v2.0 "Evolution"                   │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   nexus/    │  │   wisdom/   │  │     projects/       │ │
│  │  - agents   │  │  - patterns │  │  - _templates/      │ │
│  │  - memory   │  │  - insights │  │  - active/          │ │
│  │  - self_    │  │  - lessons  │  │                     │ │
│  │  improvement│  │             │  │                     │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│                                                             │
│                      ↓ ↑                                    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              OpenClaw (Computer Control)             │   │
│  │              Model: MiniMax M2.5                     │   │
│  │              - Browser automation                    │   │
│  │              - Screenshot analysis                   │   │
│  │              - Mouse/keyboard control                │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Self-Improvement Loop

```
DISCOVER → ANALYZE → GENERATE → APPLY → MEASURE → LEARN
    │                                              │
    └──────────────────────────────────────────────┘
```

1. **DISCOVER** - Find improvement opportunities (code quality, performance, security)
2. **ANALYZE** - Prioritize by expected value
3. **GENERATE** - Create code patches
4. **APPLY** - Safely apply with backup + rollback
5. **MEASURE** - Run benchmarks
6. **LEARN** - Update wisdom hub

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/theanh97/nexus-nuance.git
cd nexus-nuance

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your API keys

# Start the system
python3 run_system.py --host 127.0.0.1 --port 5050
```

---

## Configuration

### Required API Keys

| Service | Purpose | Required |
|---------|---------|----------|
| **MiniMax** | Primary AI model + OpenClaw | ✅ Yes |
| GLM (z.ai) | Fallback model | ❌ Optional |
| Google AI | Fast tasks | ❌ Optional |

### OpenClaw Setup

```bash
# Install OpenClaw CLI
brew install openclaw

# Configure MiniMax as default model
# Edit ~/.openclaw/openclaw.json:
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "minimax/MiniMax-M2.5"
      }
    }
  }
}

# Set MiniMax API key
export MINIMAX_API_KEY=your_key_here
```

---

## Project Structure

```
nexus-nuance/
├── nexus/                    # Core brain
│   ├── agents/               # AI agents
│   ├── memory/               # Learning system
│   ├── orchestration/        # Multi-agent coordination
│   └── self_improvement/     # Self-modification engine
│
├── wisdom/                   # Cross-project learning
│   ├── patterns/             # Learned patterns
│   ├── insights/             # AI-generated insights
│   └── experiments/          # Experiment tracking
│
├── projects/                 # Multi-project support
│   ├── _templates/           # Project templates
│   └── active/               # Active projects
│
├── infrastructure/           # Deployment configs
├── monitor/                  # Web dashboard
├── scripts/                  # Utility scripts
├── tests/                    # Test suite
│
├── ARCHITECTURE.md           # Full architecture docs
├── .env.example              # Environment template
└── requirements.txt          # Dependencies
```

---

## Dashboard

Access the monitoring dashboard at `http://127.0.0.1:5050`

Features:
- Real-time agent status
- Self-improvement cycle tracking
- Wisdom hub visualization
- OpenClaw browser control

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | System status |
| `POST /api/chat/ask` | Send command to agents |
| `GET /api/computer-control/status` | OpenClaw status |
| `POST /api/openclaw/browser` | Browser automation |
| `GET /api/events/digest` | Event digest |

---

## Documentation

- [Architecture](ARCHITECTURE.md) - Full system architecture
- [Provider Settings](docs/PROVIDER_QUICK_SETTINGS.md) - Model configuration
- [Computer Control](docs/COMPUTER_CONTROL.md) - OpenClaw integration
- [Human-Like Learning](docs/HUMAN_LIKE_LEARNING.md) - Learning system

---

## License

MIT License

---

## Credits

- **NEXUS** - Self-improving AI system
- **OpenClaw** - AI computer control
- **MiniMax** - Primary AI model
- **SICA** - Self-improvement inspiration (arXiv:2025)

---

*Building the future, one iteration at a time.*
