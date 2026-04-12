## Option A — Separate `phase-mirror-mvp` Repo ✅ Recommended

**What moves:** Everything produced by Levers 1–4: `governance/constitution.py`, `governance/ledger.py`, `governance/coupling.py`, `mcp_server/app.py`, the three ADRs, `docker-compose.yml` (MVP version), `pyproject.toml`.

**What stays in HQ:** The research tree (`research/formal/`, `pirtm_legacy/`, `digital_twin/`, `hypercompute/`), the CROF-LC healthcare artifacts, the TypeScript monorepo, all the AGI-001–009 ADRs as historical record.

**Why this is the right cut:**

- [PhaseMirror-HQ](https://github.com/PhaseMirror/PhaseMirror-HQ) currently has a `docker-compose.yml` that spins up two `mcp_server` instances behind nginx — it's already modeled as a distributed system, not a single deployable product. The MVP needs to be `docker compose up` and done.
- HQ has 40+ root-level markdown files, `LLVM-MLIR-INSTALLATION-GUIDE.md`, FDA packages, clinical review materials. A new contributor or enterprise evaluator hitting the MVP repo should see exactly three things: the governance daemon, a `README`, and a `docker-compose.yml`.
- The `phase-mirror-mvp` repo becomes the **distribution artifact** — the thing you hand to a client, point a Nuitka build at, or publish to PyPI as `phase-mirror-governance`. HQ remains the research monorepo.

**Suggested structure for the new repo:**

```
phase-mirror-mvp/
├── governance/
│   ├── constitution.py    ← Lever 1
│   ├── ledger.py          ← Lever 2
│   └── coupling.py        ← Lever 3
├── mcp_server/
│   ├── app.py             ← Lever 3
│   ├── tool_registry.yaml
│   └── tools/             ← all 26 tool files (copied, not symlinked)
├── docs/adr/
│   ├── ADR-MVP-001 through ADR-MVP-004
├── tests/
│   └── test_constitution.py ← Lever 1 tests
├── scripts/
│   └── build_binary.sh    ← Lever 4 (Nuitka)
├── docker-compose.yml     ← 3 services: daemon + redis + sandbox
├── pyproject.toml         ← uv-managed
└── README.md
```


***

## Option B — Stay in HQ, Use a `mvp/` Subdirectory

A `mvp/` folder inside HQ avoids a new repo but preserves the noise problem. You still have FDA docs, LLVM guides, and 40 status reports in the same working tree as your `docker compose up`. The only genuine advantage is avoiding the Git submodule / subtree complexity if HQ tooling needs to call MVP code directly. Given that the MVP is a **service** (HTTP API), not a library, there is no import dependency — HQ calls it over HTTP, not via Python import. So Option B's only advantage evaporates.

***

## The Precision Question

Before creating the repo: **should `phase-mirror-mvp` be public or private?**

- **Public** signals open-core positioning: the governance engine is the commodity, the proprietary value is in integrations (CROF-LC, enterprise tooling). This is the correct play if Phase Mirror's business model is the framework, not the applications.
- **Private** keeps the deployment mechanics out of competitors' hands but makes it harder to build community or attract enterprise evaluators who want to audit the governance layer before signing.

The answer to that question should be in an ADR (call it `ADR-MVP-004-repo-strategy.md`) before the repo is created, not after. What's the intended licensing posture?

