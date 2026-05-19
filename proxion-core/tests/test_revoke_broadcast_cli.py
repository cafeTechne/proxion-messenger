"""Tests for `proxion agent revoke` CLI command."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.store import MemoryStore
from proxion_messenger_core.revocation import RevocationList, certificate_revocation_id

PASSPHRASE = "revoke-test"
runner = CliRunner()

_REMOTE_STORE = "proxion_messenger_core.store_client.RemoteStore"


@pytest.fixture
def agent_with_cert(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    agent = AgentState.generate()
    peer_priv = Ed25519PrivateKey.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject=peer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    agent.certificates.append(cert)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent, cert


PEER_PUB_HEX = "ab" * 32  # fake 32-byte peer store pubkey


def _invoke_revoke(state_path, cert_id_prefix, extra_args=None):
    args = [
        "agent", "revoke",
        cert_id_prefix,
        "http://localhost:8765",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra_args or [])
    return runner.invoke(app, args)


# ---------------------------------------------------------------------------
# Happy path — revoke with --peer-store-pub
# ---------------------------------------------------------------------------

def test_revoke_with_peer_store_pub(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch(_REMOTE_STORE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg-001"
        instance.list_all.return_value = []
        instance.close.return_value = None
        instance.peek.return_value = {"count": 0, "bytes": 0, "oldest_age_s": None}

        result = _invoke_revoke(p, cert.certificate_id[:8], ["--peer-store-pub", PEER_PUB_HEX])

    assert result.exit_code == 0, result.output
    assert "revoked" in result.output.lower()


def test_revoke_posts_to_peer_mailbox(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch(_REMOTE_STORE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg-001"
        instance.list_all.return_value = []
        instance.close.return_value = None

        _invoke_revoke(p, cert.certificate_id, ["--peer-store-pub", PEER_PUB_HEX])

    # put() must have been called (notice posted to peer mailbox)
    assert instance.put.called


def test_revoke_saves_revocation_to_local_list(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch(_REMOTE_STORE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg-001"
        instance.list_all.return_value = []
        instance.close.return_value = None

        _invoke_revoke(p, cert.certificate_id[:8], ["--peer-store-pub", PEER_PUB_HEX])

    # Local revocation list should now contain the cert's revocation ID.
    # RevocationList.is_revoked() takes a token or token_id string; pass the
    # pre-computed revocation ID directly.
    from datetime import datetime, timezone
    loaded = AgentState.load(p, PASSPHRASE.encode())
    rev_id = certificate_revocation_id(cert)
    assert loaded.revocation_list.is_revoked(rev_id, datetime.now(timezone.utc))


def test_revoke_full_cert_id_also_works(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch(_REMOTE_STORE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg-001"
        instance.list_all.return_value = []
        instance.close.return_value = None

        result = _invoke_revoke(p, cert.certificate_id, ["--peer-store-pub", PEER_PUB_HEX])

    assert result.exit_code == 0


def test_revoke_with_reason(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch(_REMOTE_STORE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg-001"
        instance.list_all.return_value = []
        instance.close.return_value = None

        result = _invoke_revoke(p, cert.certificate_id[:8], [
            "--peer-store-pub", PEER_PUB_HEX,
            "--reason", "key_compromise",
        ])

    assert result.exit_code == 0
    assert "key_compromise" in result.output


# ---------------------------------------------------------------------------
# Peer pubkey auto-discovery via --peer-store-url
# ---------------------------------------------------------------------------

def test_revoke_discovers_peer_pub_from_store_url(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch("httpx.get") as mock_get, \
         patch(_REMOTE_STORE) as MockRemote:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"store_pubkey": PEER_PUB_HEX, "version": "0.1"}),
            raise_for_status=MagicMock(),
        )
        instance = MockRemote.return_value
        instance.put.return_value = "msg-001"
        instance.list_all.return_value = []
        instance.close.return_value = None

        result = _invoke_revoke(p, cert.certificate_id[:8], [
            "--peer-store-url", "http://peer:8765",
        ])

    assert result.exit_code == 0
    assert "revoked" in result.output.lower()


def test_revoke_fails_if_peer_discovery_fails(agent_with_cert):
    p, agent, cert = agent_with_cert

    with patch("httpx.get", side_effect=ConnectionError("refused")):
        result = _invoke_revoke(p, cert.certificate_id[:8], [
            "--peer-store-url", "http://peer:8765",
        ])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_revoke_unknown_cert_id_exits_1(agent_with_cert):
    p, agent, cert = agent_with_cert
    result = _invoke_revoke(p, "00000000", ["--peer-store-pub", PEER_PUB_HEX])
    assert result.exit_code == 1
    assert "No certificate found" in result.output


def test_revoke_no_peer_info_exits_1(agent_with_cert):
    p, agent, cert = agent_with_cert
    result = _invoke_revoke(p, cert.certificate_id[:8])  # no --peer-store-pub or --peer-store-url
    assert result.exit_code == 1
    assert "--peer-store-pub" in result.output or "Provide" in result.output


def test_revoke_ambiguous_prefix_exits_1(tmp_path):
    """Two certs with same prefix → ambiguous → exit 1."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    agent = AgentState.generate()
    peer_priv = Ed25519PrivateKey.generate()

    # Create two certs that both start with "00" by overriding their IDs
    for suffix in ["0011", "0022"]:
        cert = RelationshipCertificate(
            issuer=agent.identity_pub_bytes.hex(),
            subject=peer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
            capabilities=[],
            wireguard={},
        )
        cert.certificate_id = "00" + suffix + cert.certificate_id[6:]
        agent.certificates.append(cert)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke_revoke(p, "00", ["--peer-store-pub", PEER_PUB_HEX])
    assert result.exit_code == 1
    assert "Ambiguous" in result.output
