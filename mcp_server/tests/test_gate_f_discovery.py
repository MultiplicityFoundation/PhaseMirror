"""Gate F — F-03: .well-known Tool Discovery Endpoint Tests.

Tests for:
- DiscoveryEndpoint.get_discovery_document() structure
- Tool list completeness and governance_critical field
- Caching (TTL, invalidation)
- get_tool_metadata() single-tool lookup
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.discovery import (
    AuthConfig,
    DiscoveryEndpoint,
    GovernanceState,
    MCP_SPEC_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _endpoint(*, ttl: int = 0) -> DiscoveryEndpoint:
    """Return a DiscoveryEndpoint with caching disabled (ttl=0) by default."""
    return DiscoveryEndpoint(cache_ttl_seconds=ttl)


# ---------------------------------------------------------------------------
# Discovery document structure
# ---------------------------------------------------------------------------


def test_discovery_document_has_required_top_level_keys():
    doc = _endpoint().get_discovery_document()
    for key in ("mcp_version", "server_name", "governance_version", "tools", "auth"):
        assert key in doc, f"Missing key: {key}"


def test_discovery_document_mcp_version_matches_spec():
    doc = _endpoint().get_discovery_document()
    assert doc["mcp_version"] == MCP_SPEC_VERSION


def test_discovery_document_tools_is_list():
    doc = _endpoint().get_discovery_document()
    assert isinstance(doc["tools"], list)


def test_discovery_document_tools_not_empty():
    """Registry has at least one tool."""
    doc = _endpoint().get_discovery_document()
    assert len(doc["tools"]) > 0


def test_discovery_document_each_tool_has_required_fields():
    doc = _endpoint().get_discovery_document()
    required = {"name", "description", "required_scope", "governance_critical"}
    for tool in doc["tools"]:
        missing = required - tool.keys()
        assert not missing, f"Tool '{tool.get('name')}' missing fields: {missing}"


def test_discovery_document_governance_critical_is_bool():
    doc = _endpoint().get_discovery_document()
    for tool in doc["tools"]:
        assert isinstance(tool["governance_critical"], bool), (
            f"governance_critical for '{tool['name']}' is not bool"
        )


def test_discovery_document_required_scope_is_pmd_namespace():
    doc = _endpoint().get_discovery_document()
    for tool in doc["tools"]:
        assert tool["required_scope"].startswith("pmd:"), (
            f"Tool '{tool['name']}' has non-pmd scope: '{tool['required_scope']}'"
        )


def test_discovery_document_auth_block_present():
    doc = _endpoint().get_discovery_document()
    auth = doc["auth"]
    assert auth["type"] == "oauth2.1"
    assert auth["pkce_required"] is True
    assert "scopes_supported" in auth


def test_discovery_document_governance_state_present():
    ep = DiscoveryEndpoint(
        governance_state=GovernanceState(
            bootstrap_complete=True,
            merkle_root_tx_id=5,
            legitimacy=True,
        ),
        cache_ttl_seconds=0,
    )
    doc = ep.get_discovery_document()
    assert "governance_state" in doc
    assert doc["governance_state"]["bootstrap_complete"] is True
    assert doc["governance_state"]["merkle_root_tx_id"] == 5


# ---------------------------------------------------------------------------
# get_tool_metadata
# ---------------------------------------------------------------------------


def test_get_tool_metadata_returns_dict_for_known_tool():
    ep = _endpoint()
    meta = ep.get_tool_metadata("health_check")
    assert meta is not None
    assert meta["name"] == "health_check"


def test_get_tool_metadata_returns_none_for_unknown_tool():
    ep = _endpoint()
    assert ep.get_tool_metadata("nonexistent_tool_xyz") is None


def test_get_tool_metadata_includes_governance_critical():
    ep = _endpoint()
    meta = ep.get_tool_metadata("health_check")
    assert meta is not None
    assert "governance_critical" in meta


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_caching_disabled_when_ttl_zero():
    """With ttl=0, every call rebuilds the document."""
    ep = DiscoveryEndpoint(cache_ttl_seconds=0)
    doc1 = ep.get_discovery_document()
    doc2 = ep.get_discovery_document()
    # Both must be equal in content (but different objects)
    assert doc1 == doc2
    assert doc1 is not doc2  # different object — rebuilt


def test_caching_enabled_returns_same_object():
    """With ttl>0, the same dict object is returned on cache hit."""
    ep = DiscoveryEndpoint(cache_ttl_seconds=60)
    doc1 = ep.get_discovery_document()
    doc2 = ep.get_discovery_document()
    assert doc1 is doc2


def test_invalidate_cache_forces_rebuild():
    ep = DiscoveryEndpoint(cache_ttl_seconds=60)
    doc1 = ep.get_discovery_document()
    ep.invalidate_cache()
    doc2 = ep.get_discovery_document()
    assert doc1 is not doc2  # new object after invalidation
    assert doc1 == doc2       # but equal content
