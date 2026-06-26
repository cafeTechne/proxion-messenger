"""Tests for `proxion agent list-certs` CLI command."""

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.revocation import certificate_revocation_id
import time
from datetime import datetime, timedelta, timezone

PASSPHRASE = "list-certs-test"
runner = CliRunner()


@pytest.fixture
def state_path(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent


def _invoke(state_path, extra=None):
    return runner.invoke(app, [
        "agent", "list-certs",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra or []))


def test_list_certs_empty_state(state_path):
    p, _ = state_path
    result = _invoke(p)
    assert result.exit_code == 0
    assert "No certificates" in result.output


def test_list_certs_shows_cert_id_prefix(tmp_path):
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

    result = _invoke(p)
    assert result.exit_code == 0
    assert cert.certificate_id[:8] in result.output


def test_list_certs_revoked_cert_marked(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    agent.certificates.append(cert)

    # Add revocation entry
    rev_id = certificate_revocation_id(cert)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    agent.revocation_list.revoke_until(rev_id, future)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    assert "YES" in result.output


def test_list_certs_unrevoked_cert_not_marked(tmp_path):
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

    result = _invoke(p)
    assert result.exit_code == 0
    assert "YES" not in result.output
    assert "no" in result.output.lower()


def test_list_certs_multiple_certs_all_shown(tmp_path):
    agent = AgentState.generate()
    cert_ids = []
    for i in range(3):
        cert = RelationshipCertificate(
            issuer=agent.identity_pub_bytes.hex(),
            subject=f"{i:02x}" * 32,
            capabilities=[Capability(with_="/data/", can="read")],
            wireguard={},
        )
        cert.sign(agent.identity_key)
        agent.certificates.append(cert)
        cert_ids.append(cert.certificate_id[:8])

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p)
    assert result.exit_code == 0
    for cert_id in cert_ids:
        assert cert_id in result.output
