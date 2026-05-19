"""Tests for proxion_messenger_core.handshake — three-step federation handshake."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import MemoryStore, run_local_handshake
from proxion_messenger_core.federation import Capability, FederationInvite, InviteAcceptance
from proxion_messenger_core.handshake import (
    HandshakeError,
    accept_invite,
    create_invite,
    finalize_handshake,
    receive_acceptances,
    receive_certificates,
    receive_invites,
    send_certificate,
    send_invite,
)


def _new_agent():
    return Ed25519PrivateKey.generate(), X25519PrivateKey.generate()


def _pub(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


CAPS = [Capability(with_="stash://alice/shared/", can="read")]


# ---------------------------------------------------------------------------
# run_local_handshake — happy path
# ---------------------------------------------------------------------------

def test_full_handshake_cert_valid(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    cert, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, CAPS, CAPS, store
    )
    assert valid
    assert cert.issuer == _pub(alice_id).hex()
    assert cert.subject == _pub(bob_id).hex()


def test_full_handshake_cert_signed(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    cert, _ = run_local_handshake(alice_id, alice_store, bob_id, bob_store, CAPS, CAPS, store)
    assert cert.signature is not None


def test_full_handshake_store_empty_after(store):
    """All messages should be consumed by the end of the handshake."""
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    run_local_handshake(alice_id, alice_store, bob_id, bob_store, CAPS, CAPS, store)
    assert store.mailbox_count() == 0


def test_full_handshake_cert_has_capabilities(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    cert, _ = run_local_handshake(alice_id, alice_store, bob_id, bob_store, CAPS, CAPS, store)
    assert len(cert.capabilities) == len(CAPS)


# ---------------------------------------------------------------------------
# Type filtering — messages of other types left in mailbox
# ---------------------------------------------------------------------------

def test_receive_invites_ignores_other_types(store):
    """A RevocationNotice co-existing in the mailbox must survive receive_invites."""
    from proxion_messenger_core.sealed import mailbox_id_for, seal_json
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    bob_store_pub = _pub(bob_store)

    # Post a fake RevocationNotice first
    store.put(
        mailbox_id_for(bob_store_pub),
        seal_json({"@type": "RevocationNotice", "notice_id": "x"}, bob_store_pub),
    )
    # Then a real invite
    invite = create_invite(alice_id, _pub(alice_store), CAPS)
    send_invite(invite, bob_store_pub, store)

    invites = receive_invites(bob_store, store)
    assert len(invites) == 1
    # RevocationNotice should remain
    from proxion_messenger_core.sealed import mailbox_id_for as mif
    assert store.peek(mif(bob_store_pub))["count"] == 1


def test_receive_acceptances_ignores_other_types(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    alice_store_pub = _pub(alice_store)
    bob_store_pub = _pub(bob_store)
    from proxion_messenger_core.sealed import mailbox_id_for, seal_json

    # Post a noise message to Alice's mailbox
    store.put(
        mailbox_id_for(alice_store_pub),
        seal_json({"@type": "FederationInvite", "junk": True}, alice_store_pub),
    )
    invite = create_invite(alice_id, alice_store_pub, CAPS)
    send_invite(invite, bob_store_pub, store)
    invs = receive_invites(bob_store, store)
    accept_invite(invs[0][0], bob_id, bob_store_pub, CAPS, store)

    acceptances = receive_acceptances(alice_store, store)
    assert len(acceptances) == 1
    # Noise message should remain
    assert store.peek(mailbox_id_for(alice_store_pub))["count"] == 1


# ---------------------------------------------------------------------------
# Security — bad signatures and tampered data
# ---------------------------------------------------------------------------

def test_invalid_alice_invite_sig_flagged(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    alice_store_pub = _pub(alice_store)
    bob_store_pub = _pub(bob_store)

    invite = create_invite(alice_id, alice_store_pub, CAPS)
    invite.signature = "deadbeef" * 16   # corrupt the signature
    send_invite(invite, bob_store_pub, store)

    invites = receive_invites(bob_store, store)
    assert len(invites) == 1
    _, valid = invites[0]
    assert not valid


def test_bad_challenge_response_raises(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    alice_store_pub = _pub(alice_store)
    bob_store_pub = _pub(bob_store)

    invite = create_invite(alice_id, alice_store_pub, CAPS)
    send_invite(invite, bob_store_pub, store)
    bob_invite, _ = receive_invites(bob_store, store)[0]

    # Build acceptance with forged challenge_response
    forged = InviteAcceptance(
        invitation_id=bob_invite.invitation_id,
        responder={
            "public_key": _pub(bob_id).hex(),
            "store_key": bob_store_pub.hex(),
            "capabilities": [c.to_dict() for c in CAPS],
        },
        challenge_response="cafebabe" * 16,
    )
    forged.sign(bob_id)

    with pytest.raises(HandshakeError, match="challenge"):
        finalize_handshake(forged, bob_invite, alice_id)


def test_missing_store_key_in_invite_raises(store):
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()

    # Invite without store_key in issuer
    invite = FederationInvite(
        issuer={"public_key": _pub(alice_id).hex()},   # no store_key
        endpoint_hints=[],
        capabilities=CAPS,
    )
    invite.sign(alice_id)

    with pytest.raises(HandshakeError, match="store_key"):
        accept_invite(invite, bob_id, _pub(bob_store), CAPS, store)
