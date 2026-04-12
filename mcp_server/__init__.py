"""Phase Mirror MCP server package."""

from __future__ import annotations

from typing import Any

__all__ = ["RegistryBackedServer", "build_server"]


def __getattr__(name: str) -> Any:
	if name in __all__:
		from .server import RegistryBackedServer, build_server

		exports = {
			"RegistryBackedServer": RegistryBackedServer,
			"build_server": build_server,
		}
		return exports[name]
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")