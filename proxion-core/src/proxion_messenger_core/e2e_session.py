"""X3DH-style initial key agreement + double-ratchet sending/receiving chain for DM forward secrecy.

Protocol overview
-----------------
Alice (initiator) performs X3DH against Bob's published prekey bundle:

    DH1 = DH(IK_A→X25519, SPK_B)
    DH2 = DH(EK_A,        IK_B→X25519)
    DH3 = DH(EK_A,        SPK_B)
    DH4 = DH(EK_A,        OPK_B)   # only when a one-time prekey is used

    master_secret = HKDF-SHA256(
        b"\\xff"*32 || DH1 || DH2 || DH3 [|| DH4],
        info=b"ProxionX3DHv1", length=64
    )
    root_key       = master_secret[:32]
    send_chain_key = master_secret[32:]   # Alice sends, Bob receives
    recv_chain_key = root_key             # Alice receives (Bob's send chain)

Bob derives the same values with the inverse roles:
    send_chain_key = master_secret[32:]   # Bob's recv chain becomes his send chain
    recv_chain_key = root_key

Each message advances the sender's chain with:
    msg_key        = HKDF(chain_key, salt=b"\\x01", info=b"ProxionMsgKey",   length=32)
    next_chain_key = HKDF(chain_key, salt=b"\\x02", info=b"ProxionChainNext", length=32)

Messages are encrypted with AES-256-GCM; the nonce is the 12-byte big-endian
message counter, ensuring uniqueness within a session/direction.
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hkdf(ikm: bytes, length: int, salt: bytes, info: bytes) -> bytes:
    """Single-step HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)


def _b64enc(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s)


def _x25519_from_ed25519_priv(ed25519_key) -> X25519PrivateKey:
    """Derive an X25519 private key from an Ed25519 private key.

    Both key types share the same 32-byte seed format; the curve operations
    differ but the raw bytes are compatible for this seed-reuse technique.
    This is the same approach used by libsodium's ``crypto_sign_ed25519_sk_to_curve25519``.
    """
    raw_seed = ed25519_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return X25519PrivateKey.from_private_bytes(raw_seed[:32])


def _x25519_pub_from_ed25519_pub(ed25519_pub_bytes: bytes) -> X25519PublicKey:
    """Load an X25519 public key from raw Ed25519 public key bytes.

    For the seed-reuse technique the public keys are NOT interchangeable —
    they are on different curves.  However, in this simplified protocol both
    sides agree to use the *same 32-byte seed* for their X25519 identity key
    (derived from the Ed25519 private seed on their own device).  The remote
    peer therefore publishes the raw X25519 public key bytes derived from
    that seed; we treat ``ed25519_pub_bytes`` as those raw X25519 bytes.
    """
    return X25519PublicKey.from_public_bytes(ed25519_pub_bytes)


# ---------------------------------------------------------------------------
# Chain ratchet
# ---------------------------------------------------------------------------

def advance_chain(chain_key: bytes) -> tuple[bytes, bytes]:
    """Advance the chain key by one step.

    Parameters
    ----------
    chain_key:
        Current 32-byte chain key.

    Returns
    -------
    (next_chain_key, msg_key) — both 32 bytes.
    """
    msg_key = _hkdf(chain_key, length=32, salt=b"\x01", info=b"ProxionMsgKey")
    next_chain_key = _hkdf(chain_key, length=32, salt=b"\x02", info=b"ProxionChainNext")
    return next_chain_key, msg_key


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """Mutable state for one X3DH-bootstrapped DM session."""

    session_id: str
    peer_webid: str
    owner_webid: str
    root_key: bytes         # 32 bytes
    send_chain_key: bytes   # 32 bytes
    recv_chain_key: bytes   # 32 bytes
    send_count: int = 0
    recv_count: int = 0


# ---------------------------------------------------------------------------
# Prekey bundle generation
# ---------------------------------------------------------------------------

def generate_prekey_bundle(owner_webid: str, num_one_time_prekeys: int = 5) -> dict:
    """Generate a fresh prekey bundle for publishing.

    The private halves of ``signed_prekey`` and ``one_time_prekeys`` must be
    stored securely by the caller (e.g. in ``local_store`` keyed by their IDs).

    Returns
    -------
    dict with keys:
        owner_webid             str
        signed_prekey_id        int   — random non-negative integer ID
        signed_prekey_pub_b64   str   — raw X25519 public key, base64
        signed_prekey_priv_b64  str   — raw X25519 private key, base64 (store & protect!)
        one_time_prekeys        list  — each element: {id, pub_b64, priv_b64}
    """
    spk = X25519PrivateKey.generate()
    spk_pub_bytes = spk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    spk_priv_bytes = spk.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )

    one_time_prekeys: list[dict] = []
    for _ in range(num_one_time_prekeys):
        opk = X25519PrivateKey.generate()
        opk_pub = opk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        opk_priv = opk.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        one_time_prekeys.append({
            "id": secrets.randbelow(2 ** 31),
            "pub_b64": _b64enc(opk_pub),
            "priv_b64": _b64enc(opk_priv),
        })

    return {
        "owner_webid": owner_webid,
        "signed_prekey_id": secrets.randbelow(2 ** 31),
        "signed_prekey_pub_b64": _b64enc(spk_pub_bytes),
        "signed_prekey_priv_b64": _b64enc(spk_priv_bytes),
        "one_time_prekeys": one_time_prekeys,
    }


# ---------------------------------------------------------------------------
# X3DH key agreement
# ---------------------------------------------------------------------------

def _x3dh_master_secret(
    dh1: bytes,
    dh2: bytes,
    dh3: bytes,
    dh4: bytes | None,
) -> bytes:
    """Combine DH outputs into a 64-byte master secret via HKDF-SHA256."""
    f = b"\xff" * 32
    ikm = f + dh1 + dh2 + dh3
    if dh4 is not None:
        ikm += dh4
    return _hkdf(ikm, length=64, salt=b"\x00" * 32, info=b"ProxionX3DHv1")


def init_outbound_session(
    alice_identity_key,
    alice_webid: str,
    bob_webid: str,
    bob_identity_pub_bytes: bytes,
    bob_signed_prekey_pub_b64: str,
    bob_one_time_prekey_pub_b64: str | None = None,
    bob_one_time_prekey_id: int | None = None,
) -> tuple[SessionState, dict]:
    """Alice initiates an X3DH session toward Bob.

    Parameters
    ----------
    alice_identity_key:
        Alice's Ed25519PrivateKey (used for X25519 conversion).
    alice_webid:
        Alice's WebID URI.
    bob_webid:
        Bob's WebID URI.
    bob_identity_pub_bytes:
        Bob's X25519 public key bytes (32 raw bytes, same-seed derived from Ed25519).
    bob_signed_prekey_pub_b64:
        Bob's signed prekey public key, base64-encoded raw X25519 bytes.
    bob_one_time_prekey_pub_b64:
        Bob's one-time prekey public key (optional), base64-encoded.
    bob_one_time_prekey_id:
        ID of the one-time prekey being consumed (included in header so Bob can
        look it up and delete it).

    Returns
    -------
    (session_state, header_dict)
        ``header_dict`` must be transmitted to Bob alongside the first message.
        It contains: session_id, ek_pub_b64, one_time_prekey_id (or None).
    """
    # Alice's X25519 identity key (converted from Ed25519 seed)
    ik_a = _x25519_from_ed25519_priv(alice_identity_key)

    # Generate Alice's ephemeral key pair
    ek_a = X25519PrivateKey.generate()
    ek_a_pub_bytes = ek_a.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    # Bob's public keys
    ik_b_pub = X25519PublicKey.from_public_bytes(bob_identity_pub_bytes)
    spk_b_pub = X25519PublicKey.from_public_bytes(_b64dec(bob_signed_prekey_pub_b64))

    # DH computations
    dh1 = ik_a.exchange(spk_b_pub)
    dh2 = ek_a.exchange(ik_b_pub)
    dh3 = ek_a.exchange(spk_b_pub)
    dh4: bytes | None = None
    if bob_one_time_prekey_pub_b64 is not None:
        opk_b_pub = X25519PublicKey.from_public_bytes(_b64dec(bob_one_time_prekey_pub_b64))
        dh4 = ek_a.exchange(opk_b_pub)

    master = _x3dh_master_secret(dh1, dh2, dh3, dh4)
    root_key = master[:32]
    send_chain_key = master[32:]
    recv_chain_key = root_key  # Alice receives on the symmetric recv chain

    session_id = secrets.token_hex(16)
    state = SessionState(
        session_id=session_id,
        peer_webid=bob_webid,
        owner_webid=alice_webid,
        root_key=root_key,
        send_chain_key=send_chain_key,
        recv_chain_key=recv_chain_key,
    )

    header = {
        "session_id": session_id,
        "ek_pub_b64": _b64enc(ek_a_pub_bytes),
        "one_time_prekey_id": bob_one_time_prekey_id,
    }
    return state, header


def init_inbound_session(
    bob_identity_key,
    bob_webid: str,
    alice_webid: str,
    alice_identity_pub_bytes: bytes,
    header: dict,
    signed_prekey_priv_bytes: bytes,
    one_time_prekey_priv_bytes: bytes | None = None,
) -> SessionState:
    """Bob reconstructs Alice's X3DH session from her header.

    Parameters
    ----------
    bob_identity_key:
        Bob's Ed25519PrivateKey.
    bob_webid:
        Bob's WebID URI.
    alice_webid:
        Alice's WebID URI.
    alice_identity_pub_bytes:
        Alice's X25519 public key bytes (32 raw bytes).
    header:
        The header dict produced by ``init_outbound_session``.
    signed_prekey_priv_bytes:
        Raw 32-byte X25519 private key bytes for Bob's signed prekey.
    one_time_prekey_priv_bytes:
        Raw 32-byte X25519 private key bytes for the consumed OPK (or None).

    Returns
    -------
    SessionState with Bob's perspective (send/recv chains swapped vs Alice).
    """
    ek_a_pub = X25519PublicKey.from_public_bytes(_b64dec(header["ek_pub_b64"]))
    ik_a_pub = X25519PublicKey.from_public_bytes(alice_identity_pub_bytes)

    # Bob's X25519 identity key
    ik_b = _x25519_from_ed25519_priv(bob_identity_key)

    spk_b = X25519PrivateKey.from_private_bytes(signed_prekey_priv_bytes)

    # Mirror of Alice's DH computations (roles swapped)
    dh1 = spk_b.exchange(ik_a_pub)
    dh2 = ik_b.exchange(ek_a_pub)
    dh3 = spk_b.exchange(ek_a_pub)
    dh4: bytes | None = None
    if one_time_prekey_priv_bytes is not None:
        opk_b = X25519PrivateKey.from_private_bytes(one_time_prekey_priv_bytes)
        dh4 = opk_b.exchange(ek_a_pub)

    master = _x3dh_master_secret(dh1, dh2, dh3, dh4)
    root_key = master[:32]
    # Bob's chains are the inverse of Alice's
    recv_chain_key = master[32:]   # Bob receives on Alice's send chain
    send_chain_key = root_key      # Bob sends on the recv chain Alice uses

    return SessionState(
        session_id=header["session_id"],
        peer_webid=alice_webid,
        owner_webid=bob_webid,
        root_key=root_key,
        send_chain_key=send_chain_key,
        recv_chain_key=recv_chain_key,
    )


# ---------------------------------------------------------------------------
# Message encryption / decryption
# ---------------------------------------------------------------------------

def encrypt_session_message(
    state: SessionState,
    plaintext: str,
    aad: bytes = b"",
) -> tuple[SessionState, dict]:
    """Encrypt a message using the current send chain key.

    Advances the send chain and returns a new immutable-ish ``SessionState``
    (the dataclass is mutated in place for efficiency — callers should treat
    the returned state as the authoritative copy).

    Returns
    -------
    (updated_state, payload_dict)
        payload_dict keys: ciphertext_b64, nonce_b64, msg_num, session_id.
    """
    next_chain_key, msg_key = advance_chain(state.send_chain_key)
    msg_num = state.send_count

    nonce = msg_num.to_bytes(12, "big")
    ciphertext = AESGCM(msg_key).encrypt(nonce, plaintext.encode(), aad)

    state.send_chain_key = next_chain_key
    state.send_count = msg_num + 1

    payload = {
        "session_id": state.session_id,
        "msg_num": msg_num,
        "nonce_b64": _b64enc(nonce),
        "ciphertext_b64": _b64enc(ciphertext),
    }
    return state, payload


def decrypt_session_message(
    state: SessionState,
    payload: dict,
    aad: bytes = b"",
) -> tuple[SessionState, str]:
    """Decrypt an inbound message using the current recv chain key.

    Returns
    -------
    (updated_state, plaintext)

    Raises
    ------
    cryptography.exceptions.InvalidTag
        If authentication fails (wrong session, corrupted payload, replay).
    ValueError
        If the payload is malformed.
    """
    next_chain_key, msg_key = advance_chain(state.recv_chain_key)
    msg_num = payload["msg_num"]

    nonce = msg_num.to_bytes(12, "big")
    ciphertext = _b64dec(payload["ciphertext_b64"])
    plaintext_bytes = AESGCM(msg_key).decrypt(nonce, ciphertext, aad)

    state.recv_chain_key = next_chain_key
    state.recv_count = msg_num + 1

    return state, plaintext_bytes.decode()


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def session_to_dict(state: SessionState) -> dict:
    """Serialize a SessionState to a JSON-safe dict (bytes as base64 strings)."""
    return {
        "session_id": state.session_id,
        "peer_webid": state.peer_webid,
        "owner_webid": state.owner_webid,
        "root_key": _b64enc(state.root_key),
        "send_chain_key": _b64enc(state.send_chain_key),
        "recv_chain_key": _b64enc(state.recv_chain_key),
        "send_count": state.send_count,
        "recv_count": state.recv_count,
    }


def session_from_dict(d: dict) -> SessionState:
    """Deserialize a SessionState from a dict produced by ``session_to_dict``."""
    return SessionState(
        session_id=d["session_id"],
        peer_webid=d["peer_webid"],
        owner_webid=d["owner_webid"],
        root_key=_b64dec(d["root_key"]),
        send_chain_key=_b64dec(d["send_chain_key"]),
        recv_chain_key=_b64dec(d["recv_chain_key"]),
        send_count=d.get("send_count", 0),
        recv_count=d.get("recv_count", 0),
    )
