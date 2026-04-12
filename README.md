# Phase Mirror MVP

This folder contains the Phase Mirror MVP as a minimal self-contained repository.
It is designed for fast local startup and review of the governance daemon, the Redis coupling bus, and the governance ledger.

## Contents

- `governance/` — MVP governance library: `constitution.py`, `ledger.py`, `coupling.py
- `mcp_server/` — FastAPI-based MCP server with dynamic tool registration
- `docs/adr/` — MVP architecture decision records
- `tests/` — MVP harness for constitution validation
- `docker-compose.yml` — local runtime for Redis, daemon, and sandbox
- `pyproject.toml` / `requirements.txt` — Python packaging and dependencies
- `scripts/build_binary.sh` — Nuitka build helper for the MVP daemon
- `cli/` — Phase Mirror CLI analyzer wrapper

## Quick start

1. Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

2. Run the governance daemon directly:

```bash
uvicorn mcp_server.app:app --host 0.0.0.0 --port 8000
```

3. Start the local MVP stack with Docker Compose:

```bash
docker compose up --build
```

4. Inspect the tool registry:

- `http://localhost:8000/docs'

## Notes

- `mcp_server/app.py` is the MVP entrypoint.
- `mcp_server/tool_registry.yaml` drives dynamic tool registration.
- `governance/constitution.py` implements the MVP constitution schema.
- `governance/coupling.py` provides Redis Streams-based coupling.

## Sandbox service

The `sandbox` service in `docker-compose.yml` is a lightweight placeholder for sandboxed execution and integration testing.
