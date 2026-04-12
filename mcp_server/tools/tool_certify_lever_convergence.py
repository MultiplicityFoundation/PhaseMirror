"""Wave-3 lever branch manager: certify convergence."""

from __future__ import annotations

from datetime import datetime, timezone


def certify_lever_convergence(
    branch_name: str,
    wac: float,
    wac_threshold: float = 0.7,
    metadata_json: str | None = None,
) -> dict[str, object]:
    if not branch_name:
        raise ValueError("branch_name is required")
    if wac < 0.0 or wac > 1.0:
        raise ValueError("wac must be between 0.0 and 1.0")

    certified = wac <= wac_threshold
    return {
        "method": "certify_lever_convergence",
        "branch_name": branch_name,
        "wac": wac,
        "wac_threshold": wac_threshold,
        "certified": certified,
        "spectral_radius": wac,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata_json": metadata_json,
    }
