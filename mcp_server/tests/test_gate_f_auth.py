"""Gate F — F-02: OAuth 2.1 Authentication Middleware Tests.

Tests for:
- OAuthToken dataclass (expiry, scope)
- REQUIRED_SCOPES_BY_TOOL completeness
- require_auth() scope enforcement
- require_auth() expiry enforcement
- verify_pkce_challenge() PKCE S256 verification
- ReplayDetector JTI tracking
"""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.middleware.auth import (
    AuthorizationError,
    OAuthToken,
    REQUIRED_SCOPES_BY_TOOL,
    ReplayDetector,
    require_auth,
    verify_pkce_challenge,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    *,
    subject: str = "test-subject",
    scopes: frozenset = frozenset({"pmd:read"}),
    ttl: int = 3600,
    jti: str = "test-jti-001",
) -> OAuthToken:
    now = int(time.time())
    return OAuthToken(
        subject=subject,
        scopes=scopes,
        issued_at=now,
        expires_at=now + ttl,
        jti=jti,
    )


def _expired_token() -> OAuthToken:
    past = int(time.time()) - 3600
    return OAuthToken(
        subject="expired",
        scopes=frozenset({"pmd:read"}),
        issued_at=past - 3600,
        expires_at=past,
        jti="expired-jti",
    )


# ---------------------------------------------------------------------------
# OAuthToken — basic properties
# ---------------------------------------------------------------------------


def test_token_not_expired_within_window():
    token = _make_token(ttl=3600)
    assert not token.is_expired()


def test_token_expired_after_window():
    token = _expired_token()
    assert token.is_expired()


def test_token_has_scope_returns_true_when_subset():
    token = _make_token(scopes=frozenset({"pmd:read", "pmd:write"}))
    assert token.has_scope(frozenset({"pmd:read"}))
    assert token.has_scope(frozenset({"pmd:write"}))
    assert token.has_scope(frozenset({"pmd:read", "pmd:write"}))


def test_token_has_scope_returns_false_when_missing():
    token = _make_token(scopes=frozenset({"pmd:read"}))
    assert not token.has_scope(frozenset({"pmd:write"}))
    assert not token.has_scope(frozenset({"pmd:admin"}))


def test_token_is_frozen():
    """OAuthToken must be immutable (frozen=True)."""
    token = _make_token()
    with pytest.raises(Exception):
        token.subject = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# REQUIRED_SCOPES_BY_TOOL — completeness
# ---------------------------------------------------------------------------


def test_required_scopes_covers_all_registered_tools():
    """Every tool in tool_registry.yaml must have an entry in REQUIRED_SCOPES_BY_TOOL."""
    import yaml

    registry_path = Path(__file__).resolve().parents[1] / "tool_registry.yaml"
    with open(registry_path) as f:
        registry = yaml.safe_load(f)

    tool_names = {t["name"] for t in registry.get("tools", []) if isinstance(t, dict)}
    missing = tool_names - set(REQUIRED_SCOPES_BY_TOOL.keys())
    assert not missing, f"Tools not in REQUIRED_SCOPES_BY_TOOL: {missing}"


def test_required_scopes_all_use_pmd_namespace():
    """All scope strings must start with 'pmd:' (Gate F namespace convention)."""
    for tool, scopes in REQUIRED_SCOPES_BY_TOOL.items():
        for scope in scopes:
            assert scope.startswith("pmd:"), (
                f"Tool '{tool}' has non-pmd scope: '{scope}'"
            )


def test_required_scopes_write_tools_require_write_scope():
    """Write tools must require pmd:write, not just pmd:read."""
    write_tools = {"checkpoint_write", "checkpoint_prune", "rollback_execute"}
    for tool in write_tools:
        if tool in REQUIRED_SCOPES_BY_TOOL:
            assert "pmd:write" in REQUIRED_SCOPES_BY_TOOL[tool], (
                f"Write tool '{tool}' should require pmd:write"
            )


def test_required_scopes_phase_mirror_has_own_scope():
    """phase_mirror must require pmd:phase_mirror, not just pmd:read."""
    assert "pmd:phase_mirror" in REQUIRED_SCOPES_BY_TOOL.get("phase_mirror", frozenset())


# ---------------------------------------------------------------------------
# require_auth — scope enforcement
# ---------------------------------------------------------------------------


def test_require_auth_passes_with_correct_scope():
    token = _make_token(scopes=frozenset({"pmd:read"}))
    require_auth("health_check", token)  # must not raise


def test_require_auth_raises_when_scope_missing():
    token = _make_token(scopes=frozenset({"pmd:read"}))
    with pytest.raises(AuthorizationError, match="scopes"):
        require_auth("rollback_execute", token)


def test_require_auth_raises_when_token_expired():
    token = _expired_token()
    with pytest.raises(AuthorizationError, match="expired"):
        require_auth("health_check", token)


def test_require_auth_admin_scope_bypasses_all_tool_checks():
    """pmd:admin token must be authorized for any tool."""
    admin_token = _make_token(scopes=frozenset({"pmd:admin"}))
    for tool_name in REQUIRED_SCOPES_BY_TOOL:
        require_auth(tool_name, admin_token)  # must not raise


def test_require_auth_raises_for_unknown_tool():
    """An unregistered tool must raise AuthorizationError."""
    token = _make_token(scopes=frozenset({"pmd:admin"}))
    # Admin bypasses scope check; an unknown tool without admin should fail.
    read_token = _make_token(scopes=frozenset({"pmd:read"}))
    with pytest.raises(AuthorizationError):
        require_auth("nonexistent_tool_xyz", read_token)


def test_require_auth_write_tools_require_write_scope():
    """Tokens with only pmd:read must be rejected for write tools."""
    read_only_token = _make_token(scopes=frozenset({"pmd:read"}))
    for write_tool in ("checkpoint_write", "checkpoint_prune", "rollback_execute"):
        if write_tool in REQUIRED_SCOPES_BY_TOOL:
            with pytest.raises(AuthorizationError):
                require_auth(write_tool, read_only_token)


def test_require_auth_error_message_includes_tool_name():
    """AuthorizationError message must name the tool for debugging."""
    token = _make_token(scopes=frozenset({"pmd:read"}))
    with pytest.raises(AuthorizationError) as exc_info:
        require_auth("rollback_execute", token)
    assert "rollback_execute" in str(exc_info.value)


def test_require_auth_error_message_includes_subject():
    """Expiry error must include the token's subject for audit tracing."""
    token = _expired_token()
    with pytest.raises(AuthorizationError) as exc_info:
        require_auth("health_check", token)
    assert token.subject in str(exc_info.value)


# ---------------------------------------------------------------------------
# verify_pkce_challenge — PKCE S256
# ---------------------------------------------------------------------------


def test_pkce_valid_challenge():
    """verify_pkce_challenge must return True for a valid S256 pair."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert verify_pkce_challenge(verifier, challenge)


def test_pkce_invalid_challenge_returns_false():
    """verify_pkce_challenge must return False for a mismatched pair."""
    verifier = "some-verifier"
    wrong_challenge = "completely-wrong-value"
    assert not verify_pkce_challenge(verifier, wrong_challenge)


def test_pkce_tampered_verifier_rejected():
    """Changing one character in the verifier must invalidate the challenge."""
    verifier = "correct-verifier-value-123456789"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    tampered = verifier[:-1] + ("X" if verifier[-1] != "X" else "Y")
    assert not verify_pkce_challenge(tampered, challenge)


def test_pkce_is_url_safe_base64():
    """The challenge must use URL-safe Base64 (no + or / characters)."""
    verifier = "url-safe-test-verifier-value-abcdef"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert "+" not in challenge
    assert "/" not in challenge


# ---------------------------------------------------------------------------
# ReplayDetector — JTI tracking
# ---------------------------------------------------------------------------


def test_replay_detector_first_use_not_replay():
    """First use of a JTI must not be detected as a replay."""
    detector = ReplayDetector()
    future = int(time.time()) + 3600
    assert not detector.is_replay("jti-001", future)


def test_replay_detector_second_use_is_replay():
    """Second use of the same JTI must be detected as a replay."""
    detector = ReplayDetector()
    future = int(time.time()) + 3600
    detector.is_replay("jti-002", future)
    assert detector.is_replay("jti-002", future)


def test_replay_detector_different_jtis_independent():
    """Different JTIs must be tracked independently."""
    detector = ReplayDetector()
    future = int(time.time()) + 3600
    detector.is_replay("jti-a", future)
    assert not detector.is_replay("jti-b", future)


def test_replay_detector_expired_jti_not_replay():
    """A JTI whose validity window has closed must not be considered a replay."""
    detector = ReplayDetector()
    past = int(time.time()) - 1  # already expired
    detector.is_replay("jti-expired", past)
    # After expiry the JTI is pruned; a second use should not be a replay.
    assert not detector.is_replay("jti-expired", int(time.time()) + 3600)


def test_replay_detector_prunes_expired_entries():
    """Expired entries must be pruned to bound memory use."""
    detector = ReplayDetector()
    past = int(time.time()) - 1
    for i in range(100):
        detector.is_replay(f"jti-{i}", past)
    # After pruning there should be no remaining expired entries
    detector._prune_expired()
    assert len(detector._seen) == 0
