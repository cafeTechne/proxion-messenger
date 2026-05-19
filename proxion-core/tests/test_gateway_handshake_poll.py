"""Tests for cert expiry warning in _poll_handshake_completions."""
import json
import asyncio
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
import tempfile

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


@pytest.fixture
def mock_agent():
    """Create a mock agent with real crypto keys."""
    identity_key = Ed25519PrivateKey.generate()
    store_key = X25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    agent.identity_key = identity_key
    agent.identity_pub_bytes = identity_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    agent.store_pub_bytes = store_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    agent.store_key = store_key
    return agent


@pytest.fixture
def local_store_db():
    """Create a temporary local store database."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    store = LocalStore(str(db_path))
    yield store
    # Cleanup manually to avoid Windows file locking issues
    import shutil
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def gateway(mock_agent, local_store_db):
    """Create a gateway with local store."""
    config = GatewayConfig(port=9999)
    gateway = ProxionGateway(
        agent=mock_agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState()
    )
    gateway._store = local_store_db
    return gateway


@pytest.mark.asyncio
async def test_poll_broadcasts_cert_expiring_soon(gateway, local_store_db):
    """Certs expiring within 7 days should trigger a cert_expiring_soon broadcast."""
    import time as _time
    # Save a relationship expiring in 3 days
    soon = int(_time.time()) + 3 * 86400
    local_store_db.save_relationship({
        "certificate_id": "cert-expiring",
        "issuer": "aaa", "subject": "bbb",
        "capabilities": [],
        "expires_at": soon,
    }, peer_did="did:key:expiring-peer")

    broadcast_calls = []
    async def mock_broadcast(msg):
        broadcast_calls.append(msg)
    gateway.broadcast = mock_broadcast

    with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
         patch("proxion_messenger_core.handshake.receive_certificates", return_value=[]):
        await gateway._poll_handshake_completions()

    types = [c["type"] for c in broadcast_calls]
    assert "cert_expiring_soon" in types
    warn = next(c for c in broadcast_calls if c["type"] == "cert_expiring_soon")
    assert warn["certificate_id"] == "cert-expiring"


@pytest.mark.asyncio
async def test_poll_does_not_warn_valid_cert(gateway, local_store_db):
    """Certs expiring in more than 7 days should NOT trigger cert_expiring_soon."""
    import time as _time
    far = int(_time.time()) + 30 * 86400
    local_store_db.save_relationship({
        "certificate_id": "cert-valid",
        "issuer": "aaa", "subject": "bbb",
        "capabilities": [],
        "expires_at": far,
    }, peer_did="did:key:valid-peer")

    broadcast_calls = []
    async def mock_broadcast(msg):
        broadcast_calls.append(msg)
    gateway.broadcast = mock_broadcast

    with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
         patch("proxion_messenger_core.handshake.receive_certificates", return_value=[]):
        await gateway._poll_handshake_completions()

    types = [c["type"] for c in broadcast_calls]
    assert "cert_expiring_soon" not in types
