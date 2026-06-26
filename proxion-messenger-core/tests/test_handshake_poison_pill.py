"""Tests for the handshake poison-pill fix.

Before the fix, an undecryptable message left in the mailbox was never consumed,
causing every subsequent poll to try (and fail) to process it indefinitely.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.store import MemoryStore, StoreConfig
from proxion_messenger_core.sealed import mailbox_id_for, SealedEnvelope
from proxion_messenger_core.handshake import (
    receive_invites,
    receive_acceptances,
    receive_certificates,
    create_invite,
)
from proxion_messenger_core.federation import Capability


def _store_priv():
    return X25519PrivateKey.generate()


def _store_pub(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _garbage_envelope() -> SealedEnvelope:
    """A SealedEnvelope with random bytes that will fail decryption."""
    return SealedEnvelope(ephemeral_pub=b"\xff" * 32, nonce=b"\x00" * 12, ciphertext=b"\xff" * 64)


@pytest.mark.parametrize("receive_fn,label", [
    (receive_invites, "receive_invites"),
    (receive_acceptances, "receive_acceptances"),
    (receive_certificates, "receive_certificates"),
])
def test_garbage_message_is_consumed_not_retried(receive_fn, label):
    """Undecryptable envelopes must be deleted from the mailbox after one failed attempt."""
    priv = _store_priv()
    pub = _store_pub(priv)
    store = MemoryStore(StoreConfig(message_ttl=None))
    mailbox = mailbox_id_for(pub)

    # Put one garbage message that can never be decrypted
    store.put(mailbox, _garbage_envelope())
    assert store.mailbox_count() == 1

    # First call — should fail to decrypt and consume (discard) the message
    receive_fn(priv, store)

    # Mailbox must be empty now — not left with the poison pill
    remaining = store.list_all(mailbox)
    assert remaining == [], (
        f"{label}: garbage message was not consumed; "
        f"{len(remaining)} message(s) remain in mailbox"
    )


def test_wrong_type_message_is_left_for_other_handlers():
    """A decryptable message with the wrong @type must NOT be consumed by receive_invites."""
    alice_id_priv = Ed25519PrivateKey.generate()
    alice_store_priv = _store_priv()
    alice_store_pub = _store_pub(alice_store_priv)

    bob_store_priv = _store_priv()
    bob_store_pub = _store_pub(bob_store_priv)

    store = MemoryStore(StoreConfig(message_ttl=None))

    # Send Alice a valid invite (this ends up in her mailbox as an InviteAcceptance would)
    invite = create_invite(
        alice_id_priv,
        alice_store_pub,
        [Capability(with_="stash://dm/", can="crud/write")],
    )
    # Seal it to Bob's mailbox but with Alice's key (wrong type for receive_invites from Bob's POV)
    from proxion_messenger_core.sealed import seal_json
    mailbox = mailbox_id_for(bob_store_pub)
    sealed = seal_json(invite.to_dict(), bob_store_pub)
    # Override @type so it looks like a RelationshipCertificate to receive_invites
    cert_payload = dict(invite.to_dict())
    cert_payload["@type"] = "RelationshipCertificate"
    sealed2 = seal_json(cert_payload, bob_store_pub)
    store.put(mailbox, sealed2)

    # receive_invites should skip this message (wrong @type) and NOT consume it
    results = receive_invites(bob_store_priv, store)
    assert results == []

    remaining = store.list_all(mailbox)
    assert len(remaining) == 1, "Wrong-type message must be left for receive_certificates"
