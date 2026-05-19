"""Tests for pending invite persistence in AgentState."""

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.federation import Capability, FederationInvite, InviteAcceptance
from proxion_messenger_core.persist import AgentState, PendingInvite

PASSPHRASE = b"test-passphrase"


@pytest.fixture
def agent():
    return AgentState.generate()


@pytest.fixture
def invite(agent):
    inv = FederationInvite(
        issuer={"public_key": agent.identity_pub_bytes.hex()},
        endpoint_hints=[],
        capabilities=[Capability(with_="stash://me/shared/", can="read")],
    )
    inv.sign(agent.identity_key)
    return inv


@pytest.fixture
def peer_store_pub_hex():
    priv = X25519PrivateKey.generate()
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


# ---------------------------------------------------------------------------
# PendingInvite dataclass
# ---------------------------------------------------------------------------

def test_pending_invite_to_dict(invite, peer_store_pub_hex):
    pi = PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex)
    d = pi.to_dict()
    assert d["peer_store_pub_hex"] == peer_store_pub_hex
    assert d["invite"]["invitation_id"] == invite.invitation_id
    assert "sent_at" in d


def test_pending_invite_round_trip(invite, peer_store_pub_hex):
    pi = PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex, sent_at=12345.0)
    restored = PendingInvite.from_dict(pi.to_dict())
    assert restored.peer_store_pub_hex == peer_store_pub_hex
    assert restored.invite.invitation_id == invite.invitation_id
    assert restored.invite.challenge_marker == invite.challenge_marker
    assert restored.invite.nonce == invite.nonce
    assert restored.invite.signature == invite.signature
    assert restored.sent_at == 12345.0


def test_pending_invite_capabilities_preserved(invite, peer_store_pub_hex):
    pi = PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex)
    restored = PendingInvite.from_dict(pi.to_dict())
    assert len(restored.invite.capabilities) == 1
    assert restored.invite.capabilities[0].can == "read"
    assert restored.invite.capabilities[0].with_ == "stash://me/shared/"


def test_pending_invite_sent_at_defaults_to_now(invite, peer_store_pub_hex):
    before = time.time()
    pi = PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex)
    after = time.time()
    assert before <= pi.sent_at <= after


# ---------------------------------------------------------------------------
# AgentState.pending_invites — in-memory
# ---------------------------------------------------------------------------

def test_agent_state_has_pending_invites_list(agent):
    assert isinstance(agent.pending_invites, list)
    assert agent.pending_invites == []


def test_agent_state_append_pending_invite(agent, invite, peer_store_pub_hex):
    agent.pending_invites.append(
        PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex)
    )
    assert len(agent.pending_invites) == 1
    assert agent.pending_invites[0].invite.invitation_id == invite.invitation_id


# ---------------------------------------------------------------------------
# Persistence — save / load round-trip
# ---------------------------------------------------------------------------

def test_pending_invites_persisted(tmp_path, agent, invite, peer_store_pub_hex):
    agent.pending_invites.append(
        PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex)
    )
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)

    loaded = AgentState.load(p, PASSPHRASE)
    assert len(loaded.pending_invites) == 1
    pi = loaded.pending_invites[0]
    assert pi.peer_store_pub_hex == peer_store_pub_hex
    assert pi.invite.invitation_id == invite.invitation_id
    assert pi.invite.challenge_marker == invite.challenge_marker
    assert pi.invite.signature == invite.signature


def test_multiple_pending_invites_persisted(tmp_path, agent, peer_store_pub_hex):
    for i in range(3):
        inv = FederationInvite(
            issuer={"public_key": agent.identity_pub_bytes.hex()},
            endpoint_hints=[],
            capabilities=[Capability(with_=f"stash://me/r{i}/", can="read")],
        )
        inv.sign(agent.identity_key)
        agent.pending_invites.append(
            PendingInvite(invite=inv, peer_store_pub_hex=peer_store_pub_hex)
        )

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert len(loaded.pending_invites) == 3


def test_empty_pending_invites_persisted(tmp_path, agent):
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.pending_invites == []


def test_remove_finalized_invite_then_persist(tmp_path, agent, invite, peer_store_pub_hex):
    """Simulate the finalize flow: pop the invite, save, reload."""
    pi = PendingInvite(invite=invite, peer_store_pub_hex=peer_store_pub_hex)
    agent.pending_invites.append(pi)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)

    # Simulate finalize: remove the invite from pending
    agent.pending_invites = [
        x for x in agent.pending_invites
        if x.invite.invitation_id != invite.invitation_id
    ]
    agent.save(p, PASSPHRASE)

    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.pending_invites == []


# ---------------------------------------------------------------------------
# FederationInvite.from_dict / InviteAcceptance.from_dict
# ---------------------------------------------------------------------------

def test_federation_invite_from_dict_round_trip(invite):
    d = invite.to_dict()
    restored = FederationInvite.from_dict(d)
    assert restored.invitation_id == invite.invitation_id
    assert restored.challenge_marker == invite.challenge_marker
    assert restored.nonce == invite.nonce
    assert restored.signature == invite.signature
    assert restored.issuer == invite.issuer
    assert restored.created_at == invite.created_at
    assert restored.expires_at == invite.expires_at


def test_federation_invite_from_dict_capabilities(invite):
    d = invite.to_dict()
    restored = FederationInvite.from_dict(d)
    assert len(restored.capabilities) == 1
    assert restored.capabilities[0].can == "read"


def test_invite_acceptance_from_dict_round_trip():
    acc = InviteAcceptance(
        invitation_id="inv-123",
        responder={"public_key": "abc", "endpoint_hints": []},
        challenge_response="defsig",
    )
    d = acc.to_dict()
    restored = InviteAcceptance.from_dict(d)
    assert restored.invitation_id == "inv-123"
    assert restored.responder == acc.responder
    assert restored.challenge_response == "defsig"
    assert restored.timestamp == acc.timestamp
    assert restored.signature == acc.signature


# ---------------------------------------------------------------------------
# Integration — full handshake with invite persisted mid-way
# ---------------------------------------------------------------------------

def test_full_handshake_with_persistent_invite(tmp_path):
    """Simulate: Alice sends invite → saves state → reloads → finalizes."""
    from proxion_messenger_core import MemoryStore, run_local_handshake
    from proxion_messenger_core.handshake import (
        create_invite, send_invite, receive_invites, accept_invite,
        receive_acceptances, finalize_handshake, send_certificate,
        receive_certificates,
    )

    alice = AgentState.generate()
    bob = AgentState.generate()
    store = MemoryStore()
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Alice creates and sends an invite
    invite_obj = create_invite(alice.identity_key, alice.store_pub_bytes, caps)
    send_invite(invite_obj, bob.store_pub_bytes, store)

    # Alice saves her state with the pending invite
    alice.pending_invites.append(
        PendingInvite(invite=invite_obj, peer_store_pub_hex=bob.store_pub_bytes.hex())
    )
    p = tmp_path / "alice.json"
    alice.save(p, PASSPHRASE)

    # Bob receives and accepts
    invites = receive_invites(bob.store_key, store)
    assert len(invites) == 1
    received_inv, valid = invites[0]
    assert valid
    accept_invite(received_inv, bob.identity_key, bob.store_pub_bytes, caps, store)

    # Alice reloads her state and finalizes
    alice2 = AgentState.load(p, PASSPHRASE)
    assert len(alice2.pending_invites) == 1

    acceptances = receive_acceptances(alice2.store_key, store)
    assert len(acceptances) == 1
    acceptance, acc_valid = acceptances[0]
    assert acc_valid

    pi = alice2.pending_invites[0]
    cert = finalize_handshake(acceptance, pi.invite, alice2.identity_key)
    send_certificate(cert, bob.store_pub_bytes, store)

    # Bob receives the cert
    cert_pairs = receive_certificates(bob.store_key, store)
    assert len(cert_pairs) == 1
    bob_cert, cert_valid = cert_pairs[0]
    assert cert_valid
    assert bob_cert.issuer == alice2.identity_pub_bytes.hex()

    # Alice cleans up her pending invite and saves the cert
    alice2.pending_invites.clear()
    alice2.certificates.append(cert)
    alice2.save(p, PASSPHRASE)

    final = AgentState.load(p, PASSPHRASE)
    assert final.pending_invites == []
    assert len(final.certificates) == 1
