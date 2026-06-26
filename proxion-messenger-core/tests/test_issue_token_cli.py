"""Tests for `proxion agent issue-token` CLI command."""

from unittest.mock import patch, MagicMock
import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState

PASSPHRASE = "issue-token-test"
runner = CliRunner()


@pytest.fixture
def state_with_cert(tmp_path):
    agent = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(),
        subject="ee" * 32,
        capabilities=[
            Capability(with_="/data/", can="read"),
            Capability(with_="/meta/", can="write"),
        ],
        wireguard={},
    )
    cert.sign(agent.identity_key)
    agent.certificates.append(cert)
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent, cert


def _invoke(state_path, cert_prefix, validator_url, extra=None):
    return runner.invoke(app, [
        "agent", "issue-token",
        cert_prefix,
        validator_url,
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ] + (extra or []))


def test_issue_token_happy_path(state_with_cert):
    p, agent, cert = state_with_cert

    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token_id": "t1", "alg": "proxion-ed25519-v1"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = _invoke(p, cert.certificate_id[:8], "http://localhost:8766")

    assert result.exit_code == 0
    assert "t1" in result.output


def test_issue_token_no_cert_exits_1(state_with_cert):
    p, agent, cert = state_with_cert
    result = _invoke(p, "00000000", "http://localhost:8766")
    assert result.exit_code == 1
    assert "No certificate" in result.output


def test_issue_token_ambiguous_prefix_exits_1(tmp_path):
    agent = AgentState.generate()
    # Create two certs with same first 8 chars
    base_id = "aabbccdd" + "ee" * 12
    for i in range(2):
        cert = RelationshipCertificate(
            issuer=agent.identity_pub_bytes.hex(),
            subject=f"{i:02x}" * 32,
            capabilities=[Capability(with_="/data/", can="read")],
            wireguard={},
        )
        cert.sign(agent.identity_key)
        # Manually set certificate_id to share first 8 chars
        cert.certificate_id = base_id + f"{i:016x}"
        agent.certificates.append(cert)

    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())

    result = _invoke(p, "aabbccdd", "http://localhost:8766")
    assert result.exit_code == 1
    assert "Ambiguous" in result.output


def test_issue_token_http_error_exits_1(state_with_cert):
    p, agent, cert = state_with_cert

    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")
        mock_post.return_value = mock_resp

        result = _invoke(p, cert.certificate_id[:8], "http://localhost:8766")

    assert result.exit_code == 1


def test_issue_token_uses_cert_capabilities(state_with_cert):
    p, agent, cert = state_with_cert

    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token_id": "t1"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = _invoke(p, cert.certificate_id[:8], "http://localhost:8766")

    assert result.exit_code == 0
    # Check that the permissions were sent
    call_kwargs = mock_post.call_args.kwargs
    permissions = call_kwargs["json"]["permissions"]
    assert ["read", "/data/"] in permissions
    assert ["write", "/meta/"] in permissions
