"""Registry-backed Wave 1 MCP server scaffold for Phase Mirror PMD.

F-01 (Gate F): ``_governance_preflight()`` is called at the top of
``build_server()`` to enforce the governance bootstrap contract.  Pass
``skip_governance_preflight=True`` ONLY in tests.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Monorepo package path bootstrap
# ---------------------------------------------------------------------------
# Ensure development packages under `packages/` are available as top-level
# modules before importing daemon startup verification and other governance
# runtime surfaces.
ROOT = Path(__file__).resolve().parent.parent
PACKAGES_ROOT = ROOT / "packages"
for path in (str(PACKAGES_ROOT), str(ROOT)):
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)

from daemon.startup_verification import verify_server_startup_gate
from .middleware.auth import ToolIntegrityVerifier, verify_tool_before_dispatch
from .observability import get_logger, init_telemetry, metrics, track_request, metrics_response
from ._yaml import load_yaml_file

_logger = get_logger(__name__)

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover - optional dependency in Wave 1
    FastMCP = None


MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = MODULE_ROOT / "tool_registry.yaml"
WORKSPACE_ROOT = MODULE_ROOT.parent

# F-01: Sentinel file path.  Bootstrap writes this file at step 3 and removes
# it at step 7.  If it exists at server startup, the previous bootstrap either
# failed or is still in progress — refuse to start.
MCP_BOOTSTRAP_SENTINEL_PATH = WORKSPACE_ROOT / "state" / "mcp_governance_bootstrap.sentinel"

# F-01: Files whose content is covered by the governance Merkle root.
MCP_IMMUTABLE_FILE_SET: list[str] = [
    "mcp_server/tool_registry.yaml",
    "mcp_server/middleware/auth.py",
    "contracts/shared/constants.py",
    "contracts/shared/types.py",
]


def _compute_mcp_merkle_root(workspace_root: Path | None = None) -> str:
    """Compute the SHA-256 Merkle root over ``MCP_IMMUTABLE_FILE_SET``.

    Uses the same binary-tree construction as ADR-029:
      leaf_i = SHA256(bytes(f_i))
      node   = SHA256(left_hash || right_hash)

    Returns the root hash as a lowercase hex string.
    """
    root = workspace_root or WORKSPACE_ROOT
    leaves: list[bytes] = []
    for rel_path in sorted(MCP_IMMUTABLE_FILE_SET):
        full_path = root / rel_path
        if not full_path.exists():
            continue
        leaf = hashlib.sha256(full_path.read_bytes()).digest()
        leaves.append(leaf)

    if not leaves:
        return "0" * 64  # degenerate: no files tracked

    while len(leaves) > 1:
        if len(leaves) % 2 == 1:
            leaves.append(leaves[-1])  # duplicate last node (standard Merkle)
        next_level: list[bytes] = []
        for i in range(0, len(leaves), 2):
            combined = leaves[i] + leaves[i + 1]
            next_level.append(hashlib.sha256(combined).digest())
        leaves = next_level

    return leaves[0].hex()


def _governance_preflight(workspace_root: Path | None = None) -> None:
    """F-01: Governance preflight guard for MCP server startup.

    Checks two conditions before any registry is loaded:
      1. The MCP bootstrap sentinel must NOT exist (guards against partial/failed
         bootstrap being silently ignored).
      2. The governance Merkle root tx_id must be non-zero (guards against a
         fresh clone where no governance root has been established).

    The full Merkle root verification (comparing against the ledger) is
    handled by the existing ``enforce_startup_gate`` mechanism in
    ``RegistryBackedServer``, which calls ``verify_server_startup_gate()``.
    This split keeps Gate F's sentinel-file contract orthogonal to the
    daemon-layer Merkle verification.

    Raises:
        RuntimeError: with an actionable message on any failure.
    """
    root = workspace_root or WORKSPACE_ROOT
    sentinel = root / "state" / "mcp_governance_bootstrap.sentinel"

    # Condition 1 — sentinel must be absent
    if sentinel.exists():
        raise RuntimeError(
            "MCP governance bootstrap sentinel exists at "
            f"'{sentinel}'.  A bootstrap is in progress or failed.  "
            "Do not start the server until the sentinel is cleared.  "
            "Re-run: python -m scripts.governance_bootstrap"
        )

    # Condition 2 — governance tx_id must be initialised
    try:
        from contracts.shared.constants import GOVERNANCE_MERKLE_ROOT_TX_ID as tx_id
    except ImportError:
        tx_id = 0  # type: ignore[assignment]

    if not tx_id or tx_id == 0:
        raise RuntimeError(
            "Governance Merkle root transaction ID (GOVERNANCE_MERKLE_ROOT_TX_ID) "
            "is unset (value=0).  Run the governance bootstrap ceremony first: "
            "python -m scripts.governance_bootstrap"
        )


@dataclass(frozen=True)
class ToolSpec:
    """Machine-readable MCP tool contract loaded from the registry."""

    name: str
    module: str
    callable_name: str
    file_path: str
    description: str
    output: str
    inputs: tuple[str, ...]
    verify_integrity: bool
    critical: bool


class ServerStartupGateError(RuntimeError):
    """Raised when governance startup prerequisites are not satisfied."""


class InMemoryMCP:
    """Small fallback surface that mimics the subset of FastMCP used here."""

    def __init__(self, name: str):
        self.name = name
        self._tools: dict[str, dict[str, Any]] = {}

    def tool(self, name: str | None = None, description: str | None = None) -> Callable:
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            self._tools[tool_name] = {
                "description": description or (func.__doc__ or ""),
                "callable": func,
            }
            return func

        return decorator


class RegistryBackedServer:
    """Bootstraps MCP-callable tools from `tool_registry.yaml`."""

    def __init__(
        self,
        registry_path: Path | None = None,
        prefer_fastmcp: bool = True,
        integrity_verifier: ToolIntegrityVerifier | None = None,
        enforce_startup_gate: bool = False,
    ):
        if enforce_startup_gate:
            ok, message = verify_server_startup_gate()
            if not ok:
                raise ServerStartupGateError(message)

        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self.registry = self._load_registry(self.registry_path)
        self.backend = self._create_backend(prefer_fastmcp)
        self._tool_callables: dict[str, Callable[..., Any]] = {}
        self._tool_specs: dict[str, ToolSpec] = {}
        self.integrity_verifier = integrity_verifier or ToolIntegrityVerifier()
        self._register_tools()

    def _create_backend(self, prefer_fastmcp: bool) -> Any:
        server_name = self.registry.get("server_name", "tooling-pmd")
        if prefer_fastmcp and FastMCP is not None:
            return FastMCP(server_name)
        return InMemoryMCP(server_name)

    def _load_registry(self, path: Path) -> dict[str, Any]:
        registry = load_yaml_file(path)
        if not isinstance(registry, dict):
            raise ValueError(f"Registry at {path} must deserialize to a mapping")
        return registry

    def _iter_specs(self) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for entry in self.registry.get("tools", []):
            specs.append(
                ToolSpec(
                    name=str(entry["name"]),
                    module=str(entry["module"]),
                    callable_name=str(entry["callable"]),
                    file_path=str(entry.get("file", "")),
                    description=str(entry.get("description", "")),
                    output=str(entry.get("output", "object")),
                    inputs=tuple(str(item) for item in entry.get("inputs", [])),
                    verify_integrity=bool(entry.get("verify_integrity", False)),
                    critical=bool(entry.get("critical", False)),
                )
            )
        return specs

    def _register_tools(self) -> None:
        for spec in self._iter_specs():
            tool_callable = self._resolve_callable(spec)
            self._tool_callables[spec.name] = tool_callable
            self._tool_specs[spec.name] = spec
            decorator = getattr(self.backend, "tool")
            try:
                decorator(name=spec.name, description=spec.description)(tool_callable)
            except TypeError:
                decorator()(tool_callable)

    def _resolve_callable(self, spec: ToolSpec) -> Callable[..., Any]:
        module = importlib.import_module(spec.module)
        tool_callable = getattr(module, spec.callable_name)
        if not callable(tool_callable):
            raise TypeError(f"Registry target {spec.module}:{spec.callable_name} is not callable")
        return tool_callable

    def describe(self) -> dict[str, Any]:
        return {
            "server_name": self.registry.get("server_name", "tooling-pmd"),
            "schema_version": self.registry.get("schema_version"),
            "governance_version": self.registry.get("governance_version"),
            "backend": type(self.backend).__name__,
            "registry_path": str(self.registry_path),
            "tool_count": len(self._tool_callables),
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "module": spec.module,
                "callable": spec.callable_name,
                "file": spec.file_path,
                "description": spec.description,
                "output": spec.output,
                "inputs": list(spec.inputs),
                "verify_integrity": spec.verify_integrity,
                "critical": spec.critical,
            }
            for spec in self._iter_specs()
        ]

    def call_tool(self, tool_name: str, **kwargs: Any) -> Any:
        if tool_name not in self._tool_callables:
            available = ", ".join(sorted(self._tool_callables))
            raise KeyError(f"Unknown tool '{tool_name}'. Available tools: {available}")
        spec = self._tool_specs[tool_name]
        verify_tool_before_dispatch(
            verifier=self.integrity_verifier,
            tool_name=spec.name,
            tool_file=spec.file_path,
            verify_integrity=spec.verify_integrity,
        )
        with track_request(tool_name):
            return self._tool_callables[tool_name](**kwargs)


def build_server(
    registry_path: Path | None = None,
    prefer_fastmcp: bool = True,
    integrity_verifier: ToolIntegrityVerifier | None = None,
    enforce_startup_gate: bool = False,
    skip_governance_preflight: bool = False,
) -> RegistryBackedServer:
    """Build the Wave 1 PMD server scaffold.

    F-01: ``_governance_preflight()`` is the first action taken before the
    registry is loaded.  Pass ``skip_governance_preflight=True`` ONLY in
    test code where the governance bootstrap has not been run.
    """
    if not skip_governance_preflight:
        _governance_preflight()

    init_telemetry(service_name="mcp-server")
    _logger.info("MCP server build complete", extra={"tool_name": "build_server"})

    return RegistryBackedServer(
        registry_path=registry_path,
        prefer_fastmcp=prefer_fastmcp,
        integrity_verifier=integrity_verifier,
        enforce_startup_gate=enforce_startup_gate,
    )


def _parse_kwarg_pairs(pairs: list[str]) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected key=value argument, received: {pair}")
        key, value = pair.split("=", 1)
        kwargs[key] = value
    return kwargs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase Mirror PMD MCP server scaffold")
    parser.add_argument("command", choices=["describe", "list", "call"], nargs="?", default="describe")
    parser.add_argument("tool_name", nargs="?")
    parser.add_argument("tool_args", nargs="*")
    args = parser.parse_args(argv)

    server = build_server(enforce_startup_gate=True)
    if args.command == "describe":
        print(json.dumps(server.describe(), indent=2, sort_keys=True))
        return 0

    if args.command == "list":
        print(json.dumps(server.list_tools(), indent=2, sort_keys=True))
        return 0

    if not args.tool_name:
        parser.error("the 'call' command requires a tool name")

    result = server.call_tool(args.tool_name, **_parse_kwarg_pairs(args.tool_args))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())