"""Round 8: HMAC-hashed room invite codes and join-attempt rate limiting."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9977, db_path=str(tmp_path / "invite.db")),
        read_state=ReadState(),
    )


def _registered_ws(gw, webid="did:key:invite-user"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


# ---------------------------------------------------------------------------
# HMAC invite code generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_room_created_code_is_base32(gateway):
    """chat_room_create returns a base32 (lowercase) invite code."""
    ws = _registered_ws(gateway)
    await gateway._handle_chat_room_create(ws, {"name": "HMAC Room", "history_mode": "none"})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "room_created"
    code = resp["code"]
    # base32 uses alphabet a-z and 2-7 only
    assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in code.lower()), (
        f"Code {code!r} contains non-base32 characters"
    )


@pytest.mark.asyncio
async def test_room_code_hash_stored_not_plaintext(gateway):
    """The plaintext invite code is never stored in the DB rooms.code column."""
    ws = _registered_ws(gateway)
    await gateway._handle_chat_room_create(ws, {"name": "HMAC Room 2", "history_mode": "none"})
    resp = json.loads(ws.send.call_args[0][0])
    room_id = resp["room_id"]
    plaintext_code = resp["code"]

    rooms = gateway._store.get_all_rooms()
    matching = [r for r in rooms if r["room_id"] == room_id]
    assert matching, "Room not persisted"
    stored_code = matching[0]["code"]
    assert stored_code != plaintext_code, "Plaintext code must NOT be stored in DB"
    # The stored value should be a 64-char hex HMAC hash
    assert len(stored_code) == 64


@pytest.mark.asyncio
async def test_join_room_by_hash_lookup(gateway):
    """Joining with the plaintext code should resolve via HMAC hash to the correct room."""
    creator_ws = _registered_ws(gateway, webid="did:key:creator")
    await gateway._handle_chat_room_create(creator_ws, {"name": "Join Test", "history_mode": "none"})
    resp = json.loads(creator_ws.send.call_args[0][0])
    code = resp["code"]
    room_id = resp["room_id"]

    joiner_ws = _registered_ws(gateway, webid="did:key:joiner")
    await gateway._handle_join_room(joiner_ws, {"code": code})
    join_resp = json.loads(joiner_ws.send.call_args[0][0])
    assert join_resp["type"] == "room_joined", f"Got: {join_resp}"
    assert join_resp["room_id"] == room_id


@pytest.mark.asyncio
async def test_join_room_wrong_code_rejected(gateway):
    """An invalid invite code must return an error, not join any room."""
    ws = _registered_ws(gateway)
    await gateway._handle_join_room(ws, {"code": "completely-wrong-code"})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"


# ---------------------------------------------------------------------------
# Join-attempt rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_join_attempts_rate_limited(gateway):
    """After 10 failed join attempts from the same IP in 60s, further attempts are blocked."""
    ws = _registered_ws(gateway)
    gateway._session_meta[ws] = {"ip_addr": "1.2.3.4"}

    # Exhaust the rate limit with wrong codes
    for i in range(10):
        await gateway._handle_join_room(ws, {"code": f"wrongcode{i:04d}"})

    # 11th attempt should be rate-limited
    ws.send.reset_mock()
    await gateway._handle_join_room(ws, {"code": "anothercode"})
    resp = json.loads(ws.send.call_args[0][0])
    assert "Too many" in resp.get("message", "") or resp.get("type") == "error"


@pytest.mark.asyncio
async def test_join_attempts_recorded_in_store(gateway):
    """record_join_attempt persists to DB; count_recent_join_attempts reflects it."""
    code_hash = "a" * 64
    gateway._store.record_join_attempt(code_hash, "10.0.0.1")
    gateway._store.record_join_attempt(code_hash, "10.0.0.1")
    assert gateway._store.count_recent_join_attempts(code_hash, "10.0.0.1") == 2


@pytest.mark.asyncio
async def test_join_attempts_separate_per_ip(gateway):
    """Rate limiting is per-IP — different IPs have independent counters."""
    code_hash = "b" * 64
    for _ in range(10):
        gateway._store.record_join_attempt(code_hash, "1.1.1.1")
    # IP 2.2.2.2 should still have 0 attempts
    assert gateway._store.count_recent_join_attempts(code_hash, "2.2.2.2") == 0


# ---------------------------------------------------------------------------
# LocalStore invite methods
# ---------------------------------------------------------------------------

def test_create_and_consume_invite(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    store = LocalStore(str(tmp_path / "inv.db"))
    store.save_room("r1", "Room", "hash001", "", "none", "did:key:z1")
    store.create_room_invite("inv-1", "r1", "hash001", uses_left=2)
    assert store.consume_room_invite("hash001") == "r1"
    assert store.consume_room_invite("hash001") == "r1"
    assert store.consume_room_invite("hash001") is None  # exhausted


def test_consume_invite_wrong_hash_returns_none(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    store = LocalStore(str(tmp_path / "inv2.db"))
    assert store.consume_room_invite("nonexistent-hash") is None


def test_expired_invite_not_consumed(tmp_path):
    import time
    from proxion_messenger_core.local_store import LocalStore
    store = LocalStore(str(tmp_path / "inv3.db"))
    store.save_room("r2", "Room2", "hash002", "", "none", "did:key:z2")
    store.create_room_invite("inv-2", "r2", "hash002", uses_left=5, expires_at=time.time() - 1)
    assert store.consume_room_invite("hash002") is None
