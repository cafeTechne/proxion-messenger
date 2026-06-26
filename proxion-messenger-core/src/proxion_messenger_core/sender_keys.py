"""Sender Keys protocol for group E2E encryption.

Each room member maintains their own ``SenderKeyState`` — a symmetric chain
key that advances with each message they send.  Encryption is O(1); key
distribution to new members is O(existing_members).

On room join / member addition, each existing member distributes their current
sender key to the new member by sealing it with the new member's X25519 public
key (ECIES, same scheme as sealed_sender).  The new member's sender key is
distributed to all existing members in the same way.

On member kick / room re-key (already handled by room_rekey.py), all sender
keys for the room should be deleted (``delete_sender_keys_for_room``) and
fresh keys generated.

Wire format (group message payload extras):
    e2e_v      = 2          (signals encrypted room message)
    sender_id  = webid      (so receiver looks up correct sender key)
    iteration  = int        (which chain step to advance to)
    nonce_b64  = str
    ciphertext_b64 = str
"""
from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import serialization


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _hkdf(ikm: bytes, length: int, salt: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)


# ---------------------------------------------------------------------------
# Sender key state
# ---------------------------------------------------------------------------

def generate_sender_key(epoch: int = 1) -> dict:
    """Generate a fresh sender key state dict.

    Returns
    -------
    dict with keys: chain_key_b64 (32 bytes), iteration (0), epoch (int).
    """
    chain_key = os.urandom(32)
    return {
        "chain_key_b64": _b64e(chain_key),
        "iteration": 0,
        "epoch": epoch,
    }


def advance_sender_chain(chain_key: bytes) -> tuple[bytes, bytes]:
    """Advance sender chain key by one step.

    Returns
    -------
    (next_chain_key, msg_key) — both 32 bytes.
    """
    msg_key = _hkdf(chain_key, 32, salt=b"\x01", info=b"ProxionSenderMsgKey")
    next_chain_key = _hkdf(chain_key, 32, salt=b"\x02", info=b"ProxionSenderChainNext")
    return next_chain_key, msg_key


# ---------------------------------------------------------------------------
# Encryption / Decryption
# ---------------------------------------------------------------------------

def encrypt_group_message(
    sender_key_state: dict,
    plaintext: str,
    sender_webid: str,
) -> tuple[dict, dict]:
    """Encrypt a room message using the sender's current chain key.

    Returns
    -------
    (updated_sender_key_state, payload_dict)
        payload_dict keys: e2e_v, sender_id, iteration, nonce_b64, ciphertext_b64.
    """
    chain_key = _b64d(sender_key_state["chain_key_b64"])
    iteration = sender_key_state["iteration"]

    next_chain_key, msg_key = advance_sender_chain(chain_key)
    nonce = iteration.to_bytes(12, "big")
    ciphertext = AESGCM(msg_key).encrypt(nonce, plaintext.encode("utf-8"), b"")

    updated_state = {
        "chain_key_b64": _b64e(next_chain_key),
        "iteration": iteration + 1,
    }
    payload = {
        "e2e_v": 2,
        "sender_id": sender_webid,
        "iteration": iteration,
        "sender_epoch": sender_key_state.get("epoch", 1),
        "nonce_b64": _b64e(nonce),
        "ciphertext_b64": _b64e(ciphertext),
    }
    return updated_state, payload


def decrypt_group_message(
    sender_key_state: dict,
    payload: dict,
) -> tuple[dict, str]:
    """Decrypt a room message using the sender's chain key at the given iteration.

    Returns
    -------
    (updated_sender_key_state, plaintext)

    Raises
    ------
    cryptography.exceptions.InvalidTag
        If authentication fails.
    ValueError
        If payload is malformed or iteration is behind current state.
    """
    chain_key = _b64d(sender_key_state["chain_key_b64"])
    current_iteration = sender_key_state["iteration"]
    target_iteration = payload["iteration"]

    state_epoch = sender_key_state.get("epoch", 1)
    payload_epoch = payload.get("sender_epoch", 1)
    if payload_epoch < state_epoch:
        raise ValueError(
            f"sender_key_epoch_stale: payload epoch {payload_epoch} < state epoch {state_epoch}"
        )

    if target_iteration < current_iteration:
        raise ValueError(
            f"Received iteration {target_iteration} is behind current {current_iteration}"
        )

    # Advance chain to reach target iteration
    for _ in range(target_iteration - current_iteration):
        chain_key, _ = advance_sender_chain(chain_key)

    next_chain_key, msg_key = advance_sender_chain(chain_key)
    nonce = _b64d(payload["nonce_b64"])
    ciphertext = _b64d(payload["ciphertext_b64"])
    plaintext_bytes = AESGCM(msg_key).decrypt(nonce, ciphertext, b"")

    updated_state = {
        "chain_key_b64": _b64e(next_chain_key),
        "iteration": target_iteration + 1,
    }
    return updated_state, plaintext_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Key distribution (ECIES)
# ---------------------------------------------------------------------------

def _ecies_seal(plaintext_bytes: bytes, recipient_pub_bytes: bytes) -> bytes:
    """ECIES: X25519 ECDH + AES-256-GCM. Returns eph_pub(32)||nonce(12)||ciphertext."""
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    dh_out = eph.exchange(X25519PublicKey.from_public_bytes(recipient_pub_bytes))
    aes_key = _hkdf(dh_out, 32, salt=eph_pub, info=b"ProxionSenderKeyDist")
    nonce = os.urandom(12)
    ct = AESGCM(aes_key).encrypt(nonce, plaintext_bytes, eph_pub)
    return eph_pub + nonce + ct


def _ecies_unseal(sealed: bytes, recipient_priv_bytes: bytes) -> bytes:
    """ECIES unseal. Inverse of _ecies_seal."""
    if len(sealed) < 44:
        raise ValueError("sealed too short")
    eph_pub_bytes = sealed[:32]
    nonce = sealed[32:44]
    ct = sealed[44:]
    priv = X25519PrivateKey.from_private_bytes(recipient_priv_bytes)
    dh_out = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub_bytes))
    aes_key = _hkdf(dh_out, 32, salt=eph_pub_bytes, info=b"ProxionSenderKeyDist")
    return AESGCM(aes_key).decrypt(nonce, ct, eph_pub_bytes)


def distribute_sender_key(
    sender_key_state: dict,
    member_pubkeys: dict,
) -> dict:
    """Seal the sender key for each member.

    Parameters
    ----------
    sender_key_state:
        dict with chain_key_b64 and iteration.
    member_pubkeys:
        ``{webid: raw_x25519_pub_bytes}``

    Returns
    -------
    ``{webid: base64_sealed_bytes}`` — one entry per recipient.
    """
    import json as _json
    payload_bytes = _json.dumps(sender_key_state).encode("utf-8")
    result: dict = {}
    for webid, pub_bytes in member_pubkeys.items():
        sealed = _ecies_seal(payload_bytes, pub_bytes)
        result[webid] = _b64e(sealed)
    return result


def receive_sender_key(sealed_b64: str, recipient_priv_bytes: bytes) -> dict:
    """Unseal a sender key distribution packet.

    Parameters
    ----------
    sealed_b64:
        Base64-encoded sealed bytes (as produced by distribute_sender_key).
    recipient_priv_bytes:
        Recipient's raw X25519 private key bytes.

    Returns
    -------
    dict with chain_key_b64 and iteration.
    """
    import json as _json
    raw = _ecies_unseal(_b64d(sealed_b64), recipient_priv_bytes)
    return _json.loads(raw)
