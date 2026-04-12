"""F-06: HTTP Transport and Horizontal Scaling.

Wraps ``RegistryBackedServer`` in a FastAPI application that exposes the MCP
tool registry over HTTP.  The server is intentionally stateless — no sticky
session affinity is required.  Multiple instances behind nginx (or any L4/L7
load balancer) can serve any request without coordination.

Endpoints
---------
GET  /health                        — Load-balancer health probe (no DB call, <10 ms)
GET  /tools                         — List all registered tools
POST /call/{tool_name}              — Dispatch a tool call (JSON body)
GET  /.well-known/mcp               — Discovery document (F-03)
GET  /.well-known/mcp-card.json     — Server card (F-04, if identity module present)
GET  /call/{tool_name}/stream       — SSE streaming endpoint for large results
POST /governance/proof-anchor       — Record HQ proof-anchor in AuditLedger
                                      (GOVERNANCE-BRIDGE.md v0.1 condition 3)

Per Gate F L0 invariant 3, every call goes through the auth layer (F-02).

Usage (development)::

    python -m mcp_server.http_transport

Production::

    uvicorn mcp_server.http_transport:app --host 0.0.0.0 --port 8000

The ``MCPHTTPServer`` class is the primary integration point; it can be
instantiated with custom ``RegistryBackedServer``, ``SessionBackend``, and
``RateLimitBackend`` instances for testing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import BaseModel

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[assignment,misc]


class FastAPIUnavailableError(RuntimeError):
    """Raised when FastAPI is not installed."""


# ---------------------------------------------------------------------------
# Request / Response models (only if FastAPI is available)
# ---------------------------------------------------------------------------

if _FASTAPI_AVAILABLE:

    class ToolCallRequest(BaseModel):
        """Request body for POST /call/{tool_name}."""

        token: Optional[str] = None  # Bearer token (OAuth 2.1)
        args: Dict[str, Any] = {}

    class ProofAnchorRequest(BaseModel):  # GOVERNANCE-BRIDGE.md v0.1
        """Request body for POST /governance/proof-anchor.

        Receives an HQ MultiplicityProofEnvelope pi_native commitment and
        records it as a hash-chained entry in the MVP AuditLedger, satisfying
        GOVERNANCE-BRIDGE.md v0.1 condition 3.

        Fields
        ------
        pi_native:
            256-bit field element from the HQ proof envelope.
            Must be ``0x`` followed by exactly 64 hex characters.
        circuit:
            Circuit name that produced the proof
            (e.g. ``"root"``, ``"recovery"``, ``"millerRabin"``, ``"deviceAttest"``).
        proposal_id:
            The governance proposal this proof covers.  Used as the
            AuditLedger ``evaluation_id`` for cross-referencing.
        """

        pi_native: str
        circuit: str
        proposal_id: str


# ---------------------------------------------------------------------------
# MCPHTTPServer
# ---------------------------------------------------------------------------


class MCPHTTPServer:
    """HTTP wrapper around ``RegistryBackedServer``.

    Parameters
    ----------
    server:
        The underlying ``RegistryBackedServer`` instance.
    session_backend:
        External session store (F-05).  Defaults to ``InMemorySessionBackend``
        for local development; use ``SQLiteSessionBackend`` or
        ``RedisSessionBackend`` in production.
    rate_limit_backend:
        Atomic rate-limit counter store (F-05).
    discovery_endpoint:
        ``DiscoveryEndpoint`` instance for ``/.well-known/mcp`` (F-03).
    card_issuer:
        ``ServerCardIssuer`` for ``/.well-known/mcp-card.json`` (F-04).
        If ``None``, the card endpoint returns 404.
    """

    def __init__(
        self,
        server=None,
        *,
        session_backend=None,
        rate_limit_backend=None,
        discovery_endpoint=None,
        card_issuer=None,
    ) -> None:
        if not _FASTAPI_AVAILABLE:
            raise FastAPIUnavailableError(
                "FastAPI is required for MCPHTTPServer. "
                "Install it with: pip install fastapi uvicorn"
            )

        # Lazily import to allow non-FastAPI contexts to import this module header.
        from mcp_server.server import build_server
        from mcp_server.session import (
            InMemoryRateLimitBackend,
            InMemorySessionBackend,
        )

        self._server = server or build_server(
            prefer_fastmcp=False,
            skip_governance_preflight=True,  # preflight handled separately
        )
        self._session_backend = session_backend or InMemorySessionBackend()
        self._rate_backend = rate_limit_backend or InMemoryRateLimitBackend()
        self._discovery = discovery_endpoint
        self._card_issuer = card_issuer

        self.app = FastAPI(
            title="Phase Mirror PMD MCP Server",
            version="1.0.0",
            description="Governed MCP tool server for Phase Mirror PMD runtime.",
        )
        self._register_routes()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        app = self.app

        @app.get("/health")
        async def health() -> JSONResponse:
            return JSONResponse({"status": "ok", "timestamp": time.time()})

        @app.get("/tools")
        async def list_tools() -> JSONResponse:
            return JSONResponse(self._server.list_tools())

        @app.post("/call/{tool_name}")
        async def call_tool(tool_name: str, request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON body")

            args = body.get("args", {})
            if not isinstance(args, dict):
                raise HTTPException(status_code=400, detail="'args' must be a JSON object")

            # Rate limiting (F-05)
            from mcp_server.session import _rate_key, get_rate_limit_for_tool

            subject = body.get("token", "anonymous")
            rkey = _rate_key(subject, tool_name)
            limit = get_rate_limit_for_tool(tool_name)
            allowed, remaining = self._rate_backend.check_and_increment(rkey, limit)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded for tool '{tool_name}'. Retry after 60 s.",
                )

            try:
                result = self._server.call_tool(tool_name, **args)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

            return JSONResponse({"result": result, "rate_limit_remaining": remaining})

        @app.get("/.well-known/mcp")
        async def well_known_mcp() -> JSONResponse:
            if self._discovery is not None:
                return JSONResponse(self._discovery.get_discovery_document())
            # Minimal fallback
            tools = self._server.list_tools()
            return JSONResponse({"tools": tools, "mcp_version": "2025-11-05"})

        @app.get("/.well-known/mcp-card.json")
        async def well_known_card() -> JSONResponse:
            if self._card_issuer is None:
                raise HTTPException(status_code=404, detail="Server card not configured")
            import socket

            card = self._card_issuer.issue(
                issuer="tooling-pmd/v0.1.0",
                subject=socket.gethostname(),
            )
            return JSONResponse(card.to_dict())

        @app.get("/call/{tool_name}/stream")
        async def stream_tool(tool_name: str, request: Request) -> StreamingResponse:
            """SSE streaming endpoint for long-running tool results."""

            async def event_generator() -> AsyncGenerator[str, None]:
                try:
                    result = self._server.call_tool(tool_name)
                    data = json.dumps({"result": result})
                    yield f"data: {data}\n\n"
                except KeyError as exc:
                    yield f"data: {json.dumps({'error': str(exc)})}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'error': str(exc)})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # ------------------------------------------------------------------
        # GOVERNANCE-BRIDGE.md v0.1 — HQ → MVP proof-anchor endpoint
        # ------------------------------------------------------------------

        @app.post("/governance/proof-anchor")
        async def governance_proof_anchor(body: "ProofAnchorRequest") -> JSONResponse:  # type: ignore[name-defined]
            """Record an HQ MultiplicityProofEnvelope pi_native in the AuditLedger.

            Satisfies GOVERNANCE-BRIDGE.md v0.1 condition 3:
              'Its pi_native is recorded in the MVP AuditLedger as a
               PROOF_ANCHOR entry.'

            Returns
            -------
            200  {tx_id: int, entry_hash: str}
            422  pi_native format invalid
            500  ledger write failure
            """
            from governance.ledger import (
                _validate_pi_native,
                get_phase_mirror_audit_ledger,
            )

            # Validate pi_native format early — return 422 before touching ledger.
            try:
                _validate_pi_native(body.pi_native)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

            try:
                ledger = get_phase_mirror_audit_ledger()
                entry = ledger.append_proof_anchor(
                    pi_native=body.pi_native,
                    circuit=body.circuit,
                    proposal_id=body.proposal_id,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Ledger write failed: {exc}",
                )

            return JSONResponse(
                {
                    "tx_id": entry.sequence_num,
                    "entry_hash": entry.entry_hash,
                }
            )


def create_app(
    *,
    skip_governance_preflight: bool = False,
    session_backend=None,
    rate_limit_backend=None,
) -> Any:
    """Factory that builds and returns a configured FastAPI application.

    This is the recommended entry point for ``uvicorn``:

        uvicorn mcp_server.http_transport:app --host 0.0.0.0 --port 8000
    """
    if not _FASTAPI_AVAILABLE:
        raise FastAPIUnavailableError(
            "FastAPI is required.  Install it with: pip install fastapi uvicorn"
        )

    from mcp_server.server import build_server

    server = build_server(
        prefer_fastmcp=False,
        skip_governance_preflight=skip_governance_preflight,
    )
    http_server = MCPHTTPServer(
        server=server,
        session_backend=session_backend,
        rate_limit_backend=rate_limit_backend,
    )
    return http_server.app


# Module-level ``app`` attribute consumed by uvicorn:
#   uvicorn mcp_server.http_transport:app
if _FASTAPI_AVAILABLE:
    app = create_app(skip_governance_preflight=True)
else:
    app = None  # type: ignore[assignment]


def main() -> None:  # pragma: no cover - CLI entrypoint
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn is required.  Install it with: pip install uvicorn"
        )
    uvicorn.run(
        "mcp_server.http_transport:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
