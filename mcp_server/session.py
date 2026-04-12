"""F-05: Stateless Session and Rate Limiting.

Externalises all session state so that any instance in a horizontal pool can
serve any request.  Session context is keyed by JTI (JWT ID) from the OAuth
token and stored in a shared backend (SQLite for local/CI, Redis for production).

Per Gate F ADR F-05 and L0 invariant 3:
  - No in-process session cache (``_session_cache``, ``@lru_cache``, etc.)
  - Rate limit counter increments MUST be atomic
  - SQLite backend uses ``BEGIN EXCLUSIVE`` for atomic increment
  - Redis backend uses ``WATCH/MULTI/EXEC`` pipeline

The ``InMemorySessionBackend`` is provided for unit testing ONLY and must
never be used in production paths.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Tuple

DEFAULT_SESSION_DB_PATH = Path(__file__).resolve().parent.parent / "state" / "mcp_sessions.db"
DEFAULT_RATE_LIMIT_DB_PATH = Path(__file__).resolve().parent.parent / "state" / "mcp_rate_limits.db"

# Per-tool rate limits: (calls_per_minute)
DEFAULT_RATE_LIMITS: Dict[str, int] = {
    # Read-only tools — generous limit
    "health_check": 120,
    "manifest_status": 60,
    "ledger_query": 60,
    "normative_record_query": 60,
    "rollback_status": 60,
    "checkpoint_inventory": 60,
    "daemon_heartbeat": 60,
    # Evaluation tools
    "phase_mirror": 30,
    "agent_dispatch": 30,
    # Epsilon adjustments — rate-limited by protocol
    "epsilon_adjust": 2,
    "daemon_epsilon_adjust": 2,
    # Write / mutation tools — tightly limited
    "checkpoint_write": 10,
    "checkpoint_prune": 10,
    "rollback_execute": 5,
}
DEFAULT_RATE_LIMIT_FALLBACK = 30  # calls per minute for unregistered tools


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class SessionContext:
    """State associated with a single OAuth JTI (JWT ID)."""

    jti: str
    subject: str
    scopes: FrozenSet[str]
    call_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        d = asdict(self)
        d["scopes"] = sorted(d["scopes"])  # frozenset is not JSON-serializable
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "SessionContext":
        d = json.loads(raw)
        d["scopes"] = frozenset(d.get("scopes", []))
        return cls(**d)


# ---------------------------------------------------------------------------
# SessionBackend ABC
# ---------------------------------------------------------------------------


class SessionBackend(ABC):
    """Abstract interface for externalised session storage."""

    @abstractmethod
    def get(self, jti: str) -> Optional[SessionContext]:
        """Return the session for *jti*, or None if not found / expired."""

    @abstractmethod
    def set(self, jti: str, context: SessionContext, ttl_seconds: int = 3600) -> None:
        """Persist *context* under *jti* with an expiry of *ttl_seconds*."""

    @abstractmethod
    def delete(self, jti: str) -> None:
        """Remove the session for *jti* (no-op if absent)."""


# ---------------------------------------------------------------------------
# InMemorySessionBackend (TEST ONLY)
# ---------------------------------------------------------------------------


class InMemorySessionBackend(SessionBackend):
    """In-memory session store.  DO NOT USE IN PRODUCTION.

    Provided exclusively for unit tests where external storage is unavailable.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[SessionContext, float]] = {}

    def get(self, jti: str) -> Optional[SessionContext]:
        entry = self._store.get(jti)
        if entry is None:
            return None
        ctx, expires_at = entry
        if time.time() > expires_at:
            del self._store[jti]
            return None
        return ctx

    def set(self, jti: str, context: SessionContext, ttl_seconds: int = 3600) -> None:
        self._store[jti] = (context, time.time() + ttl_seconds)

    def delete(self, jti: str) -> None:
        self._store.pop(jti, None)


# ---------------------------------------------------------------------------
# SQLiteSessionBackend
# ---------------------------------------------------------------------------


class SQLiteSessionBackend(SessionBackend):
    """SQLite-backed session store for local and CI deployments.

    Thread-safe via a per-instance lock around ``check_same_thread=False``
    connections.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_SESSION_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._create_table()

    def _create_table(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    jti        TEXT PRIMARY KEY,
                    context    TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            self._conn.commit()

    def get(self, jti: str) -> Optional[SessionContext]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT context, expires_at FROM sessions WHERE jti = ?", (jti,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        context_json, expires_at = row
        if time.time() > expires_at:
            self.delete(jti)
            return None
        return SessionContext.from_json(context_json)

    def set(self, jti: str, context: SessionContext, ttl_seconds: int = 3600) -> None:
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (jti, context, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(jti) DO UPDATE SET
                    context    = excluded.context,
                    expires_at = excluded.expires_at
                """,
                (jti, context.to_json(), expires_at),
            )
            self._conn.commit()

    def delete(self, jti: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE jti = ?", (jti,))
            self._conn.commit()


# ---------------------------------------------------------------------------
# RedisSessionBackend (stub — requires redis-py)
# ---------------------------------------------------------------------------


class RedisSessionBackend(SessionBackend):
    """Redis-backed session store for production deployments.

    Requires the ``redis`` package.  If unavailable, construction raises
    ``ImportError`` so the caller can fall back to ``SQLiteSessionBackend``.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        try:
            import redis as _redis  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'redis' package is required for RedisSessionBackend. "
                "Install it with: pip install redis"
            ) from exc
        self._client = _redis.from_url(redis_url, decode_responses=True)

    def get(self, jti: str) -> Optional[SessionContext]:
        raw = self._client.get(f"session:{jti}")
        if raw is None:
            return None
        return SessionContext.from_json(raw)

    def set(self, jti: str, context: SessionContext, ttl_seconds: int = 3600) -> None:
        self._client.setex(f"session:{jti}", ttl_seconds, context.to_json())

    def delete(self, jti: str) -> None:
        self._client.delete(f"session:{jti}")


# ---------------------------------------------------------------------------
# RateLimitBackend ABC
# ---------------------------------------------------------------------------


class RateLimitBackend(ABC):
    """Abstract interface for atomic rate-limit counters."""

    @abstractmethod
    def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> Tuple[bool, int]:
        """Atomically check and increment a counter.

        Returns:
            ``(allowed, remaining)`` where *allowed* is True when the counter
            was below *limit* before the increment, and *remaining* is the
            number of calls still permitted in this window.
        """


def _rate_key(subject: str, tool_name: str) -> str:
    """Compose a rate-limit bucket key for *subject* + *tool_name* + current minute."""
    minute_bucket = int(time.time()) // 60
    return f"rate:{subject}:{tool_name}:{minute_bucket}"


# ---------------------------------------------------------------------------
# SQLiteRateLimitBackend
# ---------------------------------------------------------------------------


class SQLiteRateLimitBackend(RateLimitBackend):
    """SQLite-backed atomic rate-limit counter.

    Uses ``BEGIN EXCLUSIVE`` transactions to guarantee atomicity under
    concurrent writes (no lost-update anomaly).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_RATE_LIMIT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._create_table()

    def _create_table(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_counters (
                    key        TEXT PRIMARY KEY,
                    count      INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL
                )
                """
            )
            self._conn.commit()

    def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> Tuple[bool, int]:
        now = time.time()
        expires_at = now + window_seconds

        with self._lock:
            # BEGIN EXCLUSIVE ensures atomicity: no other writer can modify the row
            # between our SELECT and our UPDATE.
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                cur = self._conn.execute(
                    "SELECT count, expires_at FROM rate_counters WHERE key = ?", (key,)
                )
                row = cur.fetchone()

                if row is None or now > row[1]:
                    # First call in this window (or window expired)
                    count_after = 1
                    self._conn.execute(
                        """
                        INSERT INTO rate_counters (key, count, expires_at)
                        VALUES (?, 1, ?)
                        ON CONFLICT(key) DO UPDATE SET count = 1, expires_at = excluded.expires_at
                        """,
                        (key, expires_at),
                    )
                else:
                    count_after = row[0] + 1
                    self._conn.execute(
                        "UPDATE rate_counters SET count = ? WHERE key = ?",
                        (count_after, key),
                    )

                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        allowed = count_after <= limit
        remaining = max(0, limit - count_after)
        return allowed, remaining


# ---------------------------------------------------------------------------
# InMemoryRateLimitBackend (TEST ONLY)
# ---------------------------------------------------------------------------


class InMemoryRateLimitBackend(RateLimitBackend):
    """In-memory rate-limit counter.  DO NOT USE IN PRODUCTION.

    Not thread-safe under high concurrency; suitable only for unit tests.
    """

    def __init__(self) -> None:
        self._counters: Dict[str, Tuple[int, float]] = {}

    def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> Tuple[bool, int]:
        now = time.time()
        count, expires_at = self._counters.get(key, (0, now + window_seconds))
        if now > expires_at:
            count, expires_at = 0, now + window_seconds
        count_after = count + 1
        self._counters[key] = (count_after, expires_at)
        allowed = count_after <= limit
        remaining = max(0, limit - count_after)
        return allowed, remaining


# ---------------------------------------------------------------------------
# Convenience helper used by server.py
# ---------------------------------------------------------------------------


def get_rate_limit_for_tool(tool_name: str) -> int:
    """Return the per-minute rate limit for *tool_name*."""
    return DEFAULT_RATE_LIMITS.get(tool_name, DEFAULT_RATE_LIMIT_FALLBACK)
