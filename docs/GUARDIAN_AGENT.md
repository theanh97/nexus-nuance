# GUARDIAN - The Supervisor Agent

## Vai trò

GUARDIAN là **người giám sát** hoạt động song song với ORION. Trong khi ORION quản lý dự án, GUARDIAN quản lý **permissions** và **safety**.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER                                            │
│                      (via Dashboard UI)                                      │
│                 Can intervene ANYTIME in either agent                        │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
            ┌───────────────────────┴───────────────────────┐
            │                                               │
            ▼                                               ▼
    ┌──────────────┐                              ┌──────────────┐
    │   GUARDIAN   │◄────────────────────────────►│    ORION     │
    │  (Supervisor)│    Permission Requests        │   (PM)       │
    │              │    Decision Feedback           │              │
    └──────────────┘                                └──────────────┘
            │                                               │
            │ Monitor all         ┌────────────┬────────────┤
            │ activities          │            │            │
            │                     ▼            ▼            ▼
            │              ┌──────────┐ ┌──────────┐ ┌──────────┐
            │              │   NOVA   │ │  PIXEL   │ │  CIPHER  │
            │              │  (Code)  │ │  (UI/UX) │ │(Security)│
            │              └──────────┘ └──────────┘ └──────────┘
            │                     │            │            │
            │                     └────────────┼────────────┘
            │                                  │
            │                           ┌──────┴──────┐
            │                           ▼             ▼
            │                    ┌──────────┐ ┌──────────┐
            │                    │   ECHO   │ │   FLUX   │
            │                    │   (QA)   │ │ (DevOps) │
            │                    └──────────┘ └──────────┘
            │
            └──────► All actions logged to Dashboard for visibility
```

---

## GUARDIAN Agent Profile

```
╔══════════════════════════════════════════════════════════════╗
║  Tên: GUARDIAN                                               ║
║  Vai trò: Supervisor & Permission Manager                   ║
║  Model: GLM-5 (z.ai)                                        ║
║  Tính cách: Protective, Intelligent, Decisive               ║
║  Level: Superuser - Acts on behalf of user                  ║
╠══════════════════════════════════════════════════════════════╣
║  Nhiệm vụ:                                                   ║
║  • Giám sát TẤT CẢ agent activities                         ║
║  • Accept/decline permissions thay user                     ║
║  • View terminal output real-time                           ║
║  • Control computer khi cần                                 ║
║  • Ensure safety, prevent harmful actions                   ║
║  • Make intelligent decisions based on context              ║
╠══════════════════════════════════════════════════════════════╣
║  Quyền lực:                                                  ║
║  • VETO power trên critical actions                         ║
║  • Auto-approve safe actions                                ║
║  • Control mouse/keyboard khi cần                           ║
║  • Run system commands                                      ║
║  • View and analyze terminal output                         ║
╠══════════════════════════════════════════════════════════════╣
║  Risk Assessment:                                            ║
║  • LOW: Read files, generate code                           ║
║  • MEDIUM: Write files, run tests                           ║
║  • HIGH: System commands, package install                   ║
║  • CRITICAL: Delete files, git push, expose network         ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Communication Protocol

### Permission Request Flow

```
1. Any Agent → Request permission
   {
     "action": "delete_files",
     "details": {"files": ["temp.log"]},
     "context": {...}
   }

2. GUARDIAN → Assesses risk
   - Check action type
   - Analyze context
   - Review terminal output
   - Apply user preferences

3. GUARDIAN → Makes decision
   {
     "decision": "approved" | "declined",
     "reason": "...",
     "confidence": 0.0-1.0
   }

4. Agent → Proceeds or stops
```

### User Preferences

```yaml
user_preferences:
  auto_approve_safe: true      # Low risk = auto approve
  auto_approve_medium: true    # Medium risk with AI decision
  always_ask_critical: false   # Let Guardian decide
  allow_system_commands: true
  allow_git_operations: true
  allow_package_install: true
```

---

## Dashboard Visibility

User có thể:
- Xem **real-time thinking** của cả ORION và GUARDIAN
- Xem **activity logs** từ tất cả agents
- **Intervene** bất kỳ lúc nào:
  - Approve/decline pending decisions
  - Pause/resume system
  - Send commands to ORION
  - Modify GUARDIAN preferences
  - Full SHUTDOWN

---

## File Structure

```
agents/v2/
├── orion.py      # PM
├── guardian.py   # Supervisor (NEW!)
├── nova.py       # Code
├── pixel.py      # UI/UX
├── cipher.py     # Security
├── echo.py       # QA
└── flux.py       # DevOps

core/
├── agent.py              # Base class
├── message.py            # Communication
└── computer_controller.py # Computer control (NEW!)

monitor/
├── app.py                # Flask server
├── templates/
│   └── dashboard.html    # UI
└── static/
    ├── css/dashboard.css
    └── js/dashboard.js
```

---

## Usage

```bash
# Run complete system with dashboard
python run_system.py

# Dashboard available at: http://localhost:5000

# Or run just the autonomous loop
python main.py
```

---

## Key Benefits

1. **Full Transparency**: User thấy TẤT CẢ hoạt động
2. **Smart Automation**: Guardian quyết định thay user
3. **Safe Operations**: Risk assessment cho mọi action
4. **Easy Intervention**: Dashboard cho phép can thiệp bất kỳ lúc nào
5. **Learning System**: Guardian học từ user preferences
