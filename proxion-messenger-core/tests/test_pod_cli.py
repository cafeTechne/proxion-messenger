"""Tests for the 'proxion agent pod-get' and 'proxion agent pod-put' CLI commands."""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
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
def agent_with_read_write_cert(temp_state_file):
    """Create an agent state with a certificate that allows read/write."""
    # Generate agent
    agent = AgentState.generate()

    # Create a certificate with read and write capabilities
    now_ts = time.time()
    expires_at = int(now_ts) + 86400  # 1 day from now
    issuer_pub_hex = agent.identity_pub_bytes.hex()

    cert = RelationshipCertificate(
        certificate_id="cert_0123456789abcdef",
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[
            Capability(can="read", with_="/"),
            Capability(can="write", with_="/"),
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


@pytest.fixture
def agent_with_two_certs(temp_state_file):
    """Create an agent state with two certs that share an 8-char prefix."""
    agent = AgentState.generate()

    now_ts = time.time()
    expires_at = int(now_ts) + 86400
    issuer_pub_hex = agent.identity_pub_bytes.hex()

    # Two certs with same 8-char prefix for ambiguity test
    cert1 = RelationshipCertificate(
        certificate_id="cert_01234567xxxx0001",  # prefix: cert_01
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[
            Capability(can="read", with_="/"),
            Capability(can="write", with_="/"),
        ],
        wireguard={},
        expires_at=expires_at,
    )

    cert2 = RelationshipCertificate(
        certificate_id="cert_01234567xxxx0002",  # prefix: cert_01 (same!)
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[
            Capability(can="read", with_="/"),
            Capability(can="write", with_="/"),
        ],
        wireguard={},
        expires_at=expires_at,
    )

    agent.certificates.append(cert1)
    agent.certificates.append(cert2)

    passphrase = b"test-passphrase"
    agent.save(temp_state_file, passphrase)

    return temp_state_file, "test-passphrase", "cert_01"


@pytest.fixture
def temp_input_file():
    """Create a temporary input file for pod-put testing."""
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"hello world")
        temp_path = f.name
    yield temp_path
    Path(temp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# pod-get tests
# ---------------------------------------------------------------------------


def test_pod_get_happy_path(agent_with_read_write_cert):
    """pod-get successfully fetches data and writes to stdout."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()

    with patch("proxion_messenger_core.solid_auth.AuthenticatedSolidClient") as MockAuthClient:
        mock_instance = MagicMock()
        mock_instance.get.return_value = b"hello world"
        MockAuthClient.return_value = mock_instance

        result = runner.invoke(
            app,
            [
                "agent",
                "pod-get",
                "stash://alice/data/file.txt",
                "--pod-url", "http://localhost:3000",
                "--cert", cert_prefix,
                "--signing-key", signing_key,
                "--state", state_file,
                "--passphrase", passphrase,
            ],
        )

    assert result.exit_code == 0
    # Output should contain the raw bytes
    assert b"hello world" in result.stdout_bytes or "hello world" in result.stdout


def test_pod_get_with_output_file(agent_with_read_write_cert, tmp_path):
    """pod-get writes to file when --output is provided."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()
    output_file = str(tmp_path / "output.bin")

    with patch("proxion_messenger_core.solid_auth.AuthenticatedSolidClient") as MockAuthClient:
        mock_instance = MagicMock()
        mock_instance.get.return_value = b"hello world"
        MockAuthClient.return_value = mock_instance

        result = runner.invoke(
            app,
            [
                "agent",
                "pod-get",
                "stash://alice/data/file.txt",
                "--pod-url", "http://localhost:3000",
                "--cert", cert_prefix,
                "--signing-key", signing_key,
                "--output", output_file,
                "--state", state_file,
                "--passphrase", passphrase,
            ],
        )

    assert result.exit_code == 0
    assert Path(output_file).exists()
    assert Path(output_file).read_bytes() == b"hello world"
    assert "Wrote" in result.stdout


def test_pod_get_no_matching_cert_exits_1(agent_with_read_write_cert):
    """pod-get exits with 1 when certificate prefix is not found."""
    state_file, passphrase, _ = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()

    result = runner.invoke(
        app,
        [
            "agent",
            "pod-get",
            "stash://alice/data/file.txt",
            "--pod-url", "http://localhost:3000",
            "--cert", "nonexistent",
            "--signing-key", signing_key,
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "no certificate found" in result.stdout.lower()


def test_pod_get_ambiguous_cert_prefix_exits_1(agent_with_two_certs):
    """pod-get exits with 1 when cert prefix is ambiguous."""
    state_file, passphrase, cert_prefix = agent_with_two_certs
    signing_key = os.urandom(32).hex()

    result = runner.invoke(
        app,
        [
            "agent",
            "pod-get",
            "stash://alice/data/file.txt",
            "--pod-url", "http://localhost:3000",
            "--cert", cert_prefix,
            "--signing-key", signing_key,
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "ambiguous" in result.stdout.lower()


def test_pod_get_invalid_signing_key_exits_1(agent_with_read_write_cert):
    """pod-get exits with 1 when signing key is invalid hex."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "pod-get",
            "stash://alice/data/file.txt",
            "--pod-url", "http://localhost:3000",
            "--cert", cert_prefix,
            "--signing-key", "not-valid-hex",
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "invalid signing key" in result.stdout.lower()


def test_pod_get_permission_error_exits_1(agent_with_read_write_cert):
    """pod-get exits with 1 when AuthenticatedSolidClient raises PermissionError."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()

    with patch("proxion_messenger_core.solid_auth.AuthenticatedSolidClient") as MockAuthClient:
        mock_instance = MagicMock()
        mock_instance.get.side_effect = PermissionError("Access denied")
        MockAuthClient.return_value = mock_instance

        result = runner.invoke(
            app,
            [
                "agent",
                "pod-get",
                "stash://alice/data/file.txt",
                "--pod-url", "http://localhost:3000",
                "--cert", cert_prefix,
                "--signing-key", signing_key,
                "--state", state_file,
                "--passphrase", passphrase,
            ],
        )

    assert result.exit_code == 1
    assert "permission" in result.stdout.lower()


# ---------------------------------------------------------------------------
# pod-put tests
# ---------------------------------------------------------------------------


def test_pod_put_happy_path(agent_with_read_write_cert, temp_input_file):
    """pod-put successfully uploads data."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()

    with patch("proxion_messenger_core.solid_auth.AuthenticatedSolidClient") as MockAuthClient:
        mock_instance = MagicMock()
        mock_instance.put.return_value = None
        MockAuthClient.return_value = mock_instance

        result = runner.invoke(
            app,
            [
                "agent",
                "pod-put",
                "stash://alice/data/file.txt",
                temp_input_file,
                "--pod-url", "http://localhost:3000",
                "--cert", cert_prefix,
                "--signing-key", signing_key,
                "--state", state_file,
                "--passphrase", passphrase,
            ],
        )

    assert result.exit_code == 0
    assert "uploaded" in result.stdout.lower()
    assert "11 bytes" in result.stdout  # "hello world" is 11 bytes


def test_pod_put_missing_input_file_exits_1(agent_with_read_write_cert):
    """pod-put exits with 1 when input file is not found."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()

    result = runner.invoke(
        app,
        [
            "agent",
            "pod-put",
            "stash://alice/data/file.txt",
            "/nonexistent/file.bin",
            "--pod-url", "http://localhost:3000",
            "--cert", cert_prefix,
            "--signing-key", signing_key,
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "file not found" in result.stdout.lower() or "error" in result.stdout.lower()


def test_pod_put_no_matching_cert_exits_1(agent_with_read_write_cert, temp_input_file):
    """pod-put exits with 1 when certificate prefix is not found."""
    state_file, passphrase, _ = agent_with_read_write_cert
    signing_key = os.urandom(32).hex()

    result = runner.invoke(
        app,
        [
            "agent",
            "pod-put",
            "stash://alice/data/file.txt",
            temp_input_file,
            "--pod-url", "http://localhost:3000",
            "--cert", "nonexistent",
            "--signing-key", signing_key,
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "no certificate found" in result.stdout.lower()


def test_pod_put_invalid_signing_key_exits_1(agent_with_read_write_cert, temp_input_file):
    """pod-put exits with 1 when signing key is invalid hex."""
    state_file, passphrase, cert_prefix = agent_with_read_write_cert

    result = runner.invoke(
        app,
        [
            "agent",
            "pod-put",
            "stash://alice/data/file.txt",
            temp_input_file,
            "--pod-url", "http://localhost:3000",
            "--cert", cert_prefix,
            "--signing-key", "not-valid-hex",
            "--state", state_file,
            "--passphrase", passphrase,
        ],
    )

    assert result.exit_code == 1
    assert "invalid signing key" in result.stdout.lower()
