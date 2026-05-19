"""Tests for run_bidirectional_handshake helper."""

import uuid

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import MemoryStore, run_bidirectional_handshake, run_local_handshake
from proxion_messenger_core.certtoken import issue_from_certificate
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.persist import AgentState


def _pub_hex(agent: AgentState) -> str:
    return agent.identity_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def test_bidirectional_returns_two_cert_pairs():
    alice = AgentState.generate()
    bob = AgentState.generate()
    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()

    (cert_a_to_b, valid_ab), (cert_b_to_a, valid_ba) = run_bidirectional_handshake(
        alice.identity_key,
        alice.store_key,
        bob.identity_key,
        bob.store_key,
        caps,
        caps,
        store,
    )
    assert cert_a_to_b is not None and cert_b_to_a is not None
    assert valid_ab and valid_ba


def test_bidirectional_cert_issuers_are_swapped():
    alice = AgentState.generate()
    bob = AgentState.generate()
    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()
    (cert_a_to_b, _), (cert_b_to_a, _) = run_bidirectional_handshake(
        alice.identity_key,
        alice.store_key,
        bob.identity_key,
        bob.store_key,
        caps,
        caps,
        store,
    )
    assert cert_a_to_b.issuer == _pub_hex(alice)
    assert cert_b_to_a.issuer == _pub_hex(bob)


def test_bidirectional_cert_subjects_are_swapped():
    alice = AgentState.generate()
    bob = AgentState.generate()
    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()
    (cert_a_to_b, _), (cert_b_to_a, _) = run_bidirectional_handshake(
        alice.identity_key,
        alice.store_key,
        bob.identity_key,
        bob.store_key,
        caps,
        caps,
        store,
    )
    assert cert_a_to_b.subject == _pub_hex(bob)
    assert cert_b_to_a.subject == _pub_hex(alice)


def test_bidirectional_capabilities_are_independent():
    alice = AgentState.generate()
    bob = AgentState.generate()
    caps_ab = [Capability(can="read", with_="stash://messages/")]
    caps_ba = [Capability(can="write", with_="stash://messages/")]
    store = MemoryStore()
    (cert_a_to_b, _), (cert_b_to_a, _) = run_bidirectional_handshake(
        alice.identity_key,
        alice.store_key,
        bob.identity_key,
        bob.store_key,
        caps_ab,
        caps_ba,
        store,
    )
    assert [(c.can, c.with_) for c in cert_a_to_b.capabilities] == [("read", "stash://messages/")]
    assert [(c.can, c.with_) for c in cert_b_to_a.capabilities] == [("write", "stash://messages/")]


def test_bidirectional_tokens_mint_for_correct_aud():
    alice = AgentState.generate()
    bob = AgentState.generate()
    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()
    (cert_a_to_b, _), (cert_b_to_a, _) = run_bidirectional_handshake(
        alice.identity_key,
        alice.store_key,
        bob.identity_key,
        bob.store_key,
        caps,
        caps,
        store,
    )
    token_ab = issue_from_certificate(
        cert=cert_a_to_b,
        requested_permissions=[("read", "stash://messages/")],
        holder_pub_key=bob.identity_key.public_key(),
        signing_key=alice.signing_key_bytes,
    )
    token_ba = issue_from_certificate(
        cert=cert_b_to_a,
        requested_permissions=[("read", "stash://messages/")],
        holder_pub_key=alice.identity_key.public_key(),
        signing_key=bob.signing_key_bytes,
    )
    assert token_ab.aud == _pub_hex(alice)
    assert token_ba.aud == _pub_hex(bob)


def test_run_local_handshake_uses_precomputed_certificate_id():
    alice = AgentState.generate()
    bob = AgentState.generate()
    cert_id = str(uuid.uuid4())
    caps = [Capability(can="read", with_=f"stash://messages/thread/{cert_id}/")]
    store = MemoryStore()

    cert, valid = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=caps,
        bob_capabilities=caps,
        store=store,
        certificate_id=cert_id,
    )
    assert valid
    assert cert.certificate_id == cert_id


def test_bidirectional_uses_precomputed_certificate_ids_per_direction():
    alice = AgentState.generate()
    bob = AgentState.generate()
    cert_id_ab = str(uuid.uuid4())
    cert_id_ba = str(uuid.uuid4())
    caps_ab = [Capability(can="read", with_=f"stash://messages/thread/{cert_id_ab}/")]
    caps_ba = [Capability(can="read", with_=f"stash://messages/thread/{cert_id_ba}/")]
    store = MemoryStore()

    (cert_ab, valid_ab), (cert_ba, valid_ba) = run_bidirectional_handshake(
        alice.identity_key,
        alice.store_key,
        bob.identity_key,
        bob.store_key,
        caps_ab,
        caps_ba,
        store,
        certificate_id_a_to_b=cert_id_ab,
        certificate_id_b_to_a=cert_id_ba,
    )
    assert valid_ab and valid_ba
    assert cert_ab.certificate_id == cert_id_ab
    assert cert_ba.certificate_id == cert_id_ba


def test_precomputed_cert_id_supports_invite_time_thread_scoped_caps():
    alice = AgentState.generate()
    bob = AgentState.generate()
    cert_id = str(uuid.uuid4())
    allowed_path = f"stash://messages/thread/{cert_id}/"
    caps = [Capability(can="read", with_=allowed_path)]
    store = MemoryStore()

    cert, valid = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=caps,
        bob_capabilities=caps,
        store=store,
        certificate_id=cert_id,
    )
    assert valid
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", allowed_path)],
        holder_pub_key=bob.identity_key.public_key(),
        signing_key=alice.signing_key_bytes,
    )
    assert ("read", allowed_path) in token.permissions
