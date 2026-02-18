# NEXUS UNIFIED ARCHITECTURE
## Hệ Thống Tự Học Mạnh Nhất Thế Giới

---

## 🏗️ KIẾN TRÚC TỔNG QUAN

```
╔═══════════════════════════════════════════════════════════════════════╗
║                          NEXUS BRAIN                                  ║
║                    Central Intelligence Hub                           ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  ┌─────────────────────────────────────────────────────────────────┐ ║
║  │                    AUTONOMOUS CYCLES (24/7)                     │ ║
║  │  • News Scan: 5 min     • GitHub Scan: 15 min                  │ ║
║  │  • Consolidate: 1 hour  • Deep Learning: 6 hours               │ ║
║  │  • Daily Report: 24 hours                                       │ ║
║  └─────────────────────────────────────────────────────────────────┘ ║
║                                                                       ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            ║
║  │  MEMORY  │  │ KNOWLEDGE│  │  SKILL   │  │  ACTION  │            ║
║  │   HUB    │  │  SCOUT   │  │ TRACKER  │  │ EXECUTOR │            ║
║  │          │  │          │  │          │  │          │            ║
║  │ Unified  │  │ Auto-scan│  │ Level 1-10│ │ Execute  │            ║
║  │ Storage  │  │ GitHub   │  │ Progress │  │ Tasks    │            ║
║  │          │  │ News     │  │ Delegate │  │          │            ║
║  └──────────┘  └──────────┘  └──────────┘  └──────────┘            ║
║       │              │             │             │                  ║
║       └──────────────┴─────────────┴─────────────┘                  ║
║                              │                                       ║
║                    ┌─────────▼─────────┐                            ║
║                    │   SINGLE SOURCE   │                            ║
║                    │   OF TRUTH        │                            ║
║                    │   data/brain/     │                            ║
║                    └───────────────────┘                            ║
╚═══════════════════════════════════════════════════════════════════════╝
```

---

## 📁 CẤU TRÚC FILE

### CORE MODULES (src/brain/)
| File | Purpose |
|------|---------|
| `nexus_brain.py` | **Central Hub** - Orchestrate tất cả |
| `__init__.py` | Exports |

### DATA STORAGE (data/brain/)
| File | Purpose |
|------|---------|
| `knowledge.jsonl` | Tất cả kiến thức đã học |
| `patterns.jsonl` | Patterns đã detect |
| `events.jsonl` | Events log |
| `feedback.jsonl` | User feedback |
| `skills.json` | Skill progression |
| `brain.log` | System log |
| `report_*.json` | Daily reports |

---

## 🔄 AUTONOMOUS CYCLES

### Cycle Schedule
| Cycle | Interval | Action |
|-------|----------|--------|
| `news_scan` | 5 min | Scan tech news |
| `github_scan` | 15 min | Scan GitHub trending |
| `consolidate` | 60 min | Consolidate knowledge |
| `deep_learning` | 6 hours | Deep analysis session |
| `report` | 24 hours | Generate daily report |

---

## 📊 SKILL PROGRESSION SYSTEM

```
Level 1-3: Novice      → Cần supervision
Level 4-6: Competent   → Tự làm được
Level 7-8: Expert      → Mastered, fast
Level 9-10: Master     → Ready to delegate
```

### Level Calculation
```
Level = Base(1) + Experience + Success Rate + Speed Improvement
```

---

## 🧹 CLEANUP PLAN - Loại Bỏ Trùng Lặp

### Files to REMOVE (duplicates)
```
src/memory/human_like_learning.py      → XÓA (có trong nexus/)
src/memory/learning_prioritizer.py     → XÓA (có trong nexus/)
src/memory/user_feedback_learner.py    → XÓA (có trong nexus/)
src/memory/audit_logger.py             → XÓA (có trong nexus/)
src/memory/web_search_learner.py       → XÓA (có trong nexus/)
src/memory/auto_discovery.py           → XÓA (có trong nexus/)
src/memory/api_learner.py              → XÓA (có trong nexus/)
```

### Files to KEEP (unique)
```
src/brain/nexus_brain.py               → CENTRAL HUB (new)
src/loop/autonomous_loop.py            → Task execution loop
src/evolution/evolution_engine.py      → Evolution tracking
src/memory/storage_v2.py               → Storage layer
src/memory/user_feedback.py            → Feedback handling
```

### Consolidated Data Locations
```
OLD:
  data/memory/
  data/learning/
  data/evolution/
  data/loop/

NEW (Unified):
  data/brain/           → ALL data here
```

---

## 🚀 USAGE

### Start Brain (Background)
```bash
python3 scripts/brain_daemon.py start
```

### Check Status
```bash
python3 scripts/brain_daemon.py status
```

### Stop Brain
```bash
python3 scripts/brain_daemon.py stop
```

### Python API
```python
from brain import start_brain, learn, feedback, task_executed, brain_stats

# Start brain with autonomous cycles
brain = start_brain()

# Learn new knowledge
learn('github', 'library', 'Playwright', 'Browser automation')

# Record feedback
feedback('Liên tục hỏi confirm', is_positive=False)

# Record task execution
task_executed('fix_bug', duration_ms=5000, success=True)

# Get stats
stats = brain_stats()
```

---

## 🎯 PRINCIPLES

1. **SINGLE SOURCE OF TRUTH** - Mọi data ở một chỗ
2. **NO DUPLICATES** - Mỗi module chỉ có 1 bản
3. **AUTONOMOUS** - Tự chạy, tự học, tự cải tiến
4. **EVENT-DRIVEN** - Communication qua events
5. **THREAD-SAFE** - An toàn cho concurrent access

---

## 📈 PERFORMANCE

- Memory cache for fast access
- JSONL for append-only writes
- Thread-safe operations
- Background processing
- Minimal blocking

---

*Created: 2026-02-18*
*NEXUS - The World's Smartest Self-Learning System*
