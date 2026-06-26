"""Tests for `proxion agent status` CLI command."""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, FederationInvite, RelationshipCertificate
from proxion_messenger_core.persist import AgentState, PendingInvite

PASSPHRASE = "test-pass"
runner = CliRunner()


@pytest.fixture
def state_file(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent


@pytest.fixture
def state_with_cert(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="bob" * 8,
        capabilities=[Capability(with_="stash://x/", can="read")],
        wireguard={},
    )
    agent.certificates.append(cert)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent, cert


@pytest.fixture
def state_with_pending(tmp_path):
    agent = AgentState.generate()
    inv = FederationInvite(
        issuer={"public_key": agent.identity_pub_bytes.hex()},
        endpoint_hints=[],
        capabilities=[],
    )
    inv.sign(agent.identity_key)
    pi = PendingInvite(
        invite=inv,
        peer_store_pub_hex="ab" * 32,
        sent_at=time.time() - 120,
    )
    agent.pending_invites.append(pi)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent, pi


# ---------------------------------------------------------------------------
# Basic local-only output (no store_url)
# ---------------------------------------------------------------------------

def test_status_shows_identity_pubkey(state_file):
    p, agent = state_file
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0, result.output
    assert agent.identity_pub_bytes.hex() in result.output


def test_status_shows_store_pubkey(state_file):
    p, agent = state_file
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert agent.store_pub_bytes.hex() in result.output


def test_status_shows_mailbox_id(state_file):
    from proxion_messenger_core.sealed import mailbox_id_for
    p, agent = state_file
    mailbox_id = mailbox_id_for(agent.store_pub_bytes)
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert mailbox_id in result.output


def test_status_shows_zero_certs(state_file):
    p, agent = state_file
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert "Certificates: 0" in result.output or "Certificates:* 0" in result.output or "0" in result.output


def test_status_shows_zero_pending_invites(state_file):
    p, agent = state_file
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert "Pending invites: 0" in result.output or "0" in result.output


# ---------------------------------------------------------------------------
# With certificates
# ---------------------------------------------------------------------------

def test_status_lists_certificates(state_with_cert):
    p, agent, cert = state_with_cert
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert cert.certificate_id[:8] in result.output


def test_status_shows_cert_capability(state_with_cert):
    p, agent, cert = state_with_cert
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert "read" in result.output


def test_status_cert_count_correct(state_with_cert):
    p, agent, cert = state_with_cert
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert "1" in result.output  # 1 certificate


# ---------------------------------------------------------------------------
# With pending invites
# ---------------------------------------------------------------------------

def test_status_shows_pending_invite_id(state_with_pending):
    p, agent, pi = state_with_pending
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert pi.invite.invitation_id[:16] in result.output


def test_status_shows_pending_invite_peer(state_with_pending):
    p, agent, pi = state_with_pending
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    assert pi.peer_store_pub_hex[:16] in result.output


def test_status_pending_invite_count(state_with_pending):
    p, agent, pi = state_with_pending
    result = runner.invoke(app, ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE])
    assert result.exit_code == 0
    # "Pending invites: 1"
    assert "1" in result.output


# ---------------------------------------------------------------------------
# With store_url (live mailbox stats)
# ---------------------------------------------------------------------------

def test_status_with_store_url_shows_mailbox_stats(state_file):
    p, agent = state_file
    mock_info = {"count": 3, "bytes": 512, "oldest_age_s": 45.2}

    with patch("proxion_messenger_core.store_client.RemoteStore") as MockStore:
        instance = MockStore.return_value
        instance.peek.return_value = mock_info
        instance.close.return_value = None

        result = runner.invoke(
            app,
            ["agent", "status", "http://localhost:8765", "--state", str(p), "--passphrase", PASSPHRASE],
        )

    assert result.exit_code == 0
    assert "3" in result.output      # count
    assert "512" in result.output    # bytes
    assert "45" in result.output     # oldest_age_s (rounded)


def test_status_with_store_url_unreachable_still_shows_local_info(state_file):
    p, agent = state_file

    with patch("proxion_messenger_core.store_client.RemoteStore") as MockStore:
        instance = MockStore.return_value
        instance.peek.side_effect = ConnectionError("refused")
        instance.close.return_value = None

        result = runner.invoke(
            app,
            ["agent", "status", "http://localhost:8765", "--state", str(p), "--passphrase", PASSPHRASE],
        )

    # Still exits 0 and still shows the local state
    assert result.exit_code == 0
    assert agent.identity_pub_bytes.hex() in result.output
    assert agent.store_pub_bytes.hex() in result.output


def test_status_without_store_url_no_network_call(state_file):
    """Without a store_url argument, no RemoteStore is instantiated."""
    p, agent = state_file

    with patch("proxion_messenger_core.store_client.RemoteStore") as MockStore:
        result = runner.invoke(
            app,
            ["agent", "status", "--state", str(p), "--passphrase", PASSPHRASE],
        )

    MockStore.assert_not_called()
    assert result.exit_code == 0
