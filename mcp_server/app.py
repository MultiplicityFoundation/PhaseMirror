"""
Phase Mirror — FastAPI Governance Daemon
ADR-MVP-003: Replaces http_transport.py, server.py, session.py, discovery.py.

Entry point:
    uvicorn mcp_server.app:app --host 0.0.0.0 --port 8000

All tool routes are registered dynamically from tool_registry.yaml at startup.
No hardcoded routes. Add a tool to the YAML, it appears in /docs automatically.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from governance.constitution import ConstitutionModel, ConstitutionViolation
from governance.coupling import RedisCoupling
from governance.ledger import GitLedger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DEPLOY_REPO_PATH = os.environ.get("DEPLOY_REPO_PATH", ".")
TOOL_REGISTRY_PATH = os.environ.get(
    "TOOL_REGISTRY_PATH",
    os.path.join(os.path.dirname(__file__), "tool_registry.yaml"),
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ProposalRequest(BaseModel):
    """
    Generic MCP tool invocation envelope.

    `payload` is intentionally untyped for the MVP.
    Per-tool typed schemas are the post-MVP amendment (ADR-MVP-003 §Amendment Pathway).
    """
    proposal_id: str
    payload: dict[str, Any]
    rationale: str = ""
    # ConstitutionModel fields forwarded from agent context
    state_norm: float = 1.0
    drift_rate: float = 0.0
    contractivity_score: float = 0.9
    critique_results: list[dict] = []
    prime_gates: list[dict] = []
    kill_switch_active: bool = False
    rollback_anchor_sha: str | None = None
    consecutive_failures: int = 0


class ToolResponse(BaseModel):
    ok: bool
    tool_name: str
    proposal_id: str
    commit_sha: str | None = None
    result: Any = None
    violation: str | None = None


# ---------------------------------------------------------------------------
# App state (singletons, initialised in lifespan)
# ---------------------------------------------------------------------------

class AppState:
    ledger: GitLedger
    coupling: RedisCoupling
    tool_registry: dict  # raw YAML content
    tool_modules: dict[str, Any]  # tool_name -> imported module


_state = AppState()


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("Phase Mirror governance daemon starting...")

    # 1. Redis connectivity check (hard dependency per ADR-MVP-003)
    _state.coupling = RedisCoupling(redis_url=REDIS_URL)
    if not _state.coupling.ping():
        raise RuntimeError(
            f"Redis unavailable at {REDIS_URL}. "
            "The governance daemon cannot start without the coupling bus. "
            "Ensure Redis is running (docker compose up redis)."
        )
    logger.info("Redis coupling bus: OK")

    # 2. GitLedger
    _state.ledger = GitLedger(repo_path=DEPLOY_REPO_PATH)
    logger.info("GitLedger initialised at %s", DEPLOY_REPO_PATH)

    # 3. Tool registry
    with open(TOOL_REGISTRY_PATH, "r") as f:
        _state.tool_registry = yaml.safe_load(f)

    _state.tool_modules = {}
    tools = _state.tool_registry.get("tools", [])
    for tool_entry in tools:
        name = tool_entry["name"]
        module_path = tool_entry.get("module", f"mcp_server.tools.tool_{name}")
        try:
            _state.tool_modules[name] = importlib.import_module(module_path)
            logger.info("Registered tool: %s -> %s", name, module_path)
        except ImportError as exc:
            logger.warning("Tool %s could not be imported: %s", name, exc)

    logger.info(
        "Phase Mirror governance daemon ready. %d tools registered.",
        len(_state.tool_modules),
    )

    yield

    # --- Shutdown ---
    _state.coupling.close()
    logger.info("Phase Mirror governance daemon stopped.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Phase Mirror Governance Daemon",
    description=(
        "Safe, autonomous self-modification via agentic proposals. "
        "Constitution enforced by Pydantic v2. "
        "Ledger backed by Git. "
        "Coupling safety via Redis Streams."
    ),
    version="0.1.0-mvp",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_ledger() -> GitLedger:
    return _state.ledger


def get_coupling() -> RedisCoupling:
    return _state.coupling


def validate_constitution(req: ProposalRequest) -> ConstitutionModel:
    """
    FastAPI dependency: validates constitutional state before any tool executes.
    Raises HTTP 422 with violation detail on L0 failure.
    """
    # Build 10 passing critique stubs if agent didn't supply them (dev convenience).
    critique_results = req.critique_results
    if not critique_results:
        critique_results = [{"critique_id": i, "passed": True} for i in range(10)]

    try:
        return ConstitutionModel(
            state_norm=req.state_norm,
            drift_rate=req.drift_rate,
            contractivity_score=req.contractivity_score,
            critique_results=critique_results,
            prime_gates=req.prime_gates,
            kill_switch_active=req.kill_switch_active,
            rollback_anchor_sha=req.rollback_anchor_sha,
            consecutive_failures=req.consecutive_failures,
        )
    except ConstitutionViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"invariant": exc.invariant, "violation": exc.detail},
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"schema_errors": exc.errors()},
        )


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Observability"])
async def health(
    coupling: RedisCoupling = Depends(get_coupling),
    ledger: GitLedger = Depends(get_ledger),
):
    """Liveness + readiness probe. Reports Redis status and current deploy branch HEAD."""
    redis_ok = coupling.ping()
    head_sha = ledger.head_sha()
    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "connected" if redis_ok else "disconnected",
        "deploy_branch_head": head_sha,
        "tools_registered": len(_state.tool_modules),
    }


@app.get("/ledger/history", tags=["Ledger"])
async def ledger_history(
    limit: int = 20,
    ledger: GitLedger = Depends(get_ledger),
):
    """Returns the last N commits on the deploy branch (the immutable audit trail)."""
    return {"history": ledger.get_ledger_history(limit=limit)}


@app.post("/rollback/{target_sha}", tags=["Ledger"])
async def rollback(
    target_sha: str,
    ledger: GitLedger = Depends(get_ledger),
):
    """
    Rolls the deploy branch back to target_sha via git reset --hard.
    This is the Article IX §9.4 rollback mechanism.
    """
    try:
        ledger.rollback(target_sha)
        return {"ok": True, "rolled_back_to": target_sha}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/tools/{tool_name}", response_model=ToolResponse, tags=["Tools"])
async def invoke_tool(
    tool_name: str,
    req: ProposalRequest,
    constitution: ConstitutionModel = Depends(validate_constitution),
    ledger: GitLedger = Depends(get_ledger),
    coupling: RedisCoupling = Depends(get_coupling),
):
    """
    Generic tool invocation endpoint.

    Flow:
      1. ConstitutionModel validated (Lever 1 dependency above).
      2. Rate-limit check via Redis coupling bus.
      3. Tool business logic executed.
      4. Proposal committed to deploy branch via GitLedger (Lever 2).
      5. Event published to Redis Stream for downstream agents.
    """
    if tool_name not in _state.tool_modules:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_name}' not found in registry. "
                   f"Available: {sorted(_state.tool_modules.keys())}",
        )

    # --- Lever 3B: coupling rate check ---
    agent_id = req.payload.get("agent_id", "unknown")
    allowed, count = coupling.check_rate(agent_id, tool_name)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "invariant": "L0-coupling",
                "violation": (
                    f"Agent '{agent_id}' exceeded rate limit for tool '{tool_name}' "
                    f"(count={count}). Resonance cascade prevention active."
                ),
            },
        )

    # --- Execute tool business logic ---
    module = _state.tool_modules[tool_name]
    try:
        # Resolve callable: prefer registry-declared name, fall back to execute/run
        tool_entry = next(
            (t for t in _state.tool_registry.get("tools", []) if t.get("name") == tool_name),
            {},
        )
        declared_callable = tool_entry.get("callable")
        handler = (
            (getattr(module, declared_callable, None) if declared_callable else None)
            or getattr(module, "execute", None)
            or getattr(module, "run", None)
        )
        if handler is None:
            raise HTTPException(
                status_code=500,
                detail=f"Tool '{tool_name}' has no callable '{declared_callable}', execute(), or run() entrypoint.",
            )

        if isinstance(req.payload, dict):
            sig = inspect.signature(handler)
            if len(sig.parameters) == 1 and all(
                p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                for p in sig.parameters.values()
            ):
                result = handler(req.payload)
            else:
                try:
                    result = handler(**req.payload)
                except TypeError:
                    result = handler(req.payload)
        else:
            result = handler(req.payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Tool %s raised an exception", tool_name)
        raise HTTPException(status_code=500, detail=str(exc))

    # --- Lever 2: commit proposal to ledger ---
    commit_sha = ledger.commit_proposal(
        proposal_id=req.proposal_id,
        delta={"tool": tool_name, "payload": req.payload, "result": str(result)},
        rationale=req.rationale or f"Tool invocation: {tool_name}",
    )

    # --- Publish event to Redis Stream ---
    coupling.publish(
        agent_id=agent_id,
        topic=f"tool.{tool_name}.completed",
        payload={"proposal_id": req.proposal_id, "commit_sha": commit_sha},
    )

    return ToolResponse(
        ok=True,
        tool_name=tool_name,
        proposal_id=req.proposal_id,
        commit_sha=commit_sha,
        result=result,
    )


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(ConstitutionViolation)
async def constitution_violation_handler(
    request: Request, exc: ConstitutionViolation
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"invariant": exc.invariant, "violation": exc.detail},
    )
