"""R17: X3DH forward secrecy session primitives."""
import base64
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
)

from proxion_messenger_core.e2e_session import (
    generate_prekey_bundle,
    init_outbound_session,
    init_inbound_session,
    encrypt_session_message,
    decrypt_session_message,
    session_to_dict,
    session_from_dict,
)


def _ed_to_x25519_pub_bytes(ed_key: Ed25519PrivateKey) -> bytes:
    raw = ed_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    x_priv = X25519PrivateKey.from_private_bytes(raw[:32])
    return x_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _bootstrap(alice_key, bob_key, bob_bundle):
    bob_id_pub = _ed_to_x25519_pub_bytes(bob_key)
    alice_state, header = init_outbound_session(
        alice_key, "did:key:alice", "did:key:bob",
        bob_id_pub,
        bob_bundle["signed_prekey_pub_b64"],
    )
    alice_id_pub = _ed_to_x25519_pub_bytes(alice_key)
    spk_priv_bytes = base64.b64decode(bob_bundle["signed_prekey_priv_b64"])
    bob_state = init_inbound_session(
        bob_key, "did:key:bob", "did:key:alice",
        alice_id_pub, header, spk_priv_bytes,
    )
    return alice_state, bob_state


@pytest.fixture
def alice_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def bob_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def bob_bundle():
    return generate_prekey_bundle("did:key:bob")


def test_x3dh_bootstrap_creates_session_for_both_parties(alice_key, bob_key, bob_bundle):
    alice_state, bob_state = _bootstrap(alice_key, bob_key, bob_bundle)
    assert alice_state.root_key == bob_state.root_key
    assert alice_state.session_id == bob_state.session_id


def test_chain_ratchet_advances_per_message(alice_key, bob_key, bob_bundle):
    alice_state, bob_state = _bootstrap(alice_key, bob_key, bob_bundle)
    alice_state, enc1 = encrypt_session_message(alice_state, "message one")
    alice_state, enc2 = encrypt_session_message(alice_state, "message two")
    assert enc1["ciphertext_b64"] != enc2["ciphertext_b64"]
    bob_state, pt1 = decrypt_session_message(bob_state, enc1)
    bob_state, pt2 = decrypt_session_message(bob_state, enc2)
    assert pt1 == "message one"
    assert pt2 == "message two"


def test_old_chain_key_cannot_decrypt_new_messages(alice_key, bob_key, bob_bundle):
    alice_state, bob_state = _bootstrap(alice_key, bob_key, bob_bundle)
    alice_state, enc1 = encrypt_session_message(alice_state, "first")
    bob_state_before = session_from_dict(session_to_dict(bob_state))
    bob_state, _ = decrypt_session_message(bob_state, enc1)
    alice_state, enc2 = encrypt_session_message(alice_state, "second")
    with pytest.raises(Exception):
        decrypt_session_message(bob_state_before, enc2)
