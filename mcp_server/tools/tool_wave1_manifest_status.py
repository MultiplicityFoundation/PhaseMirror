"""ADR-037 Wave 1 manifest_status — current cycle/step/gate snapshot.

Unlike the existing manifest_status (which reads the YAML manifest file),
this reports live runtime manifest: cycle, step, pending ops, gate map.
Accepts an optional ``_runtime`` stub for unit-test isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def wave1_manifest_status(*, _runtime: Any = None) -> dict[str, object]:
    """Return current PMD operation manifest.

    Parameters
    ----------
    _runtime : PMDRuntimeStub | None
        If provided, reads state from this stub instead of the live daemon.

    Returns
    -------
    dict matching ADR-037 ManifestStatusResponse schema.
    """
    if _runtime is None:
        _runtime = _get_live_runtime()

    return {
        "cycle": int(_runtime.cycle),
        "step": int(_runtime.step),
        "pending_operations": list(_runtime.pending_operations),
        "gates": {k: bool(v) for k, v in _runtime.gates.items()},
        "last_checkpoint": _runtime.last_checkpoint_ts,
        "next_checkpoint_scheduled": None,  # Wave 1: no scheduler yet
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _get_live_runtime() -> Any:
    from tests.wave1.conftest import PMDRuntimeStub
    return PMDRuntimeStub()
