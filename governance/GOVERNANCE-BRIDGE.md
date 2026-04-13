# Governance Bridge Contract v0.1

**Status:** Accepted  
**Date:** 2026-04-12  
**Deciders:** Multiplicity Foundation  

## Purpose

This document defines the boundary between the two Phase Mirror governance
layers and the minimum conditions for a proposal to be considered **FULLY
LAWFUL**. It is the canonical reference for both repos.

- **MVP** (`MultiplicityFoundation/Phase-Mirror`) — behavioral governance daemon
- **HQ** (`MultiplicityFoundation/PhaseMirror-HQ`) — cryptographic proof engine

---

## Layer Ownership

| Invariant Class | Enforced By | Primary File |
|---|---|---|
| Behavioral (drift, critique, contractivity) | MVP constitution | `governance/constitution.py` L0-1..8 |
| Cryptographic (proof sovereignty, BLS key isolation) | HQ proof engine | `multiplicity/crypto/src/index.ts` |
| Ledger integrity (hash-chained audit trail) | HQ AuditLedger | `governance/ledger.py` |
| Tool invocation governance | HQ tool registry | `mcp_server/tool_registry.yaml` |
| On-chain submission gate | HQ proof-manager | `packages/lambda/apps/relay/` |

---

## FULLY LAWFUL Definition (v0.1)

A proposal is **FULLY LAWFUL** only when ALL of the following hold:

1. `ConstitutionModel(**state)` raises no `ConstitutionViolation` (L0-1..7 pass)
2. An HQ `MultiplicityProofEnvelope` exists for the same `proposal_id`
3. Its `pi_native` is recorded in the `AuditLedger` as a `PROOF_ANCHOR` entry

Conditions 2 and 3 enforcement status by version:

| Version | Condition 2+3 enforcement |
|---|---|
| v0.1 | Not enforced in code — documented intent only |
| v0.2 | L0-8 warn-only: absent anchor → audit warning; malformed → `ConstitutionViolation` — **implemented** (`constitution.py` `l0_8_proof_anchor`, `mvp/tests/test_constitution.py` `TestL0_8_ProofAnchor`) |
| v1.0 | L0-8 hard gate: absent anchor → `ConstitutionViolation` — deferred until ADR-007 resolved |

Tracked in:
- L0-8 validator: `governance/ADR-008-proof-anchor-validator.md` (Option C decided, v0.2 ✅)
- Bridge endpoint: `POST /governance/proof-anchor` (implemented in v0.1)
- Shared utility: `governance/proof_anchor.py` (`PI_NATIVE_PATTERN`, `validate_pi_native()`)

---

## Bridge API v0.1

### HQ → MVP (implemented)

```
POST /governance/proof-anchor
Content-Type: application/json

{
  "pi_native":   "0x<64-hex-chars>",
  "circuit":     "root" | "recovery" | "millerRabin" | "deviceAttest",
  "proposal_id": "<string>"
}

Response 200:
{
  "tx_id":      <int>,
  "entry_hash": "<sha256-hex>"
}

Response 422: pi_native format invalid
Response 500: ledger write failure
```

### MVP → HQ (NOT YET DEFINED)

The MVP has no current need to call back into HQ. If a verification
callback is required in future (e.g. to re-verify a stored envelope),
that API must be defined in a separate ADR.

---

## Deployment Constraint: Single-Instance (v0.1)

**`POST /governance/proof-anchor` is safe only in single-instance deployments.**

The endpoint writes to `get_phase_mirror_audit_ledger()`, which is an
in-process singleton backed by `state/phase_mirror_audit_ledger.json`.

### What this means

- With a single uvicorn worker (or `--workers 1`), the audit chain is
  correctly maintained: one in-memory ledger, one JSON file, one hash chain.
- With multiple workers (uvicorn `--workers N`, gunicorn, or any
  load-balanced multi-instance topology), each worker maintains its
  **own independent ledger**. Proof-anchor entries written to worker A
  are invisible to worker B. The hash chain is broken across processes.

### v0.1 permitted deployment

```bash
# SAFE — single worker only
uvicorn mcp_server.http_transport:app --host 0.0.0.0 --port 8000 --workers 1

# NOT SAFE — breaks audit chain
uvicorn mcp_server.http_transport:app --host 0.0.0.0 --port 8000 --workers 4
```

### Prohibition

**Do not deploy `POST /governance/proof-anchor` behind a load balancer or
with more than one worker process until ADR-007 resolves the shared durable
store.**

### v0.2 migration paths

| Option | When to use | Notes |
|---|---|---|
| SQLite WAL | Single-node, minimal ops overhead | WAL mode; one writer; no new infra |
| Redis + AOF | Multi-worker, same datacenter | Already in docker-compose; fast |
| Postgres | Multi-node production | Full ACID; highest ops cost |

**Target:** v0.2 — tracked in `governance/ADR-007-ledger-durability.md`

---

## Quorum Status: DEFERRED

`GovernanceRootCommit.quorum_threshold` is present in the ledger schema
but is **NOT enforced** in v0.1.

- `signed_by` is a single string attribution field
- No multi-signer assembly or distinct-signer validation exists
- Single-signer root commits are accepted

Multi-signer quorum gates require:
- `signers: list[str]` field in `GovernanceRootCommit`
- Validation in `LedgerStore.create_entry()`
- BLS aggregate signature support from HQ

**Target:** v0.2 — tracked in `governance/ADR-006-quorum-enforcement.md`

---

## L0 Key Isolation Invariant (inherited from HQ)

The following invariant applies to **both** repos:

> No circuit runtime, MCP server handler, or relay/cloud context shall
> ingest raw `identitySecret`. Only HKDF-derived keys may cross any
> server-side boundary.

The `mcp_server/identity.py` manages an RSA server signing key stored
at `state/mcp_server_key.pem`. This is a **server identity key**, not a
client `identitySecret`. It is not in scope of the above invariant, but
it must not be logged, echoed in API responses, or transmitted to any
downstream service.

---

## Open ADRs Gating v0.2

| ADR | Subject | Status | Blocks |
|---|---|---|---|
| ADR-006 | Multi-signer quorum | PROPOSED / undecided | BLS aggregate root commits |
| ADR-007 | Shared durable ledger | PROPOSED / undecided | Multi-worker deployment; L0-8 v1.0 hard gate |
| ADR-008 | L0-8 proof-anchor validator | v0.2 ✅ implemented; v1.0 gates on ADR-007 | — |

---

## Change Log

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-12 | Initial bridge contract; proof-anchor endpoint implemented; quorum deferred |
| v0.1.1 | 2026-04-12 | Single-instance deployment constraint explicit; ADR-007 filed; ADR-008 Option C decided; open ADR table added |
| v0.1.2 | 2026-04-12 | ADR-008 v0.2 implemented: L0-8 warn-only in `ConstitutionModel`; `TestL0_8_ProofAnchor` CI tests added; v0.2 enforcement row marked implemented; Layer Ownership table updated to L0-1..8 |
