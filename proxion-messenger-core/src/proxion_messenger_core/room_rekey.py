"""R17: Room key rotation on member removal.

When a member is kicked or leaves a room, a new symmetric room key is generated
and sealed to each remaining member's public key. This prevents the removed member
from decrypting future room messages.

Events emitted:
  - room_rekey_required   (info, to trigger client re-encryption)
  - room_key_update       (info, carries new key sealed to each recipient)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore

logger = logging.getLogger(__name__)

_ROOM_KEY_LEN = 32  # AES-256


def generate_room_key() -> bytes:
    """Generate a fresh 32-byte symmetric room key."""
    return os.urandom(_ROOM_KEY_LEN)


def seal_room_key_for_member(room_key: bytes, member_x25519_pub_bytes: bytes) -> str:
    """Seal room_key for a single member using X25519 ECDH + AES-256-GCM.

    Returns base64-encoded sealed blob: ephemeral_pub (32) || nonce (12) || ciphertext.
    """
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes, serialization

    ephemeral = X25519PrivateKey.generate()
    eph_pub_bytes = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    recipient_pub = X25519PublicKey.from_public_bytes(member_x25519_pub_bytes)
    shared = ephemeral.exchange(recipient_pub)

    wrap_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"ProxionRoomRekeyV1"
    ).derive(shared)

    nonce = os.urandom(12)
    ciphertext = AESGCM(wrap_key).encrypt(nonce, room_key, b"ProxionRoomKey")

    blob = eph_pub_bytes + nonce + ciphertext
    return base64.b64encode(blob).decode()


def unseal_room_key(sealed_b64: str, member_x25519_priv_bytes: bytes) -> bytes:
    """Unseal a room key sealed by seal_room_key_for_member.

    Returns the raw 32-byte room key.
    """
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes

    blob = base64.b64decode(sealed_b64)
    eph_pub_bytes = blob[:32]
    nonce = blob[32:44]
    ciphertext = blob[44:]

    priv = X25519PrivateKey.from_private_bytes(member_x25519_priv_bytes)
    eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)
    shared = priv.exchange(eph_pub)

    wrap_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"ProxionRoomRekeyV1"
    ).derive(shared)

    return AESGCM(wrap_key).decrypt(nonce, ciphertext, b"ProxionRoomKey")


def rotate_room_key(
    room_id: str,
    remaining_member_webids: list[str],
    store: "LocalStore | None" = None,
) -> dict:
    """Generate a new room key and emit key-update events for remaining members.

    Parameters
    ----------
    room_id:
        The room whose key is being rotated.
    remaining_member_webids:
        WebIDs of members who should receive the new key.
    store:
        LocalStore for persisting security events and member X25519 pub keys.

    Returns
    -------
    dict with: room_id, new_key_b64 (base64 of new room key),
    sealed_keys (dict[webid → sealed_b64]), rekey_event_id, rotated_at.
    """
    new_key = generate_room_key()
    new_key_b64 = base64.b64encode(new_key).decode()
    rekey_event_id = f"rekey-{room_id[:8]}-{int(time.time())}"
    rotated_at = time.time()

    sealed_keys: dict[str, str] = {}
    if store:
        for webid in remaining_member_webids:
            try:
                pub_b64u = store.get_x25519_pub(webid)
                if pub_b64u:
                    import base64 as _b64
                    pub_bytes = _b64.urlsafe_b64decode(pub_b64u + "==")
                    sealed_keys[webid] = seal_room_key_for_member(new_key, pub_bytes)
            except Exception as exc:
                logger.warning("room_rekey: failed to seal key for %s: %s", webid, exc)

        try:
            store.save_security_event(
                "room_rekey_executed", "info",
                details=json.dumps({
                    "room_id": room_id,
                    "rekey_event_id": rekey_event_id,
                    "recipients": len(sealed_keys),
                    "rotated_at": rotated_at,
                }),
            )
        except Exception:
            pass

    result = {
        "room_id": room_id,
        "new_key_b64": new_key_b64,
        "sealed_keys": sealed_keys,
        "rekey_event_id": rekey_event_id,
        "rotated_at": rotated_at,
    }
    logger.info(
        "Room %s re-keyed for %d remaining members (event=%s)",
        room_id, len(remaining_member_webids), rekey_event_id,
    )
    return result


def build_room_key_update_event(rekey_result: dict) -> dict:
    """Build the room_key_update broadcast payload from a rotate_room_key result."""
    return {
        "type": "room_key_update",
        "room_id": rekey_result["room_id"],
        "rekey_event_id": rekey_result["rekey_event_id"],
        "sealed_keys": rekey_result["sealed_keys"],
        "rotated_at": rekey_result["rotated_at"],
    }
