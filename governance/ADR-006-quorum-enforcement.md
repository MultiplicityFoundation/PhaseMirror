# ADR-006: Multi-Signer Quorum Enforcement for GovernanceRootCommit

**Status:** PROPOSED — NOT YET DECIDED  
**Date:** 2026-04-12  
**Deciders:** TBD (requires Multiplicity Foundation council vote per L0-6)

---

## Context

`GovernanceRootCommit` in `governance/ledger.py` has a `quorum_threshold`
field (default: `2`) and a `signed_by` string field. As of v0.1, quorum
is aspirational only:

- `LedgerStore.create_entry()` performs no signer-count validation
- `signed_by` is a single string; no `signers: list[str]` field exists
- The bootstrap flow (`daemon/bootstrap_governance_root.py`) passes one
  `signed_by` value with no quorum assembly step

This was explicitly documented and deferred in `GOVERNANCE-BRIDGE.md v0.1`.

---

## Decision

**NOT YET DECIDED.**

This ADR must be resolved before any of the following ship:
- Multi-signer governance actions in production
- BLS aggregate signature support bridged from HQ
- Any on-chain submission that asserts quorum as a validity condition

---

## Options Under Consideration

### Option A — Minimal schema + guard (low effort)

```python
# GovernanceRootCommit
signers: list[str] = field(default_factory=list)

# LedgerStore.create_entry()
effective = entry.signers or ([entry.signed_by] if entry.signed_by else [])
if len(set(effective)) < entry.quorum_threshold:
    raise GovernanceViolation(
        f"Quorum not met: {len(set(effective))} signer(s) "
        f"< threshold {entry.quorum_threshold}"
    )
```

- Genesis commit must set `quorum_threshold=1` explicitly
- Breaking change to bootstrap flow
- Does not verify signatures — only counts distinct signer strings

### Option B — BLS aggregate quorum (high effort, cryptographically sound)

- Each signer submits a BLS partial signature over `merkle_root`
- `LedgerStore` verifies aggregate signature using HQ `verifyProofEnvelope`
- Requires HQ→MVP bridge API v0.2 (MVP→HQ callback, not yet defined)
- Full sovereign multi-party governance

---

## Consequences of Deferral

- `quorum_threshold=2` in all root commits is a false declaration until
  Option A or B is implemented
- The field should be treated as a documentation hint only
- Any external auditor or on-chain verifier must be informed that quorum
  is not enforced in v0.1

---

## Required Before Closing This ADR

- [ ] Council vote on Option A vs B vs hybrid
- [ ] Schema migration plan for existing ledger entries
- [ ] Bootstrap flow updated and tested
- [ ] `GOVERNANCE-BRIDGE.md` updated to reflect enforcement status
- [ ] L0-8 or equivalent validator added to `constitution.py`
