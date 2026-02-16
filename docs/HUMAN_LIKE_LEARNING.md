# 🧠 NEXUS Human-Like Learning System

> **The smartest AI system that learns from users, web, and itself.**

## Overview

The NEXUS Human-Like Learning System transforms "Jack - Full Automation Tools" into an autonomous learning system that:

1. **Learns from users** - Explicit feedback (thumbs up/down, corrections) + implicit signals
2. **Learns from the web** - Real-time search, trending topics, discovery
3. **Knows WHAT to learn** - Smart prioritization based on context and impact
4. **Provides JUDGMENT** - Analyzes situations and recommends actions
5. **User has FULL CONTROL** - APPROVE / NOTIFY / AUTONOMOUS modes
6. **Reports DAILY** - Full transparency every day

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    NEXUS Human-Like Learning                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ User Feedback│  │ Judgment     │  │ Audit Logger │          │
│  │ Manager      │  │ Engine       │  │              │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                 │                   │
│         └─────────────────┼─────────────────┘                   │
│                           │                                     │
│                    ┌──────▼──────┐                             │
│                    │  Control    │                             │
│                    │  Center     │◄──── User Control           │
│                    └──────┬──────┘                             │
│                           │                                     │
│  ┌──────────────┐  ┌──────▼──────┐  ┌──────────────┐          │
│  │ Web Search   │  │ Learning    │  │ Daily       │          │
│  │ Learner      │  │ Prioritizer │  │ Intelligence│          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                 │                   │
│         └─────────────────┼─────────────────┘                   │
│                           │                                     │
│                    ┌──────▼──────┐                             │
│                    │  Daily      │                             │
│                    │  Report     │                             │
│                    └─────────────┘                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. User Feedback Manager (`user_feedback.py`)

Learns from every user interaction.

**Features:**
- Explicit feedback: thumbs up/down, ratings
- Corrections: user corrects system output
- Approvals/Denials: user approves or denies actions
- Implicit feedback: repeats, abandonment patterns

**Usage:**
```python
from src.memory import record_thumbs_up, record_correction, record_approval

# Thumbs up
record_thumbs_up("concise_code")

# Correction
record_correction("old_output", "corrected_output", {"context": "..."})

# Approval
record_approval("deploy_to_staging", {"timestamp": "now"})

# Check if should auto-approve
from src.memory import should_auto_approve
if should_auto_approve("deploy_to_staging"):
    print("Auto-approved!")
```

---

### 2. Judgment Engine (`judgment_engine.py`)

AI that thinks, not just remembers.

**Features:**
- Analyzes situations with multiple criteria (safety, efficiency, reliability, risk)
- Scores options and recommends the best
- Tracks decision outcomes for learning
- Integrates with user preferences

**Usage:**
```python
from src.memory import analyze_situation, should_proceed

# Analyze situation
result = analyze_situation(
    "Deploy to production?",
    ["Deploy now", "Deploy to staging", "Wait for approval"],
    {"risk": "high", "context": "..."}
)

print(f"Recommended: {result['recommended']['option']}")
print(f"Confidence: {result['confidence']}")
print(f"Reasoning: {result['recommended']['reasoning']}")

# Quick check
check = should_proceed("deploy to production", {"risk": "high"})
```

---

### 3. Audit Logger (`audit_logger.py`)

Full transparency - every action logged.

**Features:**
- Logs all learning events
- Tracks decisions with reasoning
- Records user feedback
- Daily log files
- Queryable history

**Usage:**
```python
from src.memory import log_learning, log_decision, get_audit_logger

# Log learning
log_learning("pattern", "New pattern discovered", "web_search")

# Log decision
log_decision("approved_deploy", "Lower risk", "success")

# Query logs
logger = get_audit_logger()
logs = logger.get_today_logs(limit=50)
print(f"Today's actions: {len(logs)}")
```

---

### 4. Web Search Learner (`web_search_learner.py`)

Daily refresh from the web.

**Features:**
- Google/Reddit/HackerNews/GitHub search
- Auto-discovery of trending topics
- Relevance scoring
- Learning from search results

**Usage:**
```python
from src.memory import search_web, learn_from_web, get_trending, run_auto_discovery

# Search
results = search_web("AI automation best practices 2024", max_results=10)

# Learn from results
discoveries = learn_from_web("AI automation", {"category": "trending"})

# Get trending
trending = get_trending()
print(f"Trending: {trending}")

# Auto discovery
summary = run_auto_discovery()
print(f"Discovered: {summary['discoveries']} new items")
```

---

### 5. Learning Prioritizer (`learning_prioritizer.py`)

Decides WHAT to learn.

**Features:**
- Context-aware prioritization
- User need weighting
- Knowledge gap tracking
- Category-based scoring

**Usage:**
```python
from src.memory import add_learning_topic, prioritize_learning, get_search_queries

# Add topics
add_learning_topic("Claude Code integration", "tooling", "user")
add_learning_topic("Python async patterns", "coding", "web")

# Prioritize
context = {"project": "automation", "user_needs": ["performance", "security"]}
priorities = prioritize_learning(context, max_topics=5)

# Get search queries
queries = get_search_queries(context)
print(f"Search: {queries}")
```

---

### 6. Daily Intelligence (`daily_intelligence.py`)

Morning ask, evening reflect.

**Features:**
- Morning: What should I learn today?
- Evening: What did I learn?
- Insight generation
- Daily cycle management

**Usage:**
```python
from src.memory import morning_query, evening_reflection, get_today_summary

# Morning
morning = morning_query({"project": "automation"})
print(f"Suggested queries: {morning['suggested_queries']}")

# Evening
evening = evening_reflection(
    learnings=[{"topic": "AI agents", "learned": True}],
    achievements=["Fixed critical bug"],
    challenges=["Need more testing"]
)

# Today's summary
summary = get_today_summary()
```

---

### 7. Control Center (`control_center.py`)

User has FULL CONTROL.

**Control Levels:**
- **APPROVE**: User must approve every action
- **NOTIFY**: System acts, then notifies user
- **AUTONOMOUS**: System acts, learns, reports later

**Usage:**
```python
from src.memory import set_control_level, get_control_level, request_approval

# Set control level
set_control_level("NOTIFY")  # or APPROVE, AUTONOMOUS

# Check current level
print(get_control_level())

# Request approval
req_id = request_approval("deploy_to_production", {"risk": "high"})

# Approve/Deny
from src.memory import approve_request, deny_request
approve_request(req_id, "Approved for deployment")
# or
deny_request(req_id, "Too risky")
```

---

### 8. Daily Report Generator (`daily_report.py`)

Full transparency every day.

**Usage:**
```python
from src.memory import generate_daily_report, save_daily_report, get_recent_reports

# Generate report
report = generate_daily_report()
print(report)

# Save to file
path = save_daily_report()
print(f"Saved to: {path}")

# Get recent reports
recent = get_recent_reports(days=7)
```

---

### 9. Human-Like Learning Integration (`human_like_learning.py`)

Central hub that ties everything together.

**Usage:**
```python
from src.memory import get_hls, learn_from_user, analyze_and_decide, full_report

# Initialize
hls = get_hls()

# Learn from user
learn_from_user("thumbs_up", "concise_code")
learn_from_user("correction", "output", context={"original": "...", "corrected": "..."})

# Analyze and decide
decision = hls.analyze_and_decide(
    "Deploy to production?",
    ["Deploy now", "Deploy to staging", "Wait"],
    {"risk": "high"}
)

# Get full report
print(full_report())

# Set control level
hls.set_control_level("APPROVE")

# Manage approvals
pending = hls.get_pending_approvals()
hls.approve_action(pending[0]['id'], "Approved")
```

---

## Auto-Learning

The system automatically:

1. **Learns from user feedback** - Every thumbs up/down, correction is stored
2. **Discovers from web** - Runs auto-discovery periodically
3. **Prioritizes** - Decides what to learn based on context
4. **Judges** - Analyzes situations and recommends actions
5. **Reports** - Generates daily reports

### Running Auto-Learning

```python
from src.memory import get_hls, run_daily_cycle

# Run full daily cycle
hls = get_hls()
results = hls.run_daily_learning()

# Or use convenience function
results = run_daily_cycle()
```

---

## Files Structure

```
src/memory/
├── user_feedback.py         # User feedback learning
├── judgment_engine.py        # AI judgment/advice
├── audit_logger.py           # Full transparency logging
├── web_search_learner.py     # Web search integration
├── learning_prioritizer.py   # Smart prioritization
├── daily_intelligence.py     # Morning/evening cycle
├── control_center.py         # User approval system
├── daily_report.py           # Daily report generator
├── human_like_learning.py    # Central integration
├── __init__.py              # Exports all
└── [existing files...]
```

---

## Quick Start

```python
# Full system test
from src.memory import get_hls

hls = get_hls()

# 1. Learn from user
hls.learn_from_user("thumbs_up", "concise_code")

# 2. Get AI judgment
decision = hls.analyze_and_decide(
    "Deploy to production?",
    ["Deploy now", "Deploy to staging", "Wait"],
    {"risk": "high"}
)

# 3. Run daily learning
results = hls.run_daily_learning()

# 4. Get full report
print(hls.get_full_report())

# 5. Check status
print(hls.get_status())
```

---

## Control Levels

| Level | Behavior | Use Case |
|-------|----------|----------|
| **APPROVE** | User must approve every action | High-risk environments |
| **NOTIFY** | System acts, then notifies | Balanced control |
| **AUTONOMOUS** | System acts, learns, reports | Fast-paced development |

---

## Daily Report Example

```
============================================================
📊 NEXUS DAILY REPORT - 2026-02-16
============================================================

🧠 LEARNING SUMMARY
----------------------------------------
   • Knowledge discovered: 15 items
   • User feedback learned: 8 items
   • Patterns identified: 5
   • Learning topics: 12 pending
   • Knowledge gaps: 3 identified

🎯 ACTIONS TAKEN
----------------------------------------
   • Total actions: 42
   • Approved: 5 actions
   • Denied: 2 actions
   • Autonomous: 35 actions

💡 INSIGHTS
----------------------------------------
   • High learning velocity today
   • Security gap identified

⚠️ PENDING DECISIONS
----------------------------------------
   • [req_xxx] deploy_to_production

📈 SYSTEM HEALTH
----------------------------------------
   • Health score: 95/100
   • Memory entries: 1,234

============================================================
```

---

**Version:** 1.0.0
**The Dream Team - Continuous Learning System**
