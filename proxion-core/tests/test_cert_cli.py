"""CLI tests for cert delegate/renew/verify/info and ledger revoke commands."""

import json
import os
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from typer.testing import CliRunner

from proxion_messenger_core import MemoryStore, run_local_handshake
from proxion_messenger_core.cli import app
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.persist import AgentState

runner = CliRunner()
PASSPHRASE = "test-passphrase"


@pytest.fixture
def alice(tmp_path):
    """An AgentState saved to a temp file."""
    agent = AgentState.generate()
    state_file = tmp_path / "alice.json"
    agent.save(state_file, PASSPHRASE.encode())
    return agent, state_file


@pytest.fixture
def cert_and_alice(alice, tmp_path):
    """A RelationshipCertificate where alice is issuer, saved to a temp JSON file."""
    agent, state_file = alice
    bob_id = Ed25519PrivateKey.generate()
    bob_store = X25519PrivateKey.generate()
    caps = [Capability(can="read", with_="stash://messages/")]
    store = MemoryStore()
    certificate, valid = run_local_handshake(
        alice_identity_priv=agent.identity_key,
        alice_store_priv=agent.store_key,
        bob_identity_priv=bob_id,
        bob_store_priv=bob_store,
        alice_capabilities=caps,
        bob_capabilities=caps,
        store=store,
    )
    assert valid
    cert_file = tmp_path / "cert.json"
    cert_file.write_text(json.dumps(certificate.to_dict(), indent=2))
    return certificate, cert_file, state_file


# ---------------------------------------------------------------------------
# cert delegate (Task 11)
# ---------------------------------------------------------------------------

def test_cert_delegate_creates_output_file(cert_and_alice, tmp_path):
    certificate, cert_file, state_file = cert_and_alice
    device_key = Ed25519PrivateKey.generate()
    device_pub_hex = device_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    output = tmp_path / "delegation.json"

    result = runner.invoke(app, [
        "cert", "delegate",
        "--cert-file", str(cert_file),
        "--issuer-key-file", str(state_file),
        "--device-pub-hex", device_pub_hex,
        "--output", str(output),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code == 0, result.output
    assert output.exists()
    delegation = json.loads(output.read_text())
    assert delegation["issuer"] == certificate.issuer
    assert delegation["subject"] == device_pub_hex


def test_cert_delegate_exits_nonzero_on_issuer_mismatch(cert_and_alice, tmp_path):
    """cert delegate fails when the key file is not the cert issuer."""
    certificate, cert_file, _ = cert_and_alice
    # Create a different agent (not the cert issuer)
    wrong_agent = AgentState.generate()
    wrong_state = tmp_path / "wrong.json"
    wrong_agent.save(wrong_state, PASSPHRASE.encode())

    device_key = Ed25519PrivateKey.generate()
    device_pub_hex = device_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    output = tmp_path / "delegation.json"

    result = runner.invoke(app, [
        "cert", "delegate",
        "--cert-file", str(cert_file),
        "--issuer-key-file", str(wrong_state),
        "--device-pub-hex", device_pub_hex,
        "--output", str(output),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# cert renew (Task 12)
# ---------------------------------------------------------------------------

def test_cert_renew_creates_renewed_cert(cert_and_alice, tmp_path):
    certificate, cert_file, state_file = cert_and_alice
    output = tmp_path / "renewed.json"

    result = runner.invoke(app, [
        "cert", "renew",
        "--cert-file", str(cert_file),
        "--issuer-key-file", str(state_file),
        "--ttl-days", "180",
        "--output", str(output),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code == 0, result.output
    assert output.exists()
    renewed = json.loads(output.read_text())
    assert renewed["expires_at"] > certificate.expires_at
    assert renewed["certificate_id"] != certificate.certificate_id


def test_cert_renew_exits_nonzero_on_bad_issuer(cert_and_alice, tmp_path):
    certificate, cert_file, _ = cert_and_alice
    wrong_agent = AgentState.generate()
    wrong_state = tmp_path / "wrong.json"
    wrong_agent.save(wrong_state, PASSPHRASE.encode())
    output = tmp_path / "renewed.json"

    result = runner.invoke(app, [
        "cert", "renew",
        "--cert-file", str(cert_file),
        "--issuer-key-file", str(wrong_state),
        "--ttl-days", "90",
        "--output", str(output),
        "--passphrase", PASSPHRASE,
    ])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# cert verify (Task 13)
# ---------------------------------------------------------------------------

def test_cert_verify_valid_cert(cert_and_alice, tmp_path):
    certificate, cert_file, _ = cert_and_alice

    result = runner.invoke(app, [
        "cert", "verify",
        "--cert-file", str(cert_file),
    ])
    assert result.exit_code == 0, result.output
    assert "VALID" in result.output


def test_cert_verify_invalid_cert_exits_nonzero(cert_and_alice, tmp_path):
    certificate, cert_file, _ = cert_and_alice
    # Tamper with the cert JSON (change one char of the signature)
    cert_data = json.loads(cert_file.read_text())
    sig = cert_data["signature"]
    cert_data["signature"] = sig[:-2] + ("00" if sig[-2:] != "00" else "ff")
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(cert_data))

    result = runner.invoke(app, [
        "cert", "verify",
        "--cert-file", str(tampered),
    ])
    assert result.exit_code != 0
    assert "INVALID" in result.output


# ---------------------------------------------------------------------------
# cert info (Task 14)
# ---------------------------------------------------------------------------

def test_cert_info_displays_fields(cert_and_alice, tmp_path):
    certificate, cert_file, _ = cert_and_alice

    result = runner.invoke(app, [
        "cert", "info",
        "--cert-file", str(cert_file),
    ])
    assert result.exit_code == 0, result.output
    # cert_id prefix, issuer prefix, expiry should all appear
    assert certificate.certificate_id[:12] in result.output
    assert certificate.issuer[:16] in result.output
    assert "read" in result.output


def test_cert_info_shows_expired_flag(tmp_path):
    # Create a cert that expired in the past
    alice_id = Ed25519PrivateKey.generate()
    alice_pub_hex = alice_id.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    bob_id = Ed25519PrivateKey.generate()
    bob_pub_hex = bob_id.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    expired_cert = RelationshipCertificate(
        issuer=alice_pub_hex,
        subject=bob_pub_hex,
        capabilities=[Capability(can="read", with_="stash://messages/")],
        wireguard={},
        expires_at=int(time.time()) - 1000,
    )
    expired_cert.sign(alice_id)

    cert_file = tmp_path / "expired.json"
    cert_file.write_text(json.dumps(expired_cert.to_dict(), indent=2))

    result = runner.invoke(app, [
        "cert", "info",
        "--cert-file", str(cert_file),
    ])
    assert result.exit_code == 0
    assert "EXPIRED" in result.output


# ---------------------------------------------------------------------------
# ledger revoke (Task 15)
# ---------------------------------------------------------------------------

def test_ledger_revoke_reports_count(cert_and_alice, tmp_path):
    """Issue 2 tokens into a SQLite ledger; ledger revoke reports Revoked 2."""
    import os as _os
    certificate, cert_file, _ = cert_and_alice
    sk = _os.urandom(32)
    ledger_path = str(tmp_path / "ledger.db")

    # Populate the ledger with 2 tokens
    from proxion_messenger_core.store_sqlite import SqliteStore
    from proxion_messenger_core.certtoken import issue_from_certificate
    from proxion_messenger_core.persist import AgentState
    from datetime import datetime, timezone

    bob_id = Ed25519PrivateKey.generate()
    ledger = SqliteStore(ledger_path)
    now = datetime.now(timezone.utc)
    for _ in range(2):
        issue_from_certificate(
            cert=certificate,
            requested_permissions=[("read", "stash://messages/")],
            holder_pub_key=bob_id.public_key(),
            signing_key=sk,
            now=now,
            store=ledger,
        )

    result = runner.invoke(app, [
        "ledger", "revoke",
        "--cert-file", str(cert_file),
        "--ledger-path", ledger_path,
    ])
    assert result.exit_code == 0, result.output
    assert "2" in result.output


def test_ledger_revoke_dry_run_reports_without_error(cert_and_alice, tmp_path):
    """--dry-run reports count without error even when ledger is empty."""
    certificate, cert_file, _ = cert_and_alice
    ledger_path = str(tmp_path / "empty_ledger.db")
    # Create an empty SQLite store
    from proxion_messenger_core.store_sqlite import SqliteStore
    SqliteStore(ledger_path)  # creates the DB

    result = runner.invoke(app, [
        "ledger", "revoke",
        "--cert-file", str(cert_file),
        "--ledger-path", ledger_path,
        "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert "0" in result.output
