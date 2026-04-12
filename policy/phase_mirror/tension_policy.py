from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is validated in workspace tests
    yaml = None


VOCAB_PATH = Path(__file__).with_name("tension_vocab.yaml")
CURRENT_RUNTIME_GOVERNANCE_VERSION = "phase-mirror-stub-v0"

DEFAULT_TENSION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "LEGITIMACY_PREDICATE_FAILED": {
        "description": "Enforcement bits legitimacy check failed",
        "score_weight": 2.0,
        "severity": "auto_fail",
        "auto_fail": True,
        "suppress_on_triggers": [],
    },
    "GOVERNANCE_VERSION_INCOMPATIBLE": {
        "description": "Target state governance_version is incompatible with the current epoch",
        "score_weight": 0.2,
        "severity": "high",
        "auto_fail": False,
        "suppress_on_triggers": ["L_Phi_breach", "kill_switch", "emergency_rollback"],
    },
}


@lru_cache(maxsize=1)
def load_tension_definitions() -> dict[str, dict[str, Any]]:
    if yaml is None:
        return DEFAULT_TENSION_DEFINITIONS

    try:
        raw = yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return DEFAULT_TENSION_DEFINITIONS

    entries = raw.get("tension_definitions", []) if isinstance(raw, dict) else []
    definitions: dict[str, dict[str, Any]] = {}
    for entry in entries:
        tension_id = entry.get("id")
        if isinstance(tension_id, str):
            definitions[tension_id] = entry
    return definitions or DEFAULT_TENSION_DEFINITIONS


def get_tension_definition(tension_id: str) -> dict[str, Any]:
    return load_tension_definitions().get(tension_id, DEFAULT_TENSION_DEFINITIONS.get(tension_id, {}))


def normalize_trigger(trigger: str) -> str:
    normalized = trigger.strip() if trigger else "none"
    if not normalized:
        return "none"

    lowered = normalized.lower()
    if lowered == "none":
        return "none"
    if lowered.startswith("emergency"):
        return "emergency_rollback"
    if lowered == "l_phi_breach":
        return "L_Phi_breach"
    if lowered == "kill_switch":
        return "kill_switch"
    return normalized