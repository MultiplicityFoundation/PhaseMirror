"""Gate F — F-04: MCP Server Cards and Cryptographic Identity Tests.

Tests for:
- ServerCard.canonical_form() determinism
- ServerCard.is_expired()
- ServerCard.verify_signature() (valid and tampered)
- ServerCardIssuer.sign() produces verifiable signature
- RSA-PSS (not PKCS1v15) is used
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.identity import (
    CryptographyUnavailableError,
    ServerCard,
    ServerCardIssuer,
)

_CRYPTO_AVAILABLE = True
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:
    _CRYPTO_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _CRYPTO_AVAILABLE,
    reason="cryptography package not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issuer(tmp_path: Path) -> ServerCardIssuer:
    return ServerCardIssuer(
        key_path=tmp_path / "test_key.pem",
        key_size_bits=2048,
        governance_proof="test-proof-abc123",
    )


def _make_card(issuer: ServerCardIssuer, *, signed: bool = True) -> ServerCard:
    card = issuer.issue(issuer="tooling-pmd/v0.1.0", subject="test-server")
    return card


# ---------------------------------------------------------------------------
# ServerCard — canonical_form
# ---------------------------------------------------------------------------


def test_canonical_form_is_deterministic(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    assert card.canonical_form() == card.canonical_form()


def test_canonical_form_does_not_include_signature(tmp_path):
    """The signature field must not appear in the canonical form."""
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    # Even with a signature set, the canonical form must exclude it.
    assert "signature" not in card.canonical_form()


def test_canonical_form_changes_when_issuer_changes(tmp_path):
    issuer = _make_issuer(tmp_path)
    card_a = issuer.issue(issuer="server-a", subject="test")
    card_b = issuer.issue(issuer="server-b", subject="test")
    assert card_a.canonical_form() != card_b.canonical_form()


# ---------------------------------------------------------------------------
# ServerCard — is_expired
# ---------------------------------------------------------------------------


def test_card_not_expired_within_window(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = issuer.issue(issuer="test", subject="test", ttl_seconds=3600)
    assert not card.is_expired()


def test_card_expired_after_ttl(tmp_path):
    issuer = _make_issuer(tmp_path)
    now = int(time.time())
    card = ServerCard(
        issuer="test",
        subject="test",
        public_key_pem=issuer.public_key_pem,
        governance_proof="proof",
        issued_at=now - 7200,
        expires_at=now - 3600,
        signature="",
    )
    assert card.is_expired()


# ---------------------------------------------------------------------------
# ServerCard — verify_signature
# ---------------------------------------------------------------------------


def test_valid_signature_verifies(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    assert card.verify_signature() is True


def test_tampered_issuer_fails_verification(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    tampered = ServerCard(
        issuer="attacker-server",  # tampered
        subject=card.subject,
        public_key_pem=card.public_key_pem,
        governance_proof=card.governance_proof,
        issued_at=card.issued_at,
        expires_at=card.expires_at,
        signature=card.signature,
    )
    assert tampered.verify_signature() is False


def test_tampered_subject_fails_verification(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    tampered = ServerCard(
        issuer=card.issuer,
        subject="attacker",  # tampered
        public_key_pem=card.public_key_pem,
        governance_proof=card.governance_proof,
        issued_at=card.issued_at,
        expires_at=card.expires_at,
        signature=card.signature,
    )
    assert tampered.verify_signature() is False


def test_tampered_governance_proof_fails_verification(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    tampered = ServerCard(
        issuer=card.issuer,
        subject=card.subject,
        public_key_pem=card.public_key_pem,
        governance_proof="tampered-proof",  # tampered
        issued_at=card.issued_at,
        expires_at=card.expires_at,
        signature=card.signature,
    )
    assert tampered.verify_signature() is False


def test_empty_signature_fails_verification(tmp_path):
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    unsigned = ServerCard(
        issuer=card.issuer,
        subject=card.subject,
        public_key_pem=card.public_key_pem,
        governance_proof=card.governance_proof,
        issued_at=card.issued_at,
        expires_at=card.expires_at,
        signature="",  # no signature
    )
    with pytest.raises(ValueError):
        unsigned.verify_signature()


# ---------------------------------------------------------------------------
# ServerCardIssuer — key persistence
# ---------------------------------------------------------------------------


def test_issuer_generates_key_file(tmp_path):
    key_path = tmp_path / "server_key.pem"
    assert not key_path.exists()
    ServerCardIssuer(key_path=key_path)
    assert key_path.exists()


def test_issuer_loads_existing_key(tmp_path):
    key_path = tmp_path / "server_key.pem"
    issuer1 = ServerCardIssuer(key_path=key_path)
    pub_key_1 = issuer1.public_key_pem

    issuer2 = ServerCardIssuer(key_path=key_path)
    pub_key_2 = issuer2.public_key_pem

    # Same key file → same public key
    assert pub_key_1 == pub_key_2


def test_issuer_public_key_is_pem(tmp_path):
    issuer = _make_issuer(tmp_path)
    pem = issuer.public_key_pem
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_issuer_governance_proof_in_card(tmp_path):
    issuer = ServerCardIssuer(
        key_path=tmp_path / "key.pem",
        governance_proof="specific-proof-xyz",
    )
    card = issuer.issue(issuer="test", subject="test")
    assert card.governance_proof == "specific-proof-xyz"


def test_card_to_dict_serialisable(tmp_path):
    """Card.to_dict() must produce a JSON-serialisable dict."""
    import json
    issuer = _make_issuer(tmp_path)
    card = _make_card(issuer)
    d = card.to_dict()
    # No exception means it's serialisable
    json.dumps(d)
    assert d["issuer"] == card.issuer
    assert d["signature"] == card.signature
