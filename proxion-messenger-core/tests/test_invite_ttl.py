"""Tests for invite TTL enforcement — purge_expired_invites and CLI integration."""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, FederationInvite
from proxion_messenger_core.persist import AgentState, PendingInvite

PASSPHRASE = "ttl-test"
runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_invite(agent: AgentState, expires_offset: int = 3600) -> FederationInvite:
    """Create a signed invite with a custom expiry offset from now."""
    inv = FederationInvite(
        issuer={"public_key": agent.identity_pub_bytes.hex()},
        endpoint_hints=[],
        capabilities=[],
    )
    inv.expires_at = int(time.time()) + expires_offset
    inv.sign(agent.identity_key)
    return inv


def _make_pending(agent: AgentState, expires_offset: int = 3600) -> PendingInvite:
    inv = _make_invite(agent, expires_offset)
    return PendingInvite(invite=inv, peer_store_pub_hex="ab" * 32, sent_at=time.time())


@pytest.fixture
def agent():
    return AgentState.generate()


# ---------------------------------------------------------------------------
# AgentState.purge_expired_invites — unit tests
# ---------------------------------------------------------------------------

def test_purge_empty_returns_empty(agent):
    assert agent.purge_expired_invites() == []


def test_purge_no_expired_leaves_all(agent):
    pi = _make_pending(agent, expires_offset=+3600)
    agent.pending_invites.append(pi)
    removed = agent.purge_expired_invites()
    assert removed == []
    assert len(agent.pending_invites) == 1


def test_purge_removes_expired(agent):
    pi_fresh = _make_pending(agent, expires_offset=+3600)
    pi_old = _make_pending(agent, expires_offset=-1)  # already expired
    agent.pending_invites.extend([pi_fresh, pi_old])
    removed = agent.purge_expired_invites()
    assert len(removed) == 1
    assert removed[0] is pi_old
    assert len(agent.pending_invites) == 1
    assert agent.pending_invites[0] is pi_fresh


def test_purge_removes_all_when_all_expired(agent):
    for _ in range(3):
        agent.pending_invites.append(_make_pending(agent, expires_offset=-1))
    removed = agent.purge_expired_invites()
    assert len(removed) == 3
    assert agent.pending_invites == []


def test_purge_respects_explicit_now(agent):
    future_now = time.time() + 7200  # 2 hours in the future
    pi = _make_pending(agent, expires_offset=+3600)  # expires 1h from now
    agent.pending_invites.append(pi)
    # With future_now, the invite looks expired
    removed = agent.purge_expired_invites(now=future_now)
    assert len(removed) == 1
    assert agent.pending_invites == []


def test_purge_boundary_exactly_at_expiry(agent):
    pi = _make_pending(agent, expires_offset=0)  # expires_at == now
    pi.invite.expires_at = int(time.time()) - 1  # strictly in the past
    agent.pending_invites.append(pi)
    removed = agent.purge_expired_invites()
    assert len(removed) == 1


def test_purge_persisted_after_save_load(tmp_path, agent):
    pi_fresh = _make_pending(agent, expires_offset=+3600)
    pi_old = _make_pending(agent, expires_offset=-1)
    agent.pending_invites.extend([pi_fresh, pi_old])

    agent.purge_expired_invites()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.pending_invites) == 1
    assert loaded.pending_invites[0].invite.invitation_id == pi_fresh.invite.invitation_id


# ---------------------------------------------------------------------------
# CLI — agent finalize purges expired invites automatically
# ---------------------------------------------------------------------------

def _state_with_expired_invite(tmp_path, agent):
    pi_expired = _make_pending(agent, expires_offset=-1)
    agent.pending_invites.append(pi_expired)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, pi_expired


def test_finalize_purges_expired_before_checking_acceptances(tmp_path, agent):
    p, pi_expired = _state_with_expired_invite(tmp_path, agent)

    with patch("proxion_messenger_core.store_client.RemoteStore") as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = []
        instance.take_all.return_value = []
        instance.close.return_value = None

        result = runner.invoke(app, [
            "agent", "finalize", "http://localhost:8765",
            "--state", str(p),
            "--passphrase", PASSPHRASE,
        ])

    assert result.exit_code == 0
    assert "expired" in result.output.lower()

    # State on disk should have the expired invite removed
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.pending_invites) == 0


def test_finalize_keeps_fresh_invites_after_purge(tmp_path, agent):
    pi_fresh = _make_pending(agent, expires_offset=+3600)
    pi_old = _make_pending(agent, expires_offset=-1)
    agent.pending_invites.extend([pi_fresh, pi_old])
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    with patch("proxion_messenger_core.store_client.RemoteStore") as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = []
        instance.take_all.return_value = []
        instance.close.return_value = None

        runner.invoke(app, [
            "agent", "finalize", "http://localhost:8765",
            "--state", str(p),
            "--passphrase", PASSPHRASE,
        ])

    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.pending_invites) == 1
    assert loaded.pending_invites[0].invite.invitation_id == pi_fresh.invite.invitation_id


# ---------------------------------------------------------------------------
# CLI — agent status flags expired invites
# ---------------------------------------------------------------------------

def test_status_shows_expired_label_for_expired_invite(tmp_path, agent):
    pi_old = _make_pending(agent, expires_offset=-3600)
    agent.pending_invites.append(pi_old)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = runner.invoke(app, [
        "agent", "status",
        "--state", str(p),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code == 0
    assert "EXPIRED" in result.output


def test_status_no_expired_label_for_fresh_invite(tmp_path, agent):
    pi_fresh = _make_pending(agent, expires_offset=+3600)
    agent.pending_invites.append(pi_fresh)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = runner.invoke(app, [
        "agent", "status",
        "--state", str(p),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code == 0
    assert "EXPIRED" not in result.output
