# Governance Bridge Contract v0.1

**Status:** Accepted  
**Date:** 2026-04-12  
**Deciders:** Multiplicity Foundation  

## Purpose

This document defines the boundary between the two Phase Mirror governance
layers and the minimum conditions for a proposal to be considered **FULLY
LAWFUL**. It is the canonical reference for both repos.

- **MVP** (`MultiplicityFoundation/Phase-Mirror`) — behavioral governance daemon
- **HQ** (`PhaseMirror/PhaseMirror-HQ`) — cryptographic proof engine

---

## Layer Ownership

| Invariant Class | Enforced By | Primary File |
|---|---|---|
| Behavioral (drift, critique, contractivity) | MVP constitution | `governance/constitution.py` L0-1..7 |
| Cryptographic (proof sovereignty, BLS key isolation) | HQ proof engine | `multiplicity/crypto/src/index.ts` |
| Ledger integrity (hash-chained audit trail) | MVP AuditLedger | `governance/ledger.py` |
| Tool invocation governance | MVP tool registry | `mcp_server/tool_registry.yaml` |
| On-chain submission gate | HQ proof-manager | `packages/lambda/apps/relay/` |

---

## FULLY LAWFUL Definition (v0.1)

A proposal is **FULLY LAWFUL** only when ALL of the following hold:

1. `ConstitutionModel(**state)` raises no `ConstitutionViolation` (L0-1..7 pass)
2. An HQ `MultiplicityProofEnvelope` exists for the same `proposal_id`
3. Its `pi_native` is recorded in the MVP `AuditLedger` as a `PROOF_ANCHOR` entry

Conditions 2 and 3 are **not yet enforced in code** as of v0.1. They are
documented here as the target definition. Enforcement is tracked in:
- L0-8 validator: `constitution.py` (target: v0.2, 30-day horizon)
- Bridge endpoint: `POST /governance/proof-anchor` (implemented in v0.1)

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

The MVP `mcp_server/identity.py` manages an RSA server signing key stored
at `state/mcp_server_key.pem`. This is a **server identity key**, not a
client `identitySecret`. It is not in scope of the above invariant, but
it must not be logged, echoed in API responses, or transmitted to any
downstream service.

---

## Change Log

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-12 | Initial bridge contract; proof-anchor endpoint implemented; quorum deferred |
