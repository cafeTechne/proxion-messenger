"""Tests for the 'proxion agent issue-from-cert' CLI command."""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState


runner = CliRunner()


@pytest.fixture
def temp_state_file():
    """Create a temporary state file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        temp_path = f.name
    yield temp_path
    # Cleanup
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def agent_with_cert(temp_state_file):
    """Create an agent state with a certificate."""
    # Generate agent
    agent = AgentState.generate()

    # Create a certificate
    now_ts = time.time()
    expires_at = int(now_ts) + 86400  # 1 day from now
    issuer_pub_hex = agent.identity_pub_bytes.hex()

    cert = RelationshipCertificate(
        certificate_id="cert_0123456789abcdef",
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[
            Capability(can="read", with_="/data/"),
        ],
        wireguard={},
        expires_at=expires_at,
    )

    # Add certificate to agent
    agent.certificates.append(cert)

    # Save state with a test passphrase
    passphrase = b"test-passphrase"
    agent.save(temp_state_file, passphrase)

    return temp_state_file, "test-passphrase", cert.certificate_id[:8]


def test_issue_from_cert_happy_path(agent_with_cert):
    """issue-from-cert successfully mints a certificate-bounded token."""
    state_file, passphrase, cert_prefix = agent_with_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            cert_prefix,
            "--perm", "read:/data/file.txt",
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 0
    # Parse the output as JSON
    output_json = json.loads(result.stdout)
    assert "token_id" in output_json
    assert output_json["permissions"] == [["read", "/data/file.txt"]]
    assert "signature" in output_json
    assert "exp" in output_json


def test_issue_from_cert_scope_exceeded_exits_1(agent_with_cert):
    """issue-from-cert rejects permissions outside the certificate scope."""
    state_file, passphrase, cert_prefix = agent_with_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            cert_prefix,
            "--perm", "write:/data/file.txt",  # cert only allows "read"
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "error" in result.stdout.lower() or "scope" in result.stdout.lower() or "covered" in result.stdout.lower()


def test_issue_from_cert_unknown_prefix_exits_1(agent_with_cert):
    """issue-from-cert exits with 1 when certificate prefix is not found."""
    state_file, passphrase, _ = agent_with_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            "nonexistent",
            "--perm", "read:/data/",
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "no certificate found" in result.stdout.lower()


def test_issue_from_cert_output_is_valid_token(agent_with_cert):
    """Output of issue-from-cert is a valid token JSON."""
    state_file, passphrase, cert_prefix = agent_with_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            cert_prefix,
            "--perm", "read:/data/",
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 0
    token = json.loads(result.stdout)

    # Verify all required token fields
    assert "token_id" in token
    assert "permissions" in token
    assert "exp" in token
    assert "aud" in token
    assert "signature" in token
    assert "holder_key_fingerprint" in token
    assert isinstance(token["permissions"], list)


def test_issue_from_cert_malformed_perm_exits_1(agent_with_cert):
    """issue-from-cert exits with 1 when permission format is malformed."""
    state_file, passphrase, cert_prefix = agent_with_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            cert_prefix,
            "--perm", "bad-no-colon",  # Missing colon
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "invalid permission format" in result.stdout.lower()


def test_issue_from_cert_multiple_permissions(agent_with_cert):
    """issue-from-cert supports multiple --perm options."""
    state_file, passphrase, cert_prefix = agent_with_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            cert_prefix,
            "--perm", "read:/data/file1.txt",
            "--perm", "read:/data/file2.txt",
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 0
    token = json.loads(result.stdout)
    # Both permissions should be in the token
    perm_list = [[p[0], p[1]] for p in token["permissions"]]
    assert ["read", "/data/file1.txt"] in perm_list
    assert ["read", "/data/file2.txt"] in perm_list


def test_issue_from_cert_with_custom_ttl(agent_with_cert):
    """issue-from-cert respects the --ttl option."""
    state_file, passphrase, cert_prefix = agent_with_cert
    import datetime

    before = datetime.datetime.now(datetime.timezone.utc)

    result = runner.invoke(
        app,
        [
            "agent",
            "issue-from-cert",
            cert_prefix,
            "--perm", "read:/data/",
            "--ttl", "7200",  # 2 hours
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    after = datetime.datetime.now(datetime.timezone.utc)
    assert result.exit_code == 0
    token = json.loads(result.stdout)

    # Parse exp and verify it's roughly 2 hours from now
    exp_dt = datetime.datetime.fromisoformat(token["exp"])
    diff = (exp_dt - before).total_seconds()
    # Should be approximately 7200 seconds (with some tolerance for execution time)
    assert 7190 < diff < 7210
