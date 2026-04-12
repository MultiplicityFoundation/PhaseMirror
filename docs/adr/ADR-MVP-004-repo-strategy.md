# ADR-MVP-004: MVP Repository Strategy

## Status
Proposed

## Context
The Phase Mirror MVP should be separate from the HQ research monorepo to keep
its surface area small, easy to understand, and simple to deploy. The MVP is a
distribution artifact, not the full research workspace.

## Decision
- Keep the MVP in its own repository structure under `mvp/` for now.
- Surface only the governance daemon, the FastAPI MCP server, Redis coupling,
  and the supporting architecture decision records.
- Treat the MVP as the distribution artifact for client evaluation and early
  adoption.
- Default to public open-core positioning for the governance layer, with
  proprietary integrations and enterprise tooling developed in HQ.

## Consequences
- The MVP is easy for evaluators to clone, run, and audit.
- HQ remains the long-term research repository, while the MVP becomes the
  client-facing product repo.
- Licensing posture should be finalized by leadership before an external release.
