"""Round 3: Audit chain integrity — hash chaining and tamper detection."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "audit.db"))


def test_audit_chain_hashes_link_correctly(store):
    """Sequential audit entries form a valid hash chain."""
    store.save_audit_log_chained("login", "info", webid="did:key:alice", ip="1.2.3.4")
    store.save_audit_log_chained("logout", "info", webid="did:key:alice", ip="1.2.3.4")
    store.save_audit_log_chained("rate_limited", "warning", ip="1.2.3.4")
    result = store.verify_audit_chain()
    assert result["ok"], f"Chain should be valid: {result}"
    assert result["break_at"] is None


def test_verify_audit_chain_detects_tamper(store, tmp_path):
    """Directly mutating entry_hash causes verify_audit_chain to return ok=False."""
    import sqlite3
    store.save_audit_log_chained("event_a", "info")
    store.save_audit_log_chained("event_b", "info")
    # Tamper: zero out the first entry's entry_hash
    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE audit_logs SET entry_hash = 'tampered' WHERE entry_hash != ''")
    conn.commit()
    conn.close()
    result = store.verify_audit_chain()
    assert not result["ok"], "Should detect tamper"
    assert result["break_at"] is not None


def test_get_audit_logs_can_return_chain_status(tmp_path):
    """get_audit_logs with verify_chain=True includes chain_ok in response."""
    import json
    import asyncio
    from unittest.mock import MagicMock, AsyncMock
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    from proxion_messenger_core.didkey import pub_key_to_did

    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9900, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    owner_did = pub_key_to_did(agent.identity_pub_bytes)
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = owner_did

    gw._store.save_audit_log_chained("test_event", "info")

    asyncio.get_event_loop().run_until_complete(
        gw._handle_get_audit_logs(ws, {"verify_chain": True})
    )
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "audit_logs"
    assert "chain_ok" in resp
    assert resp["chain_ok"] is True
