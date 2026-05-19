"""Tests for the 'proxion doctor' CLI command."""

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
def agent_no_certs(temp_state_file):
    """Create an agent state with no certificates."""
    agent = AgentState.generate()
    passphrase = b"test-passphrase"
    agent.save(temp_state_file, passphrase)
    return temp_state_file, "test-passphrase"


@pytest.fixture
def agent_with_two_valid_certs(temp_state_file):
    """Create an agent state with two valid certificates."""
    agent = AgentState.generate()

    now_ts = time.time()
    expires_at = int(now_ts) + 86400  # 1 day from now
    issuer_pub_hex = agent.identity_pub_bytes.hex()

    cert1 = RelationshipCertificate(
        certificate_id="cert_00000000aaaa0001",
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[Capability(can="read", with_="/")],
        wireguard={},
        expires_at=expires_at,
    )

    cert2 = RelationshipCertificate(
        certificate_id="cert_00000000bbbb0002",
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[Capability(can="write", with_="/")],
        wireguard={},
        expires_at=expires_at,
    )

    agent.certificates.append(cert1)
    agent.certificates.append(cert2)

    passphrase = b"test-passphrase"
    agent.save(temp_state_file, passphrase)

    return temp_state_file, "test-passphrase"


@pytest.fixture
def agent_with_one_expired_one_valid(temp_state_file):
    """Create an agent state with one expired and one valid certificate."""
    agent = AgentState.generate()

    now_ts = time.time()
    expires_at_valid = int(now_ts) + 86400  # 1 day from now
    expires_at_expired = int(now_ts) - 86400  # 1 day ago (expired)
    issuer_pub_hex = agent.identity_pub_bytes.hex()

    cert_expired = RelationshipCertificate(
        certificate_id="cert_expired0001xxxx",
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[Capability(can="read", with_="/")],
        wireguard={},
        expires_at=expires_at_expired,
    )

    cert_valid = RelationshipCertificate(
        certificate_id="cert_valid000001xxxx",
        issuer=issuer_pub_hex,
        subject=issuer_pub_hex,
        capabilities=[Capability(can="write", with_="/")],
        wireguard={},
        expires_at=expires_at_valid,
    )

    agent.certificates.append(cert_expired)
    agent.certificates.append(cert_valid)

    passphrase = b"test-passphrase"
    agent.save(temp_state_file, passphrase)

    return temp_state_file, "test-passphrase"


# ---------------------------------------------------------------------------
# doctor tests
# ---------------------------------------------------------------------------


def test_doctor_happy_path_no_certs(agent_no_certs):
    """doctor exits 0 with valid agent state and no certs."""
    state_file, passphrase = agent_no_certs
    result = runner.invoke(app, ["doctor", "--state", state_file, "--passphrase", passphrase])
    assert result.exit_code == 0
    assert "agent state" in result.stdout.lower()


def test_doctor_two_valid_certs(agent_with_two_valid_certs):
    """doctor shows 2 valid certificates."""
    state_file, passphrase = agent_with_two_valid_certs
    result = runner.invoke(app, ["doctor", "--state", state_file, "--passphrase", passphrase])
    assert result.exit_code == 0
    assert "2 valid" in result.stdout


def test_doctor_one_expired_one_valid(agent_with_one_expired_one_valid):
    """doctor shows 1 expired and 1 valid certificate."""
    state_file, passphrase = agent_with_one_expired_one_valid
    result = runner.invoke(app, ["doctor", "--state", state_file, "--passphrase", passphrase])
    assert result.exit_code == 0
    assert "1 expired" in result.stdout
    assert "1 valid" in result.stdout


def test_doctor_store_reachable(agent_no_certs):
    """doctor reaches store and shows latency."""
    state_file, passphrase = agent_no_certs
    store_url = "http://localhost:8765"
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"store_pubkey": "aa" * 32}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        result = runner.invoke(app, ["doctor", "--state", state_file, "--passphrase", passphrase, "--store-url", store_url])
    assert result.exit_code == 0
    assert "store reachable" in result.stdout.lower()


def test_doctor_store_unreachable(agent_no_certs):
    """doctor reports store unreachable."""
    state_file, passphrase = agent_no_certs
    store_url = "http://localhost:8765"
    with patch("httpx.get") as mock_get:
        import httpx
        mock_get.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["doctor", "--state", state_file, "--passphrase", passphrase, "--store-url", store_url])
    assert result.exit_code == 1
    assert "store" in result.stdout.lower()


def test_doctor_no_state_file_exits_1():
    """doctor exits 1 when state file is missing."""
    result = runner.invoke(app, ["doctor", "--state", "/nonexistent/state.json", "--passphrase", "test"])
    assert result.exit_code == 1
