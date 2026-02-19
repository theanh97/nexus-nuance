from pathlib import Path


def test_openclaw_open_guard_blocks_window_open_on_unhealthy_dashboard():
    # Use local project's dashboard template
    dashboard_path = Path(__file__).parent.parent.parent / "monitor" / "templates" / "dashboard.html"
    if not dashboard_path.exists():
        import pytest
        pytest.skip("dashboard.html not found in local project")
    dashboard_html = dashboard_path.read_text(encoding="utf-8")

    fn_start = dashboard_html.find("function openOpenClaw()")
    assert fn_start != -1

    fn_end = dashboard_html.find("function emergencyShutdown()", fn_start)
    assert fn_end != -1

    fn_body = dashboard_html[fn_start:fn_end]

    health_guard = "if (!ok || !data?.success)"
    guard_return = "return;"
    window_open = "window.open(url, '_blank', 'noopener');"
    debounce_guard = "if (openclawLastWindowUrl === url && (now - openclawLastWindowOpenAt) < 4000)"

    assert health_guard in fn_body
    assert window_open in fn_body
    assert debounce_guard in fn_body

    guard_pos = fn_body.find(health_guard)
    return_after_guard_pos = fn_body.find(guard_return, guard_pos)
    window_pos = fn_body.find(window_open)
    debounce_pos = fn_body.find(debounce_guard)

    assert guard_pos != -1
    assert return_after_guard_pos != -1
    assert debounce_pos != -1
    assert window_pos != -1
    assert return_after_guard_pos < window_pos
    assert debounce_pos < window_pos
