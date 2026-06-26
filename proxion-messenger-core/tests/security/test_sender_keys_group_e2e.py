"""Tests for group E2E via Sender Keys (Round 18)."""
import os
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.sender_keys import (
    generate_sender_key,
    encrypt_group_message,
    decrypt_group_message,
    distribute_sender_key,
    receive_sender_key,
)


def _x25519_pair():
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub_bytes = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv_bytes, pub_bytes


def test_sender_key_generated_and_distributed():
    """generate_sender_key produces a valid state; distribute_sender_key seals it per member."""
    alice_priv, alice_pub = _x25519_pair()
    bob_priv, bob_pub = _x25519_pair()

    alice_key_state = generate_sender_key()
    assert "chain_key_b64" in alice_key_state
    assert alice_key_state["iteration"] == 0

    distribution = distribute_sender_key(
        alice_key_state,
        member_pubkeys={"bob@example.org": bob_pub},
    )
    assert "bob@example.org" in distribution

    # Bob can unseal his copy
    recovered = receive_sender_key(distribution["bob@example.org"], bob_priv)
    assert recovered["chain_key_b64"] == alice_key_state["chain_key_b64"]
    assert recovered["iteration"] == alice_key_state["iteration"]


def test_group_message_encrypted_and_decrypted():
    """encrypt_group_message / decrypt_group_message round-trip."""
    alice_state = generate_sender_key()
    # Give Bob a copy of Alice's sender key at iteration 0
    bob_state = {**alice_state}

    alice_state, payload = encrypt_group_message(alice_state, "hello group", "alice@example.org")
    bob_state, plaintext = decrypt_group_message(bob_state, payload)
    assert plaintext == "hello group"
    assert payload["sender_id"] == "alice@example.org"
    assert payload["e2e_v"] == 2


def test_re_keyed_sender_cannot_decrypt_old_messages():
    """After re-keying, old sender key state cannot decrypt messages from new chain."""
    from cryptography.exceptions import InvalidTag

    alice_state = generate_sender_key()
    old_state = {**alice_state}  # snapshot before any messages

    # Advance to iteration 2
    alice_state, p1 = encrypt_group_message(alice_state, "msg1", "alice@example.org")
    alice_state, p2 = encrypt_group_message(alice_state, "msg2", "alice@example.org")

    # Generate a FRESH sender key (simulating re-key after member removal)
    fresh_state = generate_sender_key()
    fresh_state, p3 = encrypt_group_message(fresh_state, "msg3 after rekey", "alice@example.org")

    # Old state cannot decrypt fresh message (different chain)
    with pytest.raises(Exception):
        decrypt_group_message(old_state, p3)
