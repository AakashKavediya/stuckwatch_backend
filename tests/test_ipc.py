"""Unit tests for the IPC JSON-lines protocol and event emission."""

from __future__ import annotations

import io
import json
import sys
import pytest

from stuckwatch.ipc import IPC, PROTOCOL_VERSION


def test_event_serialization_and_stdout_purity(monkeypatch):
    """Verify emitted events are valid single-line JSON with version and timestamp."""
    captured_stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_stdout)

    ipc = IPC()
    ipc.emit_ready(version="1.0.0", modes=["camera", "screen_only"], platform_name="test_os")
    ipc.emit_status(
        state="idle",
        mode="screen_only",
        camera_active=False,
        consent=False,
        paused_until=None,
        sensitivity="medium",
    )
    ipc.emit_ack(cmd="hello", ok=True)

    output = captured_stdout.getvalue()
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    assert len(lines) == 3
    for line in lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
        assert parsed["v"] == PROTOCOL_VERSION
        assert "ts" in parsed
        assert "type" in parsed

    ready_ev = json.loads(lines[0])
    assert ready_ev["type"] == "ready"
    assert ready_ev["version"] == "1.0.0"

    status_ev = json.loads(lines[1])
    assert status_ev["type"] == "status"
    assert status_ev["state"] == "idle"

    ack_ev = json.loads(lines[2])
    assert ack_ev["type"] == "ack"
    assert ack_ev["cmd"] == "hello"
    assert ack_ev["ok"] is True


def test_malformed_json_and_unknown_commands(monkeypatch):
    """Malformed JSON should produce an ack with ok=false and not raise an exception."""
    captured_stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_stdout)

    fake_stdin = io.StringIO("not-valid-json\n{\"foo\":\"bar\"}\n")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    ipc = IPC()
    ipc.start()

    # Wait briefly for reader thread to consume lines
    import time
    time.sleep(0.1)
    ipc.stop()

    output = captured_stdout.getvalue()
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    assert len(lines) >= 2
    err1 = json.loads(lines[0])
    assert err1["type"] == "ack"
    assert err1["ok"] is False
    assert "Malformed JSON" in err1["error"]

    err2 = json.loads(lines[1])
    assert err2["type"] == "ack"
    assert err2["ok"] is False
    assert "Missing 'cmd'" in err2["error"]
