"""Tests for `proxion agent prune-certs` CLI command."""

import pytest
import time
from datetime import datetime, timedelta, timezone
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.revocation import certificate_revocation_id

PASSPHRASE = "prune-certs-test"
runner = CliRunner()


def _invoke(state_path, extra=None):
    return runner.invoke(app, [
        "agent", "prune-certs",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra or []))


def test_prune_certs_removes_expired(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    # Set expires_at to past
    cert.expires_at = int(time.time()) - 1
    agent.certificates.append(cert)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    assert "Pruned" in result.output
    assert "1" in result.output

    # Reload and verify cert is gone
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 0


def test_prune_certs_removes_revoked(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    agent.certificates.append(cert)

    # Add revocation
    rev_id = certificate_revocation_id(cert)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    agent.revocation_list.revoke_until(rev_id, future)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    assert "Pruned" in result.output

    # Reload and verify cert is gone
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 0


def test_prune_certs_keeps_valid_cert(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    # Set far-future expiry
    cert.expires_at = int(time.time()) + 86400 * 90
    agent.certificates.append(cert)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    assert "Nothing to prune" in result.output

    # Reload and verify cert is still there
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 1


def test_prune_certs_dry_run_does_not_save(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    cert.expires_at = int(time.time()) - 1
    agent.certificates.append(cert)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p, ["--dry-run"])
    assert result.exit_code == 0
    assert "Would prune" in result.output

    # Reload and verify cert is still there (not saved)
    loaded = AgentState.load(p, PASSPHRASE.encode())
    assert len(loaded.certificates) == 1


def test_prune_certs_empty_state_nothing_to_prune(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    assert "Nothing to prune" in result.output
