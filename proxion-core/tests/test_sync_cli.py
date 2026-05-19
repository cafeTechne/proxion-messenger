"""Tests for `proxion agent sync` CLI command."""

import time
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, FederationInvite, RelationshipCertificate
from proxion_messenger_core.persist import AgentState, PendingInvite

PASSPHRASE = "sync-test"
runner = CliRunner()
_REMOTE = "proxion_messenger_core.store_client.RemoteStore"


@pytest.fixture
def state_file(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent


def _invoke(state_path):
    return runner.invoke(app, [
        "agent", "sync", "http://localhost:8765",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ])


def _empty_store_mock():
    m = MagicMock()
    m.list_all.return_value = []
    m.take_by_ids.return_value = []
    m.take_all.return_value = []
    m.close.return_value = None
    return m


# ---------------------------------------------------------------------------
# Nothing to sync
# ---------------------------------------------------------------------------

def test_sync_nothing_changed(state_file):
    p, agent = state_file
    with patch(_REMOTE, return_value=_empty_store_mock()):
        result = _invoke(p)
    assert result.exit_code == 0
    assert "nothing changed" in result.output.lower() or "complete" in result.output.lower()


def test_sync_no_state_file_rewrite_on_empty(state_file):
    p, agent = state_file
    mtime_before = p.stat().st_mtime
    import time as _t; _t.sleep(0.05)
    with patch(_REMOTE, return_value=_empty_store_mock()):
        _invoke(p)
    # Nothing changed → file not rewritten
    assert p.stat().st_mtime == mtime_before


# ---------------------------------------------------------------------------
# Certs received
# ---------------------------------------------------------------------------

def test_sync_receives_certs(tmp_path):
    from proxion_messenger_core.handshake import send_certificate
    from proxion_messenger_core.store import MemoryStore

    alice = AgentState.generate()
    bob = AgentState.generate()
    store = MemoryStore()

    cert = RelationshipCertificate(
        issuer=alice.identity_pub_bytes.hex(),
        subject=bob.identity_pub_bytes.hex(),
        capabilities=[Capability(with_="/", can="read")],
        wireguard={},
    )
    cert.sign(alice.identity_key)
    send_certificate(cert, bob.store_pub_bytes, store)

    p = tmp_path / "bob.json"
    bob.save(p, PASSPHRASE.encode())

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.side_effect = lambda mid: store.list_all(mid)
        instance.take_by_ids.side_effect = lambda mid, ids: store.take_by_ids(mid, ids)
        instance.close.return_value = None
        result = _invoke(p)

    assert result.exit_code == 0
    assert "+1" in result.output or "cert" in result.output.lower()
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 1


# ---------------------------------------------------------------------------
# Revocations applied
# ---------------------------------------------------------------------------

def test_sync_applies_revocations(tmp_path):
    from proxion_messenger_core.store import MemoryStore
    from proxion_messenger_core.revoke import create_certificate_revocation, broadcast_revocation
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from datetime import datetime, timezone

    issuer_priv = Ed25519PrivateKey.generate()
    bob = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        subject=bob.identity_pub_bytes.hex(),
        capabilities=[],
        wireguard={},
    )
    notice = create_certificate_revocation(cert, issuer_priv)
    store = MemoryStore()
    broadcast_revocation(notice, [bob.store_pub_bytes], store)

    p = tmp_path / "bob.json"
    bob.save(p, PASSPHRASE.encode())

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.side_effect = lambda mid: store.list_all(mid)
        instance.take_by_ids.side_effect = lambda mid, ids: store.take_by_ids(mid, ids)
        instance.close.return_value = None
        result = _invoke(p)

    assert result.exit_code == 0
    assert "+1" in result.output or "revocation" in result.output.lower()

    from proxion_messenger_core.revocation import certificate_revocation_id
    loaded = AgentState.load(p, PASSPHRASE.encode())
    rev_id = certificate_revocation_id(cert)
    assert loaded.revocation_list.is_revoked(rev_id, datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Expired invites purged
# ---------------------------------------------------------------------------

def test_sync_purges_expired_invites(tmp_path):
    agent = AgentState.generate()
    inv = FederationInvite(
        issuer={"public_key": agent.identity_pub_bytes.hex()},
        endpoint_hints=[], capabilities=[],
    )
    inv.expires_at = int(time.time()) - 1  # already expired
    inv.sign(agent.identity_key)
    agent.pending_invites.append(
        PendingInvite(invite=inv, peer_store_pub_hex="ab" * 32)
    )
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    with patch(_REMOTE, return_value=_empty_store_mock()):
        result = _invoke(p)

    assert result.exit_code == 0
    assert "expired" in result.output.lower()
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.pending_invites) == 0
