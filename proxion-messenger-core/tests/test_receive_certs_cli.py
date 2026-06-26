"""Tests for `proxion agent receive-certs` CLI command."""

from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState

PASSPHRASE = "recv-test"
runner = CliRunner()
_REMOTE = "proxion_messenger_core.store_client.RemoteStore"


@pytest.fixture
def state_file(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent


def _make_cert(issuer_agent: AgentState) -> RelationshipCertificate:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    subject_priv = Ed25519PrivateKey.generate()
    cert = RelationshipCertificate(
        issuer=issuer_agent.identity_pub_bytes.hex(),
        subject=subject_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(issuer_agent.identity_key)
    return cert


def _invoke(state_path, extra=None):
    return runner.invoke(app, [
        "agent", "receive-certs", "http://localhost:8765",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra or []))


# ---------------------------------------------------------------------------
# No certs in mailbox
# ---------------------------------------------------------------------------

def test_receive_certs_empty_mailbox(state_file):
    p, agent = state_file
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = []
        instance.take_by_ids.return_value = []
        instance.close.return_value = None
        result = _invoke(p)
    assert result.exit_code == 0
    assert "No incoming" in result.output


# ---------------------------------------------------------------------------
# Valid cert received and saved
# ---------------------------------------------------------------------------

def test_receive_certs_saves_valid_cert(tmp_path):
    from proxion_messenger_core.handshake import send_certificate
    from proxion_messenger_core.store import MemoryStore
    from proxion_messenger_core.sealed import mailbox_id_for

    alice = AgentState.generate()
    bob = AgentState.generate()

    # Alice creates and sends cert to Bob's mailbox in a MemoryStore
    store = MemoryStore()
    cert = _make_cert(alice)
    send_certificate(cert, bob.store_pub_bytes, store)

    p = tmp_path / "bob.json"
    bob.save(p, PASSPHRASE.encode())

    with patch(_REMOTE) as MockRemote:
        # Route calls to the real MemoryStore
        instance = MockRemote.return_value
        instance.list_all.side_effect = lambda mid: store.list_all(mid)
        instance.take_by_ids.side_effect = lambda mid, ids: store.take_by_ids(mid, ids)
        instance.close.return_value = None

        result = _invoke(p)

    assert result.exit_code == 0, result.output
    assert "saved" in result.output.lower()

    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 1
    assert loaded.certificates[0].certificate_id == cert.certificate_id


def test_receive_certs_skips_duplicate(tmp_path):
    from proxion_messenger_core.handshake import send_certificate
    from proxion_messenger_core.store import MemoryStore

    alice = AgentState.generate()
    bob = AgentState.generate()
    cert = _make_cert(alice)
    bob.certificates.append(cert)   # pre-loaded — already stored

    store = MemoryStore()
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
    assert "already stored" in result.output.lower() or "no new" in result.output.lower()

    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 1   # still 1, not 2


def test_receive_certs_multiple_certs(tmp_path):
    from proxion_messenger_core.handshake import send_certificate
    from proxion_messenger_core.store import MemoryStore

    alice = AgentState.generate()
    bob = AgentState.generate()
    store = MemoryStore()

    certs = [_make_cert(alice) for _ in range(3)]
    for c in certs:
        send_certificate(c, bob.store_pub_bytes, store)

    p = tmp_path / "bob.json"
    bob.save(p, PASSPHRASE.encode())

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.side_effect = lambda mid: store.list_all(mid)
        instance.take_by_ids.side_effect = lambda mid, ids: store.take_by_ids(mid, ids)
        instance.close.return_value = None

        result = _invoke(p)

    assert result.exit_code == 0
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 3
