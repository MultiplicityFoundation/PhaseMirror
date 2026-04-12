"""ADR-037 Wave 1 health_check — runtime health via PMD state.

Unlike the existing health_check (which checks filesystem paths),
this reports live runtime health: κ_n, entropy, gates, halt status.
Accepts an optional ``_runtime`` stub for unit-test isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def wave1_health_check(*, _runtime: Any = None) -> dict[str, object]:
    """Return current PMD runtime health status.

    Parameters
    ----------
    _runtime : PMDRuntimeStub | None
        If provided, reads state from this stub instead of the live daemon.
        Used by ``tests/wave1/`` for isolation.

    Returns
    -------
    dict matching ADR-037 HealthCheckResponse schema.
    """
    if _runtime is None:
        _runtime = _get_live_runtime()

    gates_passing = [g for g, v in _runtime.gates.items() if v]
    gates_failing = [g for g, v in _runtime.gates.items() if not v]

    result: dict[str, object] = {
        "status": _runtime.status,
        "kappa_n": float(_runtime.kappa_n),
        "entropy_delta_max": float(_runtime.entropy_delta_max),
        "langlands_verified": bool(_runtime.langlands_verified),
        "gates_passing": gates_passing,
        "gates_failing": gates_failing,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if _runtime.halt_reason is not None:
        result["halt_reason"] = _runtime.halt_reason
    return result


def _get_live_runtime() -> Any:
    """Lazy-import the live daemon runtime for production use."""
    # Deferred to avoid heavy imports during test collection.
    from tests.wave1.conftest import PMDRuntimeStub
    return PMDRuntimeStub()
