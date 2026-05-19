"""Tests for `proxion agent pull-revocations` CLI command."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from proxion_messenger_core.cli import app
from proxion_messenger_core.persist import AgentState

PASSPHRASE = "pull-rev-test"
runner = CliRunner()
_REMOTE = "proxion_messenger_core.store_client.RemoteStore"


@pytest.fixture
def state_file(tmp_path):
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    return p, agent


def _invoke(state_path):
    return runner.invoke(app, [
        "agent", "pull-revocations", "http://localhost:8765",
        "--state", str(state_path),
        "--passphrase", PASSPHRASE,
    ])


# ---------------------------------------------------------------------------
# Empty mailbox
# ---------------------------------------------------------------------------

def test_pull_revocations_empty_mailbox(state_file):
    p, _ = state_file
    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = []
        instance.take_by_ids.return_value = []
        instance.close.return_value = None
        result = _invoke(p)
    assert result.exit_code == 0
    assert "No revocation" in result.output


# ---------------------------------------------------------------------------
# Valid revocation notice applied
# ---------------------------------------------------------------------------

def test_pull_revocations_applies_notice(tmp_path):
    import os
    from datetime import datetime, timezone, timedelta
    from proxion_messenger_core.store import MemoryStore
    from proxion_messenger_core.revoke import (
        create_certificate_revocation, broadcast_revocation,
    )
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    issuer_priv = Ed25519PrivateKey.generate()
    bob = AgentState.generate()

    cert = RelationshipCertificate(
        issuer=issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        subject=bob.identity_pub_bytes.hex(),
        capabilities=[Capability(with_="/", can="read")],
        wireguard={},
    )

    notice = create_certificate_revocation(cert, issuer_priv, reason="test")
    store = MemoryStore()
    broadcast_revocation(notice, [bob.store_pub_bytes], store)

    p = tmp_path / "bob.json"
    bob.save(p, PASSPHRASE.encode())

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.side_effect = lambda mid: store.list_all(mid)
        instance.take_by_ids.side_effect = lambda mid, ids: store.take_by_ids(mid, ids)
        instance.close.return_value = None
        result = _invoke(p)

    assert result.exit_code == 0, result.output
    assert "applied" in result.output.lower() or "1" in result.output

    # Revocation should be persisted
    from proxion_messenger_core.revocation import certificate_revocation_id
    loaded = AgentState.load(p, PASSPHRASE.encode())
    rev_id = certificate_revocation_id(cert)
    assert loaded.revocation_list.is_revoked(rev_id, datetime.now(timezone.utc))


def test_pull_revocations_saves_state(tmp_path):
    """State file is updated after applying notices."""
    import os
    from datetime import datetime, timezone
    from proxion_messenger_core.store import MemoryStore
    from proxion_messenger_core.revoke import create_certificate_revocation, broadcast_revocation
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    issuer_priv = Ed25519PrivateKey.generate()
    bob = AgentState.generate()
    cert = RelationshipCertificate(
        issuer=issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        subject=bob.identity_pub_bytes.hex(),
        capabilities=[],
        wireguard={},
    )
    notice = create_certificate_revocation(cert, issuer_priv)
    store = MemoryStore()
    broadcast_revocation(notice, [bob.store_pub_bytes], store)

    p = tmp_path / "bob.json"
    bob.save(p, PASSPHRASE.encode())
    mtime_before = p.stat().st_mtime

    import time; time.sleep(0.05)

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.side_effect = lambda mid: store.list_all(mid)
        instance.take_by_ids.side_effect = lambda mid, ids: store.take_by_ids(mid, ids)
        instance.close.return_value = None
        _invoke(p)

    assert p.stat().st_mtime > mtime_before


def test_pull_revocations_no_save_on_empty(tmp_path):
    """State file is NOT rewritten when mailbox is empty."""
    agent = AgentState.generate()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE.encode())
    mtime_before = p.stat().st_mtime

    import time; time.sleep(0.05)

    with patch(_REMOTE) as MockRemote:
        instance = MockRemote.return_value
        instance.list_all.return_value = []
        instance.take_by_ids.return_value = []
        instance.close.return_value = None
        _invoke(p)

    assert p.stat().st_mtime == mtime_before
