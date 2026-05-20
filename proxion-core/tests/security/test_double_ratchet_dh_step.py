"""Tests for the DH ratchet step added in Round 18."""
import base64
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from proxion_messenger_core.e2e_session import (
    SessionState,
    dh_ratchet_advance,
    encrypt_session_message,
    decrypt_session_message,
    init_inbound_session,
    init_outbound_session,
    generate_prekey_bundle,
    session_to_dict,
    session_from_dict,
    advance_chain,
)


def _ed_to_x25519_pub(ed_key: Ed25519PrivateKey) -> bytes:
    """Convert Ed25519 private key to matching X25519 public key bytes (seed reuse)."""
    raw_seed = ed_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return X25519PrivateKey.from_private_bytes(raw_seed[:32]).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _bootstrap():
    """Bootstrap an Alice→Bob X3DH session and return (alice_state, bob_state)."""
    alice_key = Ed25519PrivateKey.generate()
    bob_key = Ed25519PrivateKey.generate()

    bob_bundle = generate_prekey_bundle("bob@example.org")
    alice_state, header = init_outbound_session(
        alice_identity_key=alice_key,
        alice_webid="alice@example.org",
        bob_webid="bob@example.org",
        bob_identity_pub_bytes=_ed_to_x25519_pub(bob_key),
        bob_signed_prekey_pub_b64=bob_bundle["signed_prekey_pub_b64"],
    )
    bob_state = init_inbound_session(
        bob_identity_key=bob_key,
        bob_webid="bob@example.org",
        alice_webid="alice@example.org",
        alice_identity_pub_bytes=_ed_to_x25519_pub(alice_key),
        header=header,
        signed_prekey_priv_bytes=base64.b64decode(bob_bundle["signed_prekey_priv_b64"]),
    )
    return alice_state, bob_state


def test_dh_ratchet_advances_root_key_on_reply():
    """After Alice sends and Bob replies, both perform DH ratchet. Root key must change."""
    alice_state, bob_state = _bootstrap()
    original_alice_root = alice_state.root_key

    # Alice sends, Bob decrypts (no ratchet yet — just symmetric chain)
    alice_state, payload = encrypt_session_message(alice_state, "hello")
    bob_state, pt = decrypt_session_message(bob_state, payload)
    assert pt == "hello"

    # Bob replies — includes his ratchet pub
    bob_state, reply_payload = encrypt_session_message(bob_state, "hi there")
    assert "ratchet_pub_b64" in reply_payload

    # Alice decrypts — DH ratchet fires
    alice_root_before = alice_state.root_key
    alice_state, reply_pt = decrypt_session_message(alice_state, reply_payload)
    assert reply_pt == "hi there"
    # Root key must have changed after the DH ratchet step
    assert alice_state.root_key != alice_root_before


def test_compromised_chain_key_heals_after_dh_ratchet():
    """Capturing the send chain key at step N doesn't help decrypt N+2 after DH ratchet."""
    alice_state, bob_state = _bootstrap()

    # Alice sends one message (no ratchet)
    alice_state, p1 = encrypt_session_message(alice_state, "msg1")
    bob_state, _ = decrypt_session_message(bob_state, p1)

    # Capture Alice's current chain key BEFORE the ratchet
    compromised_chain_key = alice_state.send_chain_key

    # Bob replies — triggers DH ratchet on Alice's receive side
    bob_state, p2 = encrypt_session_message(bob_state, "bob reply")
    alice_state, _ = decrypt_session_message(alice_state, p2)

    # Alice now sends with a NEW chain (after DH ratchet generated new send chain)
    alice_state, p3 = encrypt_session_message(alice_state, "msg3 after ratchet")

    # Attacker tries to use the compromised chain key to decrypt p3 — must fail
    # The compromised key would advance the old chain, not the new DH-ratcheted chain
    _, attacker_msg_key = advance_chain(compromised_chain_key)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = p3["msg_num"].to_bytes(12, "big")
    import base64
    ct = base64.b64decode(p3["ciphertext_b64"])
    with pytest.raises(Exception):
        AESGCM(attacker_msg_key).decrypt(nonce, ct, b"")


def test_session_roundtrip_preserves_ratchet_fields():
    """session_to_dict / session_from_dict preserve DH ratchet state."""
    alice_state, bob_state = _bootstrap()

    alice_state, payload = encrypt_session_message(alice_state, "ping")
    bob_state, _ = decrypt_session_message(bob_state, payload)
    bob_state, reply = encrypt_session_message(bob_state, "pong")
    alice_state, _ = decrypt_session_message(alice_state, reply)

    # Serialise and deserialise
    d = session_to_dict(alice_state)
    restored = session_from_dict(d)

    assert restored.send_ratchet_key_priv_b64 == alice_state.send_ratchet_key_priv_b64
    assert restored.recv_ratchet_pub_b64 == alice_state.recv_ratchet_pub_b64
    assert restored.prev_send_count == alice_state.prev_send_count
    assert restored.root_key == alice_state.root_key

    # Restored state must still encrypt/decrypt correctly
    restored, p = encrypt_session_message(restored, "after restore")
    bob_state, pt = decrypt_session_message(bob_state, p)
    assert pt == "after restore"
