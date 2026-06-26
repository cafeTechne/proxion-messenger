"""Tests for `proxion agent renew` CLI command."""

from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState

PASSPHRASE = "renew-test"
runner = CliRunner()
_REMOTE = "proxion_messenger_core.store_client.RemoteStore"
PEER_PUB_HEX = "cd" * 32


@pytest.fixture
def state_with_cert(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    agent.certificates.append(cert)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent, cert


def _invoke(state_path, cert_prefix, extra=None):
    return runner.invoke(app, [
        "agent", "renew",
        cert_prefix,
        "http://localhost:8765",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra or []))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_renew_sends_invite_with_same_caps(state_with_cert):
    p, agent, cert = state_with_cert

    sent_invites = []

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.side_effect = lambda mid, env: (sent_invites.append(env), "msg1")[1]
        instance.close.return_value = None

        result = _invoke(p, cert.certificate_id[:8], ["--peer-store-pub", PEER_PUB_HEX])

    assert result.exit_code == 0, result.output
    assert "sent" in result.output.lower()
    assert instance.put.called


def test_renew_creates_pending_invite(state_with_cert):
    p, agent, cert = state_with_cert

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg1"
        instance.close.return_value = None

        _invoke(p, cert.certificate_id[:8], ["--peer-store-pub", PEER_PUB_HEX])

    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.pending_invites) == 1
    assert loaded.pending_invites[0].peer_store_pub_hex == PEER_PUB_HEX


def test_renew_shows_cert_id_in_output(state_with_cert):
    p, agent, cert = state_with_cert

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg1"
        instance.close.return_value = None

        result = _invoke(p, cert.certificate_id[:8], ["--peer-store-pub", PEER_PUB_HEX])

    assert cert.certificate_id[:8] in result.output


def test_renew_shows_capabilities(state_with_cert):
    p, agent, cert = state_with_cert

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg1"
        instance.close.return_value = None

        result = _invoke(p, cert.certificate_id[:8], ["--peer-store-pub", PEER_PUB_HEX])

    assert "read" in result.output


def test_renew_with_peer_store_url(state_with_cert):
    p, agent, cert = state_with_cert

    with patch("httpx.get") as mock_get, \
         patch(_REMOTE) as MockRemote:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"store_pubkey": PEER_PUB_HEX}),
            raise_for_status=MagicMock(),
        )
        instance = MockRemote.return_value
        instance.put.return_value = "msg1"
        instance.close.return_value = None

        result = _invoke(p, cert.certificate_id[:8], ["--peer-store-url", "http://peer:8765"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_renew_unknown_cert_id_exits_1(state_with_cert):
    p, agent, cert = state_with_cert
    result = _invoke(p, "00000000", ["--peer-store-pub", PEER_PUB_HEX])
    assert result.exit_code == 1
    assert "No certificate" in result.output


def test_renew_no_peer_info_exits_1(state_with_cert):
    p, agent, cert = state_with_cert
    result = _invoke(p, cert.certificate_id[:8])  # no --peer-store-pub or --peer-store-url
    assert result.exit_code == 1


def test_renew_full_cert_id_works(state_with_cert):
    p, agent, cert = state_with_cert

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.put.return_value = "msg1"
        instance.close.return_value = None

        result = _invoke(p, cert.certificate_id, ["--peer-store-pub", PEER_PUB_HEX])

    assert result.exit_code == 0
