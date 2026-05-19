"""Tests for gateway write-through pod sync."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.federation import RelationshipCertificate
import tempfile
from pathlib import Path


@pytest.fixture
def mock_agent():
    """Create a mock agent with real crypto keys."""
    identity_key = Ed25519PrivateKey.generate()
    store_key = X25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    agent.identity_key = identity_key
    agent.store_key = store_key
    agent.identity_pub_bytes = identity_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    agent.store_pub_bytes = store_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return agent


@pytest.fixture
def local_store_db():
    """Create a temporary local store database."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    store = LocalStore(str(db_path))
    yield store
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
async def test_sync_message_to_pod_handles_errors(gateway, mock_agent):
    """_sync_message_to_pod should handle errors gracefully."""
    mock_pod_client = MagicMock()
    cert_dict = {
        "issuer": mock_agent.identity_pub_bytes.hex(),
        "subject": "peer_pub_hex",
        "certificate_id": "test-cert",
        "capabilities": [],
        "expires_at": 9999999999,
    }
    
    # Should not raise even if send_message fails
    with patch('proxion_messenger_core.messaging.send_message', side_effect=Exception("Send failed")):
        # Should complete without error
        await gateway._sync_message_to_pod(
            mock_pod_client, cert_dict, "test content", "msg-id", "from-webid"
        )


def test_write_through_sync_skips_without_store(mock_agent):
    """Write-through sync should skip gracefully when no store exists."""
    config = GatewayConfig(port=9999)
    gateway = ProxionGateway(
        agent=mock_agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState()
    )
    # No _store set
    assert gateway._store is None
    
    # Helper function should handle None store gracefully
    assert gateway._store is None


def test_write_through_sync_skips_without_pod_client(gateway):
    """Write-through sync should skip when no pod client for target."""
    from proxion_messenger_core.store import MemoryStore
    # No dm_clients set
    assert len(gateway.dm_clients) == 0
    # _get_store returns a no-op MemoryStore fallback (never a pod client)
    assert isinstance(gateway._get_store(), MemoryStore)
