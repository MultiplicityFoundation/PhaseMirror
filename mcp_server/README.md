# MCP Server

## PMD Zone Classification (ADR-021, ADR-022)

**Zone**: 2 — Daemon Runtime  
**Role**: Machine-to-machine execution surface; tool registration and dispatch  

### Scope

**In scope**:
- MCP server bootstrap and initialization
- Tool registration, discovery, and versioning contract
- Tool transport and middleware
- Request/response dispatching and error handling
- MCP-callable tool implementations (see `tools/`)

**Out of scope**:
- Operator command-line interfaces (use `scripts/` instead)
- Daemon orchestration logic (use `daemon/` instead)
- State management (use `state/` instead)

### Consumers

- `daemon/` — calls MCP tools for autonomous actions
- Tooling repo external integrations — call MCP tools directly
- Human operators (indirectly via scripts that wrap MCP tools)

### Dependencies

- `policy/phase_mirror/` — policy gate checks
- `mcp/manifest/` — tool registry metadata

---

This directory is the canonical PMD nervous-system root defined by `ADR-022` and `ADR-025`.

Wave 1 establishes three concrete artifacts:

- `server.py` as the registry-backed server bootstrap
- `tool_registry.yaml` as the first machine-readable tool contract surface
- `tools/` as the initial implementation home for MCP-callable tools

Current scope is intentionally small. The server registers two baseline tools first:

- `health_check`
- `manifest_status`

These stubs are meant to prove the directory, registry, and dispatch shape before more governance-critical tools are added.