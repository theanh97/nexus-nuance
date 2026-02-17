# Nexus Progress Snapshot

Last updated: 2026-02-17T23:43:11.781981

## Project
- name: Nexus
- status: Ready
- current_iteration: 0
- score: 0
- updated: None

## Orion
- instance: orion-1 (Orion 1)
- running: True
- paused: False
- pause_reason: 
- iteration: 4
- active_flows: 3
- last_progress_at: 2026-02-17T23:41:21.251822
- last_cycle_started_at: 2026-02-17T23:41:21.276698
- last_cycle_finished_at: 2026-02-17T23:41:07.226186

## Monitor
- autopilot_enabled: True
- thread_alive: True
- interval_sec: 10
- stuck_threshold_sec: 90
- cooldown_sec: 35
- last_reason: {'orion-1': 'no_progress:90s'}
- last_recovery: {'orion-1': '2026-02-17T23:42:51.995165'}

## Computer Control
- available: True
- enabled: True
- provider: pyautogui

## Signals (last window)
- counts: {'error': 5, 'warning': 3, 'success': 14, 'info': 98}
- top_agent: orion
- latest_event: Monitor supervisor -> orion-1 recovery (no_progress:90s) ok

## Pending
- pending_decisions: 0
