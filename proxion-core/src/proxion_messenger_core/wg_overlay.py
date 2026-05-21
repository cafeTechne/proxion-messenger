"""WireGuard overlay state manager (pure data layer — no OS calls).

Manages local WireGuard identity and peer state in LocalStore. Generates
proper Curve25519 keypairs using the ``cryptography`` library (same curve
WireGuard uses). Actual WG interface configuration is deferred to a future
OS-integration layer; this module only tracks state.

Usage
-----
    manager = WgOverlayManager(store)
    identity = manager.ensure_local_identity()
    manager.upsert_peer("did:web:bob.example", pub_b64, "1.2.3.4:51820", "10.0.0.2/32")
    manager.update_path_mode("did:web:bob.example", "direct", reason="handshake_ok")
    direct_peers = manager.get_peers_by_mode("direct")
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore


def generate_wg_keypair() -> tuple[str, str]:
    """Generate a WireGuard-compatible Curve25519 keypair.

    Returns
    -------
    (privkey_b64, pubkey_b64)
        Both keys are 32-byte Curve25519 keys, base64-encoded (standard, not URL-safe).
        Private key is the raw scalar; public key is its Curve25519 base-point multiple.
    """
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption
    )
    priv_key = X25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    priv_b64 = base64.b64encode(
        priv_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ).decode()
    pub_b64 = base64.b64encode(
        pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    return priv_b64, pub_b64


class WgOverlayManager:
    """Thin facade over LocalStore's WG overlay tables."""

    def __init__(self, store: "LocalStore") -> None:
        self._store = store

    def ensure_local_identity(self) -> dict:
        """Return the existing WG identity, or generate + save a new one."""
        existing = self._store.get_wg_local_identity()
        if existing:
            return existing
        priv_b64, pub_b64 = generate_wg_keypair()
        self._store.save_wg_local_identity(pub_b64, priv_b64)
        return self._store.get_wg_local_identity() or {
            "pubkey_b64": pub_b64,
            "priv_wrapped_b64": priv_b64,
        }

    def get_local_identity(self) -> dict | None:
        return self._store.get_wg_local_identity()

    def upsert_peer(
        self,
        peer_webid: str,
        pubkey_b64: str,
        endpoint_hint: str | None,
        allowed_ips: str,
        path_mode: str = "unknown",
    ) -> None:
        self._store.upsert_wg_peer(peer_webid, pubkey_b64, endpoint_hint, allowed_ips, path_mode)

    def update_path_mode(
        self,
        peer_webid: str,
        new_mode: str,
        reason: str = "",
        last_handshake_at: float | None = None,
    ) -> None:
        peer = self._store.get_wg_peer(peer_webid)
        old_mode = peer["path_mode"] if peer else None
        self._store.update_wg_peer_path_mode(peer_webid, new_mode, last_handshake_at)
        if old_mode != new_mode:
            self._store.log_wg_connectivity_event(peer_webid, old_mode, new_mode, reason)

    def get_peers_by_mode(self, mode: str) -> list[dict]:
        return self._store.get_wg_peers_by_mode(mode)

    def get_peer(self, peer_webid: str) -> dict | None:
        return self._store.get_wg_peer(peer_webid)
