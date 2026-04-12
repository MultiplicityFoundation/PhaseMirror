"""F-03: MCP Tool Discovery Endpoint.

Publishes `/.well-known/mcp` discovery document derived from `tool_registry.yaml`
and the current governance state.  Responses are cached for a configurable TTL
(default 60 s) to amortise registry reads.

Per Gate F ADR F-03 and ADR-025, every tool entry in the discovery document must
include a `governance_critical` flag so that clients and orchestrators can
distinguish autonomous-callable tools from human-gated operations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp_server._yaml import load_yaml_file

MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = MODULE_ROOT / "tool_registry.yaml"

# MCP spec version targeted by this implementation
MCP_SPEC_VERSION = "2025-11-05"

# Default cache TTL in seconds (F-03 requirement: 60 s)
DEFAULT_CACHE_TTL_SECONDS = 60


@dataclass
class ToolDiscoveryEntry:
    """Single tool entry in the discovery document."""

    name: str
    description: str
    required_scope: str
    governance_critical: bool
    inputs: List[str]
    output: str


@dataclass
class GovernanceState:
    """Runtime governance posture included in every discovery document."""

    bootstrap_complete: bool
    merkle_root_tx_id: int
    legitimacy: bool
    p_bad: bool = False


@dataclass
class AuthConfig:
    """OAuth 2.1 auth configuration block in the discovery document."""

    auth_type: str = "oauth2.1"
    pkce_required: bool = True
    scopes_supported: List[str] = field(default_factory=lambda: [
        "pmd:read",
        "pmd:phase_mirror",
        "pmd:dispatch",
        "pmd:write",
        "pmd:admin",
    ])
    authorization_endpoint: Optional[str] = None
    token_endpoint: Optional[str] = None


class DiscoveryEndpoint:
    """Builds and caches the `/.well-known/mcp` discovery document.

    Usage::

        endpoint = DiscoveryEndpoint()
        doc = endpoint.get_discovery_document()

    The document is regenerated from `tool_registry.yaml` at most once per
    ``cache_ttl_seconds``.  Pass ``cache_ttl_seconds=0`` to disable caching
    (useful in tests).
    """

    def __init__(
        self,
        *,
        registry_path: Path | None = None,
        governance_state: GovernanceState | None = None,
        auth_config: AuthConfig | None = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self.governance_state = governance_state or GovernanceState(
            bootstrap_complete=False,
            merkle_root_tx_id=0,
            legitimacy=False,
        )
        self.auth_config = auth_config or AuthConfig()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_discovery_document(self) -> Dict[str, Any]:
        """Return the full `/.well-known/mcp` discovery document.

        The response is cached for ``cache_ttl_seconds``.  The cache is
        invalidated when it expires or when :meth:`invalidate_cache` is called.
        """
        if self._is_cache_valid():
            return self._cache  # type: ignore[return-value]
        doc = self._build_document()
        self._store_cache(doc)
        return doc

    def get_tool_metadata(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Return metadata for a single tool, or None if not found."""
        doc = self.get_discovery_document()
        for tool in doc.get("tools", []):
            if tool["name"] == tool_name:
                return tool
        return None

    def invalidate_cache(self) -> None:
        """Force the next call to :meth:`get_discovery_document` to rebuild."""
        self._cache = None
        self._cache_expires_at = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_cache_valid(self) -> bool:
        if self._cache is None:
            return False
        if self.cache_ttl_seconds == 0:
            return False
        return time.monotonic() < self._cache_expires_at

    def _store_cache(self, doc: Dict[str, Any]) -> None:
        self._cache = doc
        if self.cache_ttl_seconds > 0:
            self._cache_expires_at = time.monotonic() + self.cache_ttl_seconds

    def _build_document(self) -> Dict[str, Any]:
        registry = load_yaml_file(self.registry_path)
        if not isinstance(registry, dict):
            registry = {}

        tools = self._build_tool_list(registry)
        gov = self.governance_state
        auth = self.auth_config

        doc: Dict[str, Any] = {
            "mcp_version": MCP_SPEC_VERSION,
            "server_name": registry.get("server_name", "tooling-pmd"),
            "governance_version": registry.get("governance_version", "v0.1.0"),
            "governance_state": {
                "bootstrap_complete": gov.bootstrap_complete,
                "merkle_root_tx_id": gov.merkle_root_tx_id,
                "legitimacy": gov.legitimacy,
                "P_bad": gov.p_bad,
            },
            "tools": tools,
            "auth": {
                "type": auth.auth_type,
                "pkce_required": auth.pkce_required,
                "scopes_supported": list(auth.scopes_supported),
            },
        }

        # Add optional OAuth endpoints if configured
        if auth.authorization_endpoint:
            doc["auth"]["authorization_endpoint"] = auth.authorization_endpoint
        if auth.token_endpoint:
            doc["auth"]["token_endpoint"] = auth.token_endpoint

        return doc

    def _build_tool_list(self, registry: Dict[str, Any]) -> List[Dict[str, Any]]:
        from mcp_server.middleware.auth import REQUIRED_SCOPES_BY_TOOL

        tools: List[Dict[str, Any]] = []
        for raw in registry.get("tools", []):
            if not isinstance(raw, dict):
                continue
            name = raw.get("name", "")
            required_scopes = REQUIRED_SCOPES_BY_TOOL.get(name, frozenset({"pmd:read"}))
            # Pick one representative scope string for the discovery document.
            # pmd:admin is the sentinel; use the first specific scope otherwise.
            scope_str = sorted(required_scopes)[0] if required_scopes else "pmd:read"

            tools.append({
                "name": name,
                "description": raw.get("description", ""),
                "required_scope": scope_str,
                "governance_critical": bool(raw.get("critical", False)),
                "inputs": list(raw.get("inputs", [])),
                "output": raw.get("output", ""),
                "verify_integrity": bool(raw.get("verify_integrity", False)),
            })
        return tools
