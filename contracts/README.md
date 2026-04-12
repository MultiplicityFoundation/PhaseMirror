# Contracts

This folder is the canonical contract organ for shared PMD type and invariant vocabulary.

The current root package is `shared/`, which contains the cross-cutting contract substrate used by runtime and policy surfaces:

- `types.py` defines shared structured payloads and typed boundaries
- `constants.py` centralizes stable contract constants
- `exceptions.py` provides contract-layer failure vocabulary

In PMD terms, `contracts/` is where stable schemas, types, and invariant-bearing interfaces live before they are enforced by `policy/phase_mirror/`, consumed by `mcp_server/`, or materialized in runtime surfaces.