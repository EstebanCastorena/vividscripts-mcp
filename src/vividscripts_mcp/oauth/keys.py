"""Mock RSA signing key for Phase 1 token minting.

Phase 1's ``/oauth/token`` mints locally-signed JWTs because there's no
Cognito wired in yet. The key lives in-process, rotates on every server
restart, and is exposed via ``get_signing_key().public_jwk`` so the
Bearer validator (KAN-52) can publish a JWKS document.

**Out of scope for Phase 1:** durable keys, key rotation policy, HSM
backing. Phase 3 (KAN-31) removes this module — Cognito does the
signing, and the Bearer validator fetches Cognito's JWKS over HTTPS.
"""

from __future__ import annotations

import base64
import threading
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

#: Key identifier exposed in the JWT header + JWKS document. Phase 1 has
#: only one key, but the ``kid`` is required so the validator can find the
#: right key when Phase 3 rotation adds more.
KID = "vividscripts-mcp-phase1"

#: Signing algorithm. Aligned with KAN-48's PRM
#: ``resource_signing_alg_values_supported`` and the security AC requiring
#: explicit ``algorithms=["RS256"]`` at the validator.
ALGORITHM = "RS256"


@dataclass(frozen=True)
class MockSigningKey:
    """A snapshot of the in-process keypair, plus the public JWK form."""

    private_pem: bytes
    public_pem: bytes
    public_jwk: dict[str, Any]


_lock = threading.Lock()
_key: MockSigningKey | None = None


def _generate() -> MockSigningKey:
    """Generate a fresh RSA-2048 keypair and compute its public JWK."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key()

    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    numbers = public.public_numbers()
    n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    public_jwk: dict[str, Any] = {
        "kty": "RSA",
        "kid": KID,
        "alg": ALGORITHM,
        "use": "sig",
        "n": base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode("ascii"),
        "e": base64.urlsafe_b64encode(e_bytes).rstrip(b"=").decode("ascii"),
    }

    return MockSigningKey(
        private_pem=private_pem,
        public_pem=public_pem,
        public_jwk=public_jwk,
    )


def get_signing_key() -> MockSigningKey:
    """Return the process-wide signing key, generating it on first use."""
    global _key
    with _lock:
        if _key is None:
            _key = _generate()
        return _key


def reset_signing_key() -> None:
    """For tests: force a fresh keypair on next ``get_signing_key()``."""
    global _key
    with _lock:
        _key = None
