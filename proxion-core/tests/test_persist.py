"""Tests for proxion_messenger_core.persist — AgentState save/load."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from proxion_messenger_core.persist import AgentState, PersistError
from proxion_messenger_core.revocation import RevocationList
from proxion_messenger_core.federation import Capability, RelationshipCertificate


PASSPHRASE = b"correct-horse-battery-staple"


@pytest.fixture
def state():
    return AgentState.generate()


@pytest.fixture
def state_file(tmp_path, state):
    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    return p, state


# ---------------------------------------------------------------------------
# AgentState.generate
# ---------------------------------------------------------------------------

def test_generate_creates_ed25519_identity(state):
    assert isinstance(state.identity_key, Ed25519PrivateKey)


def test_generate_creates_x25519_store_key(state):
    assert isinstance(state.store_key, X25519PrivateKey)


def test_generate_empty_revocation_list(state):
    assert isinstance(state.revocation_list, RevocationList)


def test_generate_empty_certificates(state):
    assert state.certificates == []


def test_two_generates_differ(state):
    other = AgentState.generate()
    assert state.identity_pub_bytes != other.identity_pub_bytes
    assert state.store_pub_bytes != other.store_pub_bytes


# ---------------------------------------------------------------------------
# Convenience properties
# ---------------------------------------------------------------------------

def test_identity_pub_bytes_length(state):
    assert len(state.identity_pub_bytes) == 32


def test_store_pub_bytes_length(state):
    assert len(state.store_pub_bytes) == 32


def test_signing_key_bytes_length(state):
    assert len(state.signing_key_bytes) == 32


def test_signing_key_bytes_is_raw_private(state):
    # Reconstruct the key from raw bytes and verify the public key matches
    reconstructed = Ed25519PrivateKey.from_private_bytes(state.signing_key_bytes)
    assert (
        reconstructed.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        == state.identity_pub_bytes
    )


# ---------------------------------------------------------------------------
# save / load — happy path
# ---------------------------------------------------------------------------

def test_save_creates_file(tmp_path, state):
    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    assert p.exists()


def test_save_file_is_valid_json(tmp_path, state):
    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    data = json.loads(p.read_text())
    assert data["@type"] == "ProxionAgentState"
    assert data["version"] == 1


def test_load_restores_identity_pub(state_file):
    p, original = state_file
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.identity_pub_bytes == original.identity_pub_bytes


def test_load_restores_store_pub(state_file):
    p, original = state_file
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.store_pub_bytes == original.store_pub_bytes


def test_load_identity_key_is_functional(state_file):
    """Loaded key must be able to sign — Ed25519.sign() must not raise."""
    p, _ = state_file
    loaded = AgentState.load(p, PASSPHRASE)
    sig = loaded.identity_key.sign(b"test message")
    assert len(sig) == 64


def test_load_store_key_is_functional(state_file, tmp_path):
    """Loaded X25519 key must be able to perform ECDH."""
    p, _ = state_file
    loaded = AgentState.load(p, PASSPHRASE)
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(loaded.store_pub)
    assert len(shared) == 32


def test_round_trip_preserves_signing_key_bytes(state_file):
    p, original = state_file
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.signing_key_bytes == original.signing_key_bytes


# ---------------------------------------------------------------------------
# Revocation list persistence
# ---------------------------------------------------------------------------

def test_revocation_entries_persisted(tmp_path, state):
    from proxion_messenger_core import issue_token
    import os
    sk = os.urandom(32)
    now = datetime.now(timezone.utc)
    token = issue_token([("read", "/data/")], now + timedelta(hours=1), "svc", [], "fp", sk, now=now)
    state.revocation_list.revoke(token, now)

    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.revocation_list.is_revoked(token, now)


def test_empty_revocation_list_persisted(state_file):
    p, _ = state_file
    loaded = AgentState.load(p, PASSPHRASE)
    # No entries — is_revoked should not crash
    from proxion_messenger_core import issue_token
    import os
    now = datetime.now(timezone.utc)
    sk = os.urandom(32)
    token = issue_token([("read", "/x/")], now + timedelta(hours=1), "s", [], "fp", sk, now=now)
    assert not loaded.revocation_list.is_revoked(token, now)


# ---------------------------------------------------------------------------
# Certificate persistence
# ---------------------------------------------------------------------------

def test_certificates_persisted(tmp_path, state):
    cert = RelationshipCertificate(
        issuer="alice",
        subject="bob",
        capabilities=[Capability(with_="stash://alice/shared/", can="read")],
        wireguard={},
    )
    state.certificates.append(cert)

    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert len(loaded.certificates) == 1
    assert loaded.certificates[0].issuer == "alice"
    assert loaded.certificates[0].subject == "bob"
    assert len(loaded.certificates[0].capabilities) == 1
    assert loaded.certificates[0].capabilities[0].can == "read"


def test_certificate_id_preserved(tmp_path, state):
    cert = RelationshipCertificate("a", "b", [], {})
    orig_id = cert.certificate_id
    state.certificates.append(cert)
    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.certificates[0].certificate_id == orig_id


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_wrong_passphrase_raises(state_file):
    p, _ = state_file
    with pytest.raises(PersistError, match="passphrase|corrupted|Ed25519|invalid key envelope"):
        AgentState.load(p, b"wrong-passphrase")


def test_missing_file_raises(tmp_path):
    with pytest.raises(PersistError, match="cannot read"):
        AgentState.load(tmp_path / "nonexistent.json", PASSPHRASE)


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(PersistError, match="not valid JSON"):
        AgentState.load(p, PASSPHRASE)


def test_wrong_type_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"@type": "SomethingElse", "version": 1}), encoding="utf-8")
    with pytest.raises(PersistError, match="ProxionAgentState"):
        AgentState.load(p, PASSPHRASE)


def test_wrong_version_raises(tmp_path, state):
    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    data = json.loads(p.read_text())
    data["version"] = 99
    p.write_text(json.dumps(data))
    with pytest.raises(PersistError, match="version"):
        AgentState.load(p, PASSPHRASE)


def test_atomic_write_does_not_leave_tmp(tmp_path, state):
    p = tmp_path / "agent.json"
    state.save(p, PASSPHRASE)
    tmp = p.with_suffix(".json.tmp")
    assert not tmp.exists()


# ---------------------------------------------------------------------------
# Integration — full handshake then persist, reload, verify cert still works
# ---------------------------------------------------------------------------

def test_persist_and_reload_cert_for_certtoken(tmp_path):
    """Save a state with a real handshake cert, reload it, mint a token."""
    from proxion_messenger_core import run_local_handshake, MemoryStore, issue_from_certificate
    from proxion_messenger_core.federation import Capability
    import os

    alice_id = Ed25519PrivateKey.generate()
    alice_store = X25519PrivateKey.generate()
    bob_id = Ed25519PrivateKey.generate()
    bob_store = X25519PrivateKey.generate()
    caps = [Capability(with_="stash://alice/shared/bob/", can="read")]

    mem = MemoryStore()
    cert, valid = run_local_handshake(alice_id, alice_store, bob_id, bob_store, caps, caps, mem)
    assert valid

    state = AgentState(identity_key=bob_id, store_key=bob_store)
    state.certificates.append(cert)
    p = tmp_path / "bob.json"
    state.save(p, PASSPHRASE)

    loaded = AgentState.load(p, PASSPHRASE)
    restored_cert = loaded.certificates[0]
    sk = os.urandom(32)
    now = datetime.now(timezone.utc)
    token = issue_from_certificate(
        cert=restored_cert,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=loaded.identity_pub,
        signing_key=sk,
        now=now,
    )
    assert token is not None
    assert ("read", "stash://alice/shared/bob/") in token.permissions


# ---------------------------------------------------------------------------
# CSS fields persistence
# ---------------------------------------------------------------------------

def test_agent_state_css_fields_round_trip(tmp_path):
    """css_pod_url and css_webid survive save/load."""
    state = AgentState.generate()
    state.css_pod_url = "http://localhost:3001"
    state.css_webid = "http://localhost:3001/alice/profile/card#me"
    path = tmp_path / "state.json"
    state.save(path, b"testpass")
    loaded = AgentState.load(path, b"testpass")
    assert loaded.css_pod_url == "http://localhost:3001"
    assert loaded.css_webid == "http://localhost:3001/alice/profile/card#me"


def test_agent_state_css_fields_default_none(tmp_path):
    """AgentState loaded from a state file without css fields defaults to None."""
    state = AgentState.generate()
    path = tmp_path / "state.json"
    state.save(path, b"testpass")
    # Simulate old-format file by removing css fields
    raw = json.loads(path.read_text())
    raw.pop("css_pod_url", None)
    raw.pop("css_webid", None)
    path.write_text(json.dumps(raw))
    loaded = AgentState.load(path, b"testpass")
    assert loaded.css_pod_url is None
    assert loaded.css_webid is None
