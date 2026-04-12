"""Shared constants used by CRMF/CCRE/ACFL/WKD-family modules."""

from __future__ import annotations

import math
from pathlib import Path

DRIFT_GUARD_DEFAULT = 0.3
CONTRACTION_THRESHOLD = 1.0
RESONANCE_MIN_DEFAULT = 0.3
RESONANCE_MAX_DEFAULT = 0.7
LIPSCHITZ_ALPHA_DEFAULT = 0.3

# ─── ADR-016: Circuit-Aware Epsilon Bounds ────────────────────────────
# The convergence recurrence requires:
#   n >= log(||X_0|| / delta) / epsilon
# For a circuit compiled with a fixed loop depth, epsilon must be large enough
# that the required convergence steps fit inside the circuit. The watchdog may
# not decrease epsilon below this safe floor.
CONTRACTIVITY_INITIAL_NORM: float = 1.0
CONTRACTIVITY_TARGET_DELTA: float = 0.01
MAX_CIRCUIT_STEPS: int = 128
EPSILON_DEFAULT: float = 0.05
EPSILON_MIN_SAFE: float = math.log(
	CONTRACTIVITY_INITIAL_NORM / CONTRACTIVITY_TARGET_DELTA
) / MAX_CIRCUIT_STEPS
EPSILON_MIN: float = max(0.01, EPSILON_MIN_SAFE)
EPSILON_MAX: float = 3.0
EPSILON_ADJUST_MAX_PER_CALL: float = 0.02
EPSILON_ADJUST_MAX_CUMULATIVE_10MIN: float = 0.10

# ─── ADR-018: Kill-switch fallback event log path ─────────────────────
# This path is intentionally outside the repository tree so MCP tools that
# operate within the workspace cannot mutate emergency halt fallback records.
KILL_EVENT_LOG_PATH: Path = Path("/var/log/mcp_pmd/kill_events")

# ─── ADR-012: Governance Merkle Root Immutability ─────────────────────
# This constant is the only reference from code to the immutability root.
# The actual root hash is stored in the governance ledger (external to all files),
# breaking the self-reference paradox of trying to hardcode a file's hash inside itself.
#
# Per ADR-012:
# - GOVERNANCE_MERKLE_ROOT_TX_ID is updated when governance modifies immutable files
# - Daemon startup fetches the root from the ledger using this ID
# - Daemon recomputes the live Merkle root and compares to ledger entry
# - If mismatch: engage kill-switch immediately
#
# NOTE: This value is typically 0 until first governance action.
# Set by governance.ledger.create_governance_root_commit()
# Updated via daemon.bootstrap_governance_root.py during system initialization
GOVERNANCE_MERKLE_ROOT_TX_ID: int = 5
