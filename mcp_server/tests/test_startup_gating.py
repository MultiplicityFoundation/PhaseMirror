from __future__ import annotations

import pytest

from mcp_server.server import ServerStartupGateError, build_server


def test_build_server_enforces_startup_gate_when_enabled(monkeypatch):
    monkeypatch.setattr("mcp_server.server.verify_server_startup_gate", lambda: (False, "sentinel present"))

    with pytest.raises(ServerStartupGateError) as exc_info:
        build_server(prefer_fastmcp=False, enforce_startup_gate=True)

    assert "sentinel" in str(exc_info.value).lower()


def test_build_server_skips_gate_by_default(monkeypatch):
    monkeypatch.setattr("mcp_server.server.verify_server_startup_gate", lambda: (False, "sentinel present"))

    server = build_server(prefer_fastmcp=False)
    assert server.describe()["tool_count"] > 0
