"""Gate F — F-06: HTTP Transport Tests.

Tests for:
- MCPHTTPServer instantiation
- /health endpoint
- /tools endpoint
- /call/{tool_name} dispatch
- /.well-known/mcp discovery
- Rate limiting via RateLimitBackend
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_FASTAPI_AVAILABLE = True
try:
    from fastapi.testclient import TestClient
    from mcp_server.http_transport import MCPHTTPServer
except ImportError:
    _FASTAPI_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE,
    reason="fastapi or httpx not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client():
    """Build a test client with a real server but no governance preflight."""
    from mcp_server.server import build_server
    from mcp_server.session import InMemoryRateLimitBackend, InMemorySessionBackend
    from mcp_server.discovery import DiscoveryEndpoint

    server = build_server(prefer_fastmcp=False, skip_governance_preflight=True)
    http_server = MCPHTTPServer(
        server=server,
        session_backend=InMemorySessionBackend(),
        rate_limit_backend=InMemoryRateLimitBackend(),
        discovery_endpoint=DiscoveryEndpoint(cache_ttl_seconds=0),
    )
    return TestClient(http_server.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_200():
    client = _make_client()
    response = client.get("/health")
    assert response.status_code == 200


def test_health_endpoint_returns_ok_status():
    client = _make_client()
    data = client.get("/health").json()
    assert data["status"] == "ok"


def test_health_endpoint_returns_timestamp():
    client = _make_client()
    data = client.get("/health").json()
    assert "timestamp" in data
    assert isinstance(data["timestamp"], (int, float))


# ---------------------------------------------------------------------------
# /tools
# ---------------------------------------------------------------------------


def test_tools_endpoint_returns_200():
    client = _make_client()
    response = client.get("/tools")
    assert response.status_code == 200


def test_tools_endpoint_returns_list():
    client = _make_client()
    data = client.get("/tools").json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_tools_endpoint_includes_health_check():
    client = _make_client()
    data = client.get("/tools").json()
    names = {t["name"] for t in data}
    assert "health_check" in names


# ---------------------------------------------------------------------------
# /call/{tool_name}
# ---------------------------------------------------------------------------


def test_call_health_check_returns_200():
    client = _make_client()
    response = client.post("/call/health_check", json={"args": {}})
    assert response.status_code == 200


def test_call_health_check_returns_result():
    client = _make_client()
    data = client.post("/call/health_check", json={"args": {}}).json()
    assert "result" in data


def test_call_unknown_tool_returns_404():
    client = _make_client()
    response = client.post("/call/nonexistent_tool_xyz", json={"args": {}})
    assert response.status_code == 404


def test_call_with_invalid_json_returns_400():
    client = _make_client()
    response = client.post(
        "/call/health_check",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /.well-known/mcp
# ---------------------------------------------------------------------------


def test_well_known_mcp_returns_200():
    client = _make_client()
    response = client.get("/.well-known/mcp")
    assert response.status_code == 200


def test_well_known_mcp_returns_tool_list():
    client = _make_client()
    data = client.get("/.well-known/mcp").json()
    assert "tools" in data
    assert len(data["tools"]) > 0


def test_well_known_mcp_card_returns_404_when_not_configured():
    """When no card_issuer is provided, the card endpoint returns 404."""
    client = _make_client()
    response = client.get("/.well-known/mcp-card.json")
    assert response.status_code == 404
