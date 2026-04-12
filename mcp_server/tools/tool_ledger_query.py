"""Wave 1 ledger-query MCP tool."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = REPO_ROOT / "surplus.db"


def ledger_query(element_id: str | None = None, sqlite_path: str | None = None) -> dict[str, Any]:
    """Inspect the existing SQLite surplus ledger surface without mutating it."""
    db_path = Path(sqlite_path) if sqlite_path else DEFAULT_SQLITE_PATH
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path

    if not db_path.exists():
        return {
            "status": "unavailable",
            "db_path": str(db_path),
            "reason": "SQLite surplus ledger file does not exist yet.",
        }

    if element_id is None:
        return _query_summary(db_path)

    parsed_element_id = int(element_id)
    return _query_entry(db_path, parsed_element_id)


def _query_summary(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'surplus_ledger'"
        )
        has_table = cursor.fetchone() is not None
        if not has_table:
            return {
                "status": "empty",
                "db_path": str(db_path),
                "reason": "SQLite file exists but does not contain a surplus_ledger table.",
            }

        cursor.execute("SELECT element_id FROM surplus_ledger ORDER BY element_id")
        element_ids = [row[0] for row in cursor.fetchall()]
        return {
            "status": "ok",
            "db_path": str(db_path),
            "entry_count": len(element_ids),
            "element_ids": element_ids,
        }


def _query_entry(db_path: Path, element_id: int) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT element_id, current_surplus, event_count, cumulative_deltas, last_partner, created_at, last_updated_at "
            "FROM surplus_ledger WHERE element_id = ?",
            (element_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "status": "not_found",
                "db_path": str(db_path),
                "element_id": element_id,
            }

        cursor.execute(
            "SELECT timestamp, delta, partner_id, reason, surplus_after "
            "FROM ledger_events WHERE element_id = ? ORDER BY id",
            (element_id,),
        )
        history = [
            {
                "timestamp": event_row[0],
                "delta": event_row[1],
                "partner_id": event_row[2],
                "reason": event_row[3],
                "surplus_after": event_row[4],
            }
            for event_row in cursor.fetchall()
        ]

    return {
        "status": "ok",
        "db_path": str(db_path),
        "entry": {
            "element_id": row[0],
            "current_surplus": row[1],
            "event_count": row[2],
            "cumulative_deltas": row[3],
            "last_partner": row[4],
            "created_at": row[5],
            "last_updated_at": row[6],
            "history": history,
        },
    }