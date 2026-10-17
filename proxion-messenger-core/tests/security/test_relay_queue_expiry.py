"""Round 3: Relay queue expiry and max-attempt enforcement."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_enqueue_sets_relay_expiry(store):
    """enqueue_relay sets expires_at to now + 86400."""
    before = time.time()
    store.enqueue_relay("relay-001", "did:key:bob", "wss://bob.example.com", {"msg": "hi"})
    after = time.time()
    rows = store._conn().execute("SELECT expires_at FROM pending_relays WHERE id='relay-001'").fetchall()
    assert rows, "Row should exist"
    expires_at = rows[0][0]
    assert before + 86400 - 5 <= expires_at <= after + 86400 + 5


def test_retry_loop_skips_expired_relays(store):
    """get_pending_relays excludes rows whose expires_at is in the past."""
    store.enqueue_relay("relay-expired", "did:key:bob", "wss://bob.example.com", {"msg": "old"})
    # Force expires_at to the past
    with store._conn() as conn:
        conn.execute(
            "UPDATE pending_relays SET expires_at = ? WHERE id = ?",
            (time.time() - 10, "relay-expired"),
        )
    pending = store.get_pending_relays()
    ids = [r["id"] for r in pending]
    assert "relay-expired" not in ids, "Expired relay should not be returned"


def test_relay_kept_until_expiry_regardless_of_attempts(store):
    """A relay is retried until expires_at, not dropped after a fixed number of
    attempts (10 attempts with the backoff cap gave up after about 3 hours)."""
    store.enqueue_relay("relay-many", "did:key:bob", "wss://bob.example.com", {"msg": "retry"})
    with store._conn() as conn:
        conn.execute(
            "UPDATE pending_relays SET attempt_count = 25 WHERE id = ?",
            ("relay-many",),
        )
    ids = [r["id"] for r in store.get_pending_relays()]
    assert "relay-many" in ids


@pytest.mark.asyncio
async def test_expired_relay_is_failed_and_the_sender_told(tmp_path):
    """A relay still queued past expires_at used to stay 'pending' forever (the
    retry query skips it), so the sender's message showed as queued for good."""
    import json as _json
    from unittest.mock import AsyncMock
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "x.db")))
    gw._store.enqueue_relay("relay-stale", "did:key:bob", "wss://bob.example.com",
                            {"from_webid": "did:key:alice", "to_webid": "did:key:bob"})
    with gw._store._conn() as conn:
        conn.execute("UPDATE pending_relays SET expires_at = ? WHERE id = ?",
                     (time.time() - 10, "relay-stale"))
    gw._send_to_identity = AsyncMock()
    await gw._expire_stale_relays()
    gw._send_to_identity.assert_awaited_once()
    who, raw = gw._send_to_identity.await_args.args
    assert who == "did:key:alice"
    assert _json.loads(raw) == {"type": "relay_expired", "message_id": "relay-stale"}
    await gw._expire_stale_relays()
    gw._send_to_identity.assert_awaited_once()  # told once, not every pass
