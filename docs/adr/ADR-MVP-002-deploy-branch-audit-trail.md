# ADR-MVP-002: Deploy Branch Audit Trail

## Status
Proposed

## Context
The Phase Mirror MVP must preserve auditability for governance actions and
deployment state. The system should be able to reconstruct a trusted snapshot
from the deployed branch and verify the current state against a committed history.

## Decision
- Use a Git-backed ledger model for deployment audits.
- The `deploy` branch stores signed governance metadata and audit commits.
- `GitLedger` tracks commit SHAs, root hashes, and rollback anchors.
- The MVP repository is responsible for bootstrapping the ledger from the current
  `DEPLOY_REPO_PATH`.

## Consequences
- Developers can verify deployed state using standard Git history.
- Governance actions gain a repeatable audit trail without extra database
  infrastructure.
- The MVP remains self-contained and suitable for `docker compose up`.
