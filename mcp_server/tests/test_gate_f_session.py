"""Gate F — F-05: Stateless Session and Rate Limiting Tests.

Tests for:
- SessionContext serialisation round-trip
- InMemorySessionBackend CRUD and TTL
- SQLiteSessionBackend CRUD and TTL
- InMemoryRateLimitBackend check_and_increment
- SQLiteRateLimitBackend atomic increment
- get_rate_limit_for_tool
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.session import (
    InMemoryRateLimitBackend,
    InMemorySessionBackend,
    SessionContext,
    SQLiteRateLimitBackend,
    SQLiteSessionBackend,
    _rate_key,
    get_rate_limit_for_tool,
)


# ---------------------------------------------------------------------------
# SessionContext — serialisation
# ---------------------------------------------------------------------------


def test_session_context_json_round_trip():
    ctx = SessionContext(
        jti="jti-abc",
        subject="user@example.com",
        scopes=frozenset({"pmd:read", "pmd:write"}),
        call_count=5,
    )
    serialised = ctx.to_json()
    recovered = SessionContext.from_json(serialised)
    assert recovered.jti == ctx.jti
    assert recovered.subject == ctx.subject
    assert recovered.scopes == ctx.scopes
    assert recovered.call_count == ctx.call_count


def test_session_context_scopes_recovered_as_frozenset():
    ctx = SessionContext(jti="x", subject="y", scopes=frozenset({"pmd:read"}))
    recovered = SessionContext.from_json(ctx.to_json())
    assert isinstance(recovered.scopes, frozenset)


# ---------------------------------------------------------------------------
# InMemorySessionBackend
# ---------------------------------------------------------------------------


def test_in_memory_set_and_get():
    backend = InMemorySessionBackend()
    ctx = SessionContext(jti="j1", subject="s1", scopes=frozenset({"pmd:read"}))
    backend.set("j1", ctx)
    result = backend.get("j1")
    assert result is not None
    assert result.jti == "j1"


def test_in_memory_get_missing_returns_none():
    backend = InMemorySessionBackend()
    assert backend.get("nonexistent") is None


def test_in_memory_delete_removes_session():
    backend = InMemorySessionBackend()
    ctx = SessionContext(jti="j2", subject="s2", scopes=frozenset())
    backend.set("j2", ctx)
    backend.delete("j2")
    assert backend.get("j2") is None


def test_in_memory_delete_nonexistent_no_error():
    backend = InMemorySessionBackend()
    backend.delete("ghost")  # must not raise


def test_in_memory_ttl_expiry():
    """Sessions expire after their TTL."""
    backend = InMemorySessionBackend()
    ctx = SessionContext(jti="expire-me", subject="s", scopes=frozenset())
    backend.set("expire-me", ctx, ttl_seconds=0)  # 0-second TTL
    # Give a tiny bit of time to pass
    time.sleep(0.01)
    assert backend.get("expire-me") is None


# ---------------------------------------------------------------------------
# SQLiteSessionBackend
# ---------------------------------------------------------------------------


def test_sqlite_session_set_and_get(tmp_path):
    backend = SQLiteSessionBackend(db_path=tmp_path / "sessions.db")
    ctx = SessionContext(jti="sq1", subject="alice", scopes=frozenset({"pmd:read"}))
    backend.set("sq1", ctx)
    result = backend.get("sq1")
    assert result is not None
    assert result.subject == "alice"


def test_sqlite_session_missing_returns_none(tmp_path):
    backend = SQLiteSessionBackend(db_path=tmp_path / "sessions.db")
    assert backend.get("missing") is None


def test_sqlite_session_delete(tmp_path):
    backend = SQLiteSessionBackend(db_path=tmp_path / "sessions.db")
    ctx = SessionContext(jti="sq2", subject="bob", scopes=frozenset())
    backend.set("sq2", ctx)
    backend.delete("sq2")
    assert backend.get("sq2") is None


def test_sqlite_session_overwrite(tmp_path):
    """Overwriting an existing session must update it."""
    backend = SQLiteSessionBackend(db_path=tmp_path / "sessions.db")
    ctx1 = SessionContext(jti="sq3", subject="alice", scopes=frozenset({"pmd:read"}))
    ctx2 = SessionContext(jti="sq3", subject="bob", scopes=frozenset({"pmd:write"}))
    backend.set("sq3", ctx1)
    backend.set("sq3", ctx2)
    result = backend.get("sq3")
    assert result is not None
    assert result.subject == "bob"


def test_sqlite_session_ttl_expiry(tmp_path):
    backend = SQLiteSessionBackend(db_path=tmp_path / "sessions.db")
    ctx = SessionContext(jti="expire", subject="s", scopes=frozenset())
    backend.set("expire", ctx, ttl_seconds=0)
    time.sleep(0.01)
    assert backend.get("expire") is None


# ---------------------------------------------------------------------------
# InMemoryRateLimitBackend
# ---------------------------------------------------------------------------


def test_in_memory_rate_limit_allows_within_limit():
    backend = InMemoryRateLimitBackend()
    allowed, remaining = backend.check_and_increment("key1", limit=5)
    assert allowed is True
    assert remaining == 4


def test_in_memory_rate_limit_denies_at_limit():
    backend = InMemoryRateLimitBackend()
    for _ in range(5):
        backend.check_and_increment("key2", limit=5)
    allowed, remaining = backend.check_and_increment("key2", limit=5)
    assert allowed is False
    assert remaining == 0


def test_in_memory_rate_limit_remaining_decrements():
    backend = InMemoryRateLimitBackend()
    _, r1 = backend.check_and_increment("key3", limit=10)
    _, r2 = backend.check_and_increment("key3", limit=10)
    assert r2 == r1 - 1


# ---------------------------------------------------------------------------
# SQLiteRateLimitBackend
# ---------------------------------------------------------------------------


def test_sqlite_rate_limit_allows_within_limit(tmp_path):
    backend = SQLiteRateLimitBackend(db_path=tmp_path / "rate.db")
    allowed, remaining = backend.check_and_increment("rl-key1", limit=5)
    assert allowed is True
    assert remaining == 4


def test_sqlite_rate_limit_denies_at_limit(tmp_path):
    backend = SQLiteRateLimitBackend(db_path=tmp_path / "rate.db")
    for _ in range(5):
        backend.check_and_increment("rl-key2", limit=5)
    allowed, remaining = backend.check_and_increment("rl-key2", limit=5)
    assert allowed is False
    assert remaining == 0


def test_sqlite_rate_limit_independent_keys(tmp_path):
    backend = SQLiteRateLimitBackend(db_path=tmp_path / "rate.db")
    allowed_a, _ = backend.check_and_increment("key-a", limit=1)
    allowed_b, _ = backend.check_and_increment("key-b", limit=1)
    assert allowed_a is True
    assert allowed_b is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_rate_key_format():
    key = _rate_key("user@example.com", "health_check")
    parts = key.split(":")
    assert parts[0] == "rate"
    assert parts[1] == "user@example.com"
    assert parts[2] == "health_check"
    assert len(parts) == 4


def test_get_rate_limit_known_tool():
    limit = get_rate_limit_for_tool("rollback_execute")
    assert isinstance(limit, int)
    assert limit > 0


def test_get_rate_limit_unknown_tool_returns_fallback():
    limit = get_rate_limit_for_tool("nonexistent_xyz")
    assert isinstance(limit, int)
    assert limit > 0
