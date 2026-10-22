"""Tests for the PC-side hook that feeds the Claude light. Run with:  pytest dev"""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("claude_status_hook", Path(__file__).resolve().parent.parent / "pc" / "claude_status_hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def event(name, **fields):
    return {"hook_event_name": name, "session_id": "abc-123", "cwd": "C:/Users/Pouyan/Desktop/PiClock", **fields}


@pytest.mark.parametrize("ev, expected", [
    (event("SessionStart", source="startup"), {"state": "idle", "task": ""}),
    (event("UserPromptSubmit", prompt="  \nAdd a settings page\nwith encryption"), {"state": "working", "task": "Add a settings page"}),
    (event("PreToolUse", tool_name="Bash"), {"state": "working", "heartbeat": True}),
    (event("PostToolUse", tool_name="Bash"), {"state": "working", "heartbeat": True}),
    (event("PostToolUseFailure", tool_name="Bash", error="boom"), {"state": "working", "heartbeat": True}),
    (event("PermissionRequest", tool_name="Bash"), {"state": "attention"}),
    (event("Notification", notification_type="permission_prompt", message="Claude needs your permission"), {"state": "attention"}),
    (event("Notification", notification_type="idle_prompt", message="Waiting for input"), {"state": "done"}),
    (event("Notification", notification_type="quota_auto_resume_fired", message="Resumed"), {"state": "working"}),
    (event("Notification", notification_type="quota_auto_resume_stale", message="Limit reset while asleep"), {"state": "limit", "task": "Limit reset while asleep"}),
    (event("Notification", notification_type="auth_success"), None),
    (event("Stop", stop_hook_active=False), {"state": "done"}),
    (event("StopFailure", error="rate_limit", error_details="429", last_assistant_message="API Error: Rate limit reached. Resets at 3pm"),
     {"state": "limit", "task": "API Error: Rate limit reached. Resets at 3pm"}),
    (event("StopFailure", error="server_error", error_details="Internal server error, could not generate a response"),
     {"state": "done", "task": "Internal server error, could not generate a response"}),
    (event("StopFailure", error="invalid_request", error_details="You've hit your usage limit until 12:20am"),
     {"state": "limit", "task": "You've hit your usage limit until 12:20am"}),
    (event("StopFailure", error="authentication_failed"), {"state": "done", "task": "authentication failed"}),
    (event("SessionStart", source="compact"), None),
    (event("SessionEnd", reason="other"), {"state": "ended"}),
    (event("PreCompact", trigger="auto"), None),
    (event("PostToolUse", tool_name="Bash", agent_id="sub-1", agent_type="Explore"), None),
])
def test_state_for(ev, expected):
    assert hook.state_for(ev) == expected


def test_first_line_is_trimmed():
    assert hook.first_line("x" * 300).endswith("…") and len(hook.first_line("x" * 300)) == 120
    assert hook.first_line(None) == ""


def test_build_report_rate_limits_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    r = hook.build_report(event("UserPromptSubmit", prompt="Do a thing"), now=1000, pc="PC")
    assert r == {"state": "working", "task": "Do a thing", "session_id": "abc-123", "seq": 1000, "project": "PiClock", "pc": "PC"}
    assert hook.build_report(event("PostToolUse"), now=1030, pc="PC") is None          # too soon after the prompt
    beat = hook.build_report(event("PostToolUse"), now=1070, pc="PC")
    assert beat["state"] == "working" and "task" not in beat                          # heartbeats carry no task
    assert hook.build_report(event("PostToolUse"), now=1100, pc="PC") is None
    assert hook.build_report(event("Stop"), now=1101, pc="PC")["state"] == "done"      # real events always go
    assert hook.build_report(event("SessionEnd", reason="other"), now=1200, pc="PC")["state"] == "ended"
    assert not list((tmp_path / "beats").iterdir())                                   # the stamp is gone with the session


def test_build_report_ignores_events_without_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    assert hook.build_report({"hook_event_name": "Stop"}, now=1, pc="PC") is None
    assert hook.build_report(event("Notification", notification_type="auth_success"), now=1, pc="PC") is None
