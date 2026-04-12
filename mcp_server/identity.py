"""F-04: MCP Server Cards and Cryptographic Identity.

Implements RSA-PSS signed ``ServerCard`` objects that allow MCP clients to
verify the server's identity and governance posture.  The signature binds the
card payload to the operator's private key, which is the same key used during
the governance bootstrap ceremony (F-01).

Per Gate F ADR F-04:
  - Signing MUST use RSA-PSS with SHA-256 (not PKCS1-v1_5).
  - The ``governance_proof`` field carries the Merkle root ``tx_id`` from F-01,
    creating a cryptographic chain between the running server and its trust root.
  - Public key is stored in PEM format and published via ``/.well-known/mcp-card.json``.

The ``cryptography`` package (>=41) is required.  If it is absent, all signing
and verification operations raise ``CryptographyUnavailableError`` so the rest
of the server can degrade gracefully rather than crashing at import time.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CRYPTO_AVAILABLE = False


class CryptographyUnavailableError(RuntimeError):
    """Raised when the ``cryptography`` package is not installed."""


DEFAULT_KEY_PATH = Path(__file__).resolve().parent.parent / "state" / "mcp_server_key.pem"
DEFAULT_CARD_TTL_SECONDS = 86_400  # 24 hours


# ---------------------------------------------------------------------------
# ServerCard dataclass
# ---------------------------------------------------------------------------


@dataclass
class ServerCard:
    """Cryptographically signed server identity card.

    The canonical payload used for signing is produced by
    :meth:`canonical_form`.  The ``signature`` field is the hex-encoded
    RSA-PSS signature over ``canonical_form().encode("utf-8")``.
    """

    issuer: str
    subject: str
    public_key_pem: str        # PEM-encoded RSA public key
    governance_proof: str      # Merkle root tx_id from F-01 (hex or int as str)
    issued_at: int             # Unix timestamp
    expires_at: int            # Unix timestamp
    signature: str = ""        # Hex-encoded RSA-PSS signature (empty before signing)

    def canonical_form(self) -> str:
        """Return the deterministic JSON string that is signed.

        The signature covers all fields except ``signature`` itself.
        """
        payload = {
            "issuer": self.issuer,
            "subject": self.subject,
            "public_key_pem": self.public_key_pem,
            "governance_proof": self.governance_proof,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def is_expired(self) -> bool:
        """Return True if the card's validity window has closed."""
        return time.time() > self.expires_at

    def verify_signature(self) -> bool:
        """Verify the RSA-PSS signature against the canonical payload.

        Returns:
            True if the signature is valid, False otherwise.

        Raises:
            CryptographyUnavailableError: if ``cryptography`` is not installed.
            ValueError: if ``public_key_pem`` or ``signature`` are malformed.
        """
        if not _CRYPTO_AVAILABLE:
            raise CryptographyUnavailableError(
                "The 'cryptography' package is required for signature verification. "
                "Install it with: pip install cryptography"
            )
        try:
            public_key = serialization.load_pem_public_key(self.public_key_pem.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid public_key_pem: {exc}") from exc

        try:
            sig_bytes = bytes.fromhex(self.signature)
        except ValueError as exc:
            raise ValueError(f"Invalid signature hex: {exc}") from exc

        if not sig_bytes:
            raise ValueError("signature is empty; card has not been signed")

        payload_bytes = self.canonical_form().encode("utf-8")
        try:
            public_key.verify(  # type: ignore[union-attr]
                sig_bytes,
                payload_bytes,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except InvalidSignature:
            return False

    def to_dict(self) -> dict:
        """Serialize the card to a JSON-safe dictionary."""
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "public_key_pem": self.public_key_pem,
            "governance_proof": self.governance_proof,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "signature": self.signature,
        }


# ---------------------------------------------------------------------------
# ServerCardIssuer
# ---------------------------------------------------------------------------


class ServerCardIssuer:
    """Issues and verifies RSA-PSS signed ServerCards.

    The issuer loads (or generates) an RSA key pair from *key_path*.  The
    private key is used during :meth:`sign`; the public key is embedded in
    every issued card.
    """

    def __init__(
        self,
        *,
        key_path: Path | None = None,
        key_size_bits: int = 2048,
        governance_proof: str = "0",
    ) -> None:
        if not _CRYPTO_AVAILABLE:
            raise CryptographyUnavailableError(
                "The 'cryptography' package is required for ServerCardIssuer. "
                "Install it with: pip install cryptography"
            )
        self.key_path = key_path or DEFAULT_KEY_PATH
        self.key_size_bits = key_size_bits
        self.governance_proof = governance_proof
        self._private_key = self._load_or_generate_key()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue(
        self,
        *,
        issuer: str,
        subject: str,
        ttl_seconds: int = DEFAULT_CARD_TTL_SECONDS,
    ) -> ServerCard:
        """Create and sign a new ServerCard.

        Args:
            issuer: Identity of the signer (e.g. ``"tooling-pmd/v0.1.0"``).
            subject: Identity of the server instance (e.g. hostname).
            ttl_seconds: Card validity window in seconds.

        Returns:
            A signed ``ServerCard``.
        """
        now = int(time.time())
        card = ServerCard(
            issuer=issuer,
            subject=subject,
            public_key_pem=self.public_key_pem,
            governance_proof=self.governance_proof,
            issued_at=now,
            expires_at=now + ttl_seconds,
        )
        return self.sign(card)

    def sign(self, card: ServerCard) -> ServerCard:
        """Sign a ``ServerCard`` and return a new instance with the signature set.

        Raises:
            CryptographyUnavailableError: if ``cryptography`` is not installed.
        """
        payload_bytes = card.canonical_form().encode("utf-8")
        sig_bytes = self._private_key.sign(
            payload_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return ServerCard(
            issuer=card.issuer,
            subject=card.subject,
            public_key_pem=card.public_key_pem,
            governance_proof=card.governance_proof,
            issued_at=card.issued_at,
            expires_at=card.expires_at,
            signature=sig_bytes.hex(),
        )

    @property
    def public_key_pem(self) -> str:
        """PEM-encoded public key (safe to publish)."""
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def _load_or_generate_key(self):  # type: ignore[return]
        """Load the private key from *key_path* or generate and persist a new one."""
        if self.key_path.exists():
            return self._load_key(self.key_path)
        return self._generate_and_save_key(self.key_path)

    def _load_key(self, path: Path):  # type: ignore[return]
        pem_bytes = path.read_bytes()
        return serialization.load_pem_private_key(pem_bytes, password=None)

    def _generate_and_save_key(self, path: Path):  # type: ignore[return]
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=self.key_size_bits,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.write_bytes(pem_bytes)
        # Restrict permissions so only the owning process can read the key.
        path.chmod(0o600)
        return private_key
