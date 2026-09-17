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


def _verify(pubkey_hex, sig_bytes, message):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(sig_bytes, message)
        return True
    except (InvalidSignature, ValueError):
        return False


def test_full_handshake_cert_is_mutually_signed(store):
    """R113: a genuine handshake yields a cert the SUBJECT also consented to, so
    verify_mutual (issuer + subject signatures) accepts it."""
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    cert, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, CAPS, CAPS, store
    )
    assert valid
    assert cert.subject_signature is not None
    assert cert.verify_mutual(_verify)
    # Tampering the subject to a party that never consented breaks verify_mutual.
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    ).hex()
    cert.subject = other
    assert not cert.verify_mutual(_verify)


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


# ---------------------------------------------------------------------------
# F5 — capability intersection: the issuer only signs what it offered
# ---------------------------------------------------------------------------

def test_finalize_drops_capability_not_offered(store):
    """An acceptor that echoes a capability the issuer never offered gets a cert
    without it — the cert is the intersection of the offer and the echo."""
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    offered = [Capability(with_="stash://alice/shared/", can="read")]
    echoed = offered + [Capability(with_="/", can="admin")]
    cert, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, offered, echoed, store
    )
    assert valid
    grants = {(c.can, c.with_) for c in cert.capabilities}
    assert ("read", "stash://alice/shared/") in grants
    assert ("admin", "/") not in grants


def test_finalize_preserves_exact_echo_and_consent(store):
    """Echoing exactly the offer preserves the capability and keeps the v2
    subject-consent valid (the consent digest is over the intersected list)."""
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    offered = [Capability(with_="stash://alice/shared/", can="read")]
    cert, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, offered, offered, store
    )
    assert valid
    grants = {(c.can, c.with_) for c in cert.capabilities}
    assert grants == {("read", "stash://alice/shared/")}
    assert cert.verify_mutual(_verify)


def test_finalize_drops_echoed_caveat_widening(store):
    """An acceptor may not widen a caveat the issuer offered: an echo that loosens
    the offered caveat is not covered and is dropped from the cert."""
    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    offered = [Capability(with_="stash://alice/shared/", can="read", caveats={"quota_mb": 100})]
    echoed = [Capability(with_="stash://alice/shared/", can="read", caveats={"quota_mb": 500})]
    cert, valid = run_local_handshake(
        alice_id, alice_store, bob_id, bob_store, offered, echoed, store
    )
    assert valid
    assert cert.capabilities == []


def test_process_join_requests_intersects_offer(store):
    """process_join_requests intersects each echo against the supplied offer."""
    from proxion_messenger_core.handshake import process_join_requests

    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    alice_store_pub = _pub(alice_store)
    bob_store_pub = _pub(bob_store)

    offered = [Capability(with_="stash://room/", can="read")]
    invite = create_invite(alice_id, alice_store_pub, offered)
    send_invite(invite, bob_store_pub, store)
    bob_invite, _ = receive_invites(bob_store, store)[0]
    echoed = offered + [Capability(with_="/", can="admin")]
    accept_invite(bob_invite, bob_id, bob_store_pub, echoed, store)

    results = process_join_requests(
        alice_id, alice_store, store, offered_capabilities=offered
    )
    assert len(results) == 1
    cert, ok = results[0]
    assert ok
    grants = {(c.can, c.with_) for c in cert.capabilities}
    assert grants == {("read", "stash://room/")}


def test_process_join_requests_without_offer_keeps_echo(store):
    """Without an offer, process_join_requests keeps the acceptor's echoed set
    unchanged (prior behaviour for callers that do not yet supply an offer)."""
    from proxion_messenger_core.handshake import process_join_requests

    alice_id, alice_store = _new_agent()
    bob_id, bob_store = _new_agent()
    alice_store_pub = _pub(alice_store)
    bob_store_pub = _pub(bob_store)

    offered = [Capability(with_="stash://room/", can="read")]
    invite = create_invite(alice_id, alice_store_pub, offered)
    send_invite(invite, bob_store_pub, store)
    bob_invite, _ = receive_invites(bob_store, store)[0]
    echoed = offered + [Capability(with_="/", can="admin")]
    accept_invite(bob_invite, bob_id, bob_store_pub, echoed, store)

    results = process_join_requests(alice_id, alice_store, store)
    assert len(results) == 1
    cert, ok = results[0]
    assert ok
    grants = {(c.can, c.with_) for c in cert.capabilities}
    assert ("admin", "/") in grants
