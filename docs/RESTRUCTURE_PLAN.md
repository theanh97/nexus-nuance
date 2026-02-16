# PROJECT RESTRUCTURE PLAN

## Current Issues

```
❌ Files scattered in root
❌ Memory data mixed with code
❌ Dashboards in root
❌ No clear separation of concerns
❌ Not scalable for growth
```

## Proposed Structure

```
auto-dev-loop/
│
├── 📁 src/                          # SOURCE CODE
│   ├── 📁 core/                     # Core framework
│   │   ├── __init__.py
│   │   ├── agent.py                 # Base agent class
│   │   ├── message.py               # Message protocol
│   │   ├── model_router.py          # Model routing
│   │   └── computer_controller.py   # Screen control
│   │
│   ├── 📁 agents/                   # All agents
│   │   ├── __init__.py
│   │   ├── 📁 v1/                   # Legacy agents
│   │   │   ├── orchestrator.py
│   │   │   ├── vision_analyzer.py
│   │   │   └── browser_controller.py
│   │   │
│   │   └── 📁 v2/                   # New agents
│   │       ├── __init__.py
│   │       ├── orion.py             # PM
│   │       ├── nova.py              # Code
│   │       ├── pixel.py             # UI/UX
│   │       ├── cipher.py            # Security
│   │       ├── echo.py              # QA
│   │       ├── flux.py              # DevOps
│   │       └── guardian.py          # Permissions
│   │
│   ├── 📁 memory/                   # Memory & Learning
│   │   ├── __init__.py
│   │   ├── manager.py               # Memory management
│   │   ├── scanner.py               # Knowledge scanning
│   │   ├── debugger.py              # Self-debugging
│   │   └── learning_loop.py         # Continuous learning
│   │
│   ├── 📁 api/                      # API Layer
│   │   ├── __init__.py
│   │   ├── routes.py                # API routes
│   │   └── websocket.py             # Real-time updates
│   │
│   └── 📁 utils/                    # Utilities
│       ├── __init__.py
│       ├── helpers.py
│       ├── validators.py
│       └── constants.py
│
├── 📁 config/                       # CONFIGURATION
│   ├── settings.yaml                # Main config
│   ├── mcp_config.json              # MCP servers
│   └── logging.yaml                 # Logging config
│
├── 📁 docs/                         # DOCUMENTATION
│   ├── README.md                    # Main readme
│   ├── ARCHITECTURE.md              # Architecture
│   ├── PROJECT_BRAIN.md             # Brain file
│   ├── API.md                       # API docs
│   └── 📁 memory/                   # Memory docs
│       ├── philosophies.md
│       ├── thinking-frameworks.md
│       ├── self-evolution.md
│       ├── knowledge-update.md
│       ├── self-debugging.md
│       ├── token-optimization.md
│       ├── user-preferences.md
│       ├── lessons.md
│       └── patterns.md
│
├── 📁 tests/                        # TESTS
│   ├── __init__.py
│   ├── test_memory.py
│   ├── test_agents.py
│   ├── test_learning.py
│   └── test_integration.py
│
├── 📁 scripts/                      # SCRIPTS
│   ├── start.py                     # Start system
│   ├── demo.py                      # Demo mode
│   └── migrate.py                   # Migration scripts
│
├── 📁 dashboards/                   # HTML DASHBOARDS
│   ├── main.html                    # Main dashboard
│   ├── health.html                  # Health monitor
│   └── learning.html                # Learning status
│
├── 📁 data/                         # DATA STORAGE
│   ├── 📁 memory/                   # Memory data
│   │   ├── knowledge_store.json
│   │   ├── patterns.json
│   │   ├── lessons.json
│   │   └── issues.json
│   │
│   ├── 📁 logs/                     # Logs
│   │   ├── system.log
│   │   └── decisions.log
│   │
│   └── 📁 state/                    # State files
│       └── learning_state.json
│
├── 📁 output/                       # GENERATED OUTPUT
│   └── apps/                        # Generated apps
│
├── 📁 monitor/                      # MONITORING
│   ├── app.py                       # Flask app
│   └── templates/
│
├── .env.example                     # Environment template
├── .gitignore
├── requirements.txt
├── pyproject.toml                   # Project metadata
└── Makefile                         # Common commands
```

## Migration Plan

### Phase 1: Create Structure
```bash
mkdir -p src/{core,agents,memory,api,utils}
mkdir -p docs/memory
mkdir -p tests
mkdir -p scripts
mkdir -p dashboards
mkdir -p data/{memory,logs,state}
mkdir -p output/apps
```

### Phase 2: Move Files
```bash
# Core
mv core/* src/core/

# Agents
mv agents/v2/* src/agents/
mv agents/*.py src/agents/v1/

# Memory
mv memory/*.py src/memory/

# Docs
mv *.md docs/
mv .claude/.../memory/*.md docs/memory/

# Data
mv memory/*.json data/memory/

# Dashboards
mv *.html dashboards/
```

### Phase 3: Update Imports
- Update all import paths
- Create proper __init__.py files
- Update config paths

### Phase 4: Create Entry Points
```python
# src/__init__.py
from .memory import start_learning, status, health_check
from .agents import get_agent

# scripts/start.py
from src import start_learning
start_learning()
```

## Benefits

```
✅ Clear separation of concerns
✅ Easy to find files
✅ Scalable for growth
✅ Professional structure
✅ Easy testing
✅ Clean imports
```

## Commands After Restructure

```bash
# Start system
python scripts/start.py

# Run tests
pytest tests/

# View dashboard
open dashboards/main.html

# Check memory
cat data/memory/knowledge_store.json
```

---

*Plan created: 2025-02-15*
*Status: PENDING APPROVAL*
