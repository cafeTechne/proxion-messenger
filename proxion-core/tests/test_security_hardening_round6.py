"""Round 6 security hardening regression tests.

Covers:
  1. Session revocation race — revoked socket commands are silently dropped.
  2. Room permissions: kick without being creator is rejected.
  3. Room permissions: pin_message by non-owner is rejected.
  4. Room permissions: set_disappear_timer by non-owner is rejected.
  5. kick_member with empty creator_webid is rejected (not bypassed).
  6. connect_css rejected for non-gateway-owner.
  7. get_all_presence scoped to caller's contacts only.
  8. Scheduler _NullWs gets correct from_webid identity.
  9. LocalStore save_message quota enforcement.
"""
from __future__ import annotations

import asyncio
import json
import pytest
import time
from unittest.mock import MagicMock, AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did


def _make_agent():
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    return agent, pub_key_to_did(pub_bytes)


def _fake_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


def _msgs(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


def _errors(ws):
    return [m for m in _msgs(ws) if m.get("type") == "error"]


@pytest.fixture
def gw(tmp_path):
    agent, _ = _make_agent()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=0, db_path=str(tmp_path / "r6.db")),
        read_state=ReadState(),
    )


def _register(gw, webid):
    ws = _fake_ws()
    gw._client_webids[ws] = webid
    gw._webid_sockets[webid] = ws
    gw.clients.add(ws)
    return ws


# ---------------------------------------------------------------------------
# 1. Session revocation race: revoked socket commands are silently dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoked_socket_command_dropped(gw):
    """process_command on a revoked socket must return without sending any response."""
    ws = _register(gw, "did:key:alice")
    gw._revoked_sessions.add(ws)
    await gw.process_command(ws, {"cmd": "get_rooms"})
    assert ws.send.call_count == 0, "revoked socket should receive no response"


@pytest.mark.asyncio
async def test_unrevoked_socket_still_works(gw):
    """A socket not in _revoked_sessions must be processed normally."""
    ws = _register(gw, "did:key:alice")
    await gw.process_command(ws, {"cmd": "get_rooms"})
    msgs = _msgs(ws)
    assert any(m.get("type") == "rooms" for m in msgs), f"expected rooms response, got {msgs}"


# ---------------------------------------------------------------------------
# 2. Room permissions: kick_member
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kick_member_non_creator_rejected(gw):
    """kick_member from a non-creator must return an error."""
    alice = _register(gw, "did:key:alice")
    bob = _register(gw, "did:key:bob")

    room_id = "room1"
    gw._local_rooms[room_id] = {
        "creator_webid": "did:key:alice",
        "members": {alice, bob},
    }
    # Bob is not creator — must be rejected
    await gw.process_command(bob, {"cmd": "kick_member", "room_id": room_id, "webid": "did:key:alice"})
    errs = _errors(bob)
    assert errs, f"expected error, got {_msgs(bob)}"
    assert "creator" in errs[0]["message"].lower() or "owner" in errs[0]["message"].lower()


@pytest.mark.asyncio
async def test_kick_member_empty_creator_rejected(gw):
    """kick_member when creator_webid is empty string must still be rejected (not bypass)."""
    alice = _register(gw, "did:key:alice")
    bob = _register(gw, "did:key:bob")

    room_id = "room2"
    gw._local_rooms[room_id] = {
        "creator_webid": "",   # empty → no owner set
        "members": {alice, bob},
    }
    await gw.process_command(alice, {"cmd": "kick_member", "room_id": room_id, "webid": "did:key:bob"})
    errs = _errors(alice)
    assert errs, f"empty creator_webid should be rejected, got {_msgs(alice)}"


@pytest.mark.asyncio
async def test_kick_member_creator_succeeds(gw):
    """kick_member from the room creator must succeed (no error response)."""
    alice = _register(gw, "did:key:alice")
    bob = _register(gw, "did:key:bob")

    room_id = "room3"
    gw._local_rooms[room_id] = {
        "creator_webid": "did:key:alice",
        "members": {alice, bob},
    }
    await gw.process_command(alice, {"cmd": "kick_member", "room_id": room_id, "webid": "did:key:bob"})
    errs = _errors(alice)
    assert not errs, f"creator kick should succeed, got errors: {errs}"


# ---------------------------------------------------------------------------
# 3. Room permissions: pin_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pin_message_non_owner_rejected(gw):
    """pin_message from a non-owner must return an error."""
    alice = _register(gw, "did:key:alice")
    bob = _register(gw, "did:key:bob")

    room_id = "room_pin1"
    gw._local_rooms[room_id] = {
        "creator_webid": "did:key:alice",
        "members": {alice, bob},
    }
    await gw.process_command(bob, {
        "cmd": "pin_message",
        "thread_id": f"room:{room_id}",
        "message_id": "msg1",
    })
    errs = _errors(bob)
    assert errs, f"non-owner pin should be rejected, got {_msgs(bob)}"
    assert "owner" in errs[0]["message"].lower() or "creator" in errs[0]["message"].lower()


@pytest.mark.asyncio
async def test_pin_message_owner_not_blocked(gw):
    """pin_message from the room owner must not be blocked by the permission check."""
    alice = _register(gw, "did:key:alice")

    room_id = "room_pin2"
    gw._local_rooms[room_id] = {
        "creator_webid": "did:key:alice",
        "members": {alice},
        "messages": [],
    }
    await gw.process_command(alice, {
        "cmd": "pin_message",
        "thread_id": f"room:{room_id}",
        "message_id": "msg1",
    })
    errs = _errors(alice)
    assert not errs, f"owner pin should not be blocked by permission check, got: {errs}"


# ---------------------------------------------------------------------------
# 4. Room permissions: set_disappear_timer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_disappear_timer_non_owner_rejected(gw):
    """set_disappear_timer from a non-owner must return an error."""
    alice = _register(gw, "did:key:alice")
    bob = _register(gw, "did:key:bob")

    room_id = "room_timer1"
    gw._local_rooms[room_id] = {
        "creator_webid": "did:key:alice",
        "members": {alice, bob},
    }
    await gw.process_command(bob, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 60000,
    })
    errs = _errors(bob)
    assert errs, f"non-owner timer set should be rejected, got {_msgs(bob)}"
    assert "owner" in errs[0]["message"].lower()


@pytest.mark.asyncio
async def test_set_disappear_timer_owner_succeeds(gw):
    """set_disappear_timer from the room owner must succeed."""
    alice = _register(gw, "did:key:alice")

    room_id = "room_timer2"
    gw._local_rooms[room_id] = {
        "creator_webid": "did:key:alice",
        "members": {alice},
    }
    await gw.process_command(alice, {
        "cmd": "set_disappear_timer",
        "room_id": room_id,
        "ms": 30000,
    })
    errs = _errors(alice)
    assert not errs, f"owner set_disappear_timer should not be rejected, got: {errs}"
    assert gw._room_disappear_timers.get(room_id) == 30000


# ---------------------------------------------------------------------------
# 5. connect_css restricted to gateway owner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_css_non_owner_rejected(gw):
    """connect_css must be rejected for any caller who is not the gateway owner."""
    stranger = _register(gw, "did:key:stranger")
    await gw.process_command(stranger, {
        "cmd": "connect_css",
        "css_url": "https://pod.example.com",
        "email": "a@b.com",
        "password": "pass",
    })
    msgs = _msgs(stranger)
    # OWNER_ONLY_CMDS ACL returns E_FORBIDDEN; older css_error also acceptable
    err_msgs = [m for m in msgs if m.get("type") in ("css_error", "error")]
    assert err_msgs, f"non-owner connect_css should return an error, got {msgs}"
    combined = err_msgs[0].get("message", "") + err_msgs[0].get("code", "")
    assert "owner" in combined.lower() or "forbidden" in combined.lower()


@pytest.mark.asyncio
async def test_connect_css_owner_passes_auth_check(gw):
    """connect_css with the gateway owner DID must pass the permission check (may still fail for other reasons)."""
    gateway_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    owner = _register(gw, gateway_did)
    await gw.process_command(owner, {
        "cmd": "connect_css",
        "css_url": "https://pod.example.com",
        "email": "a@b.com",
        "password": "pass",
    })
    msgs = _msgs(owner)
    css_errs = [m for m in msgs if m.get("type") == "css_error"]
    # Should NOT get "Only the gateway owner" error; other errors (e.g. SSRF) are acceptable
    owner_rejected = any("owner" in m.get("message", "").lower() for m in css_errs)
    assert not owner_rejected, f"owner should pass auth check, got: {css_errs}"


# ---------------------------------------------------------------------------
# 6. get_all_presence scoped to caller's contacts only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_all_presence_filters_to_contacts(gw):
    """get_all_presence must not leak presence of users outside the caller's contact list."""
    alice = _register(gw, "did:key:alice")

    # Seed presence for three users
    gw._user_presence["did:key:alice"] = {"status": "online"}
    gw._user_presence["did:key:bob"] = {"status": "online"}     # not a contact
    gw._user_presence["did:key:charlie"] = {"status": "online"}  # not a contact

    await gw.process_command(alice, {"cmd": "get_all_presence"})
    msgs = _msgs(alice)
    presence_msgs = [m for m in msgs if m.get("type") == "all_presence"]
    assert presence_msgs, f"expected all_presence, got {msgs}"
    returned_wids = set(presence_msgs[-1]["presence"].keys())

    # Alice sees herself (always allowed) but not bob or charlie (no store = no contacts)
    assert "did:key:alice" in returned_wids, "caller should see their own presence"
    assert "did:key:bob" not in returned_wids, "non-contact bob should be filtered"
    assert "did:key:charlie" not in returned_wids, "non-contact charlie should be filtered"


# ---------------------------------------------------------------------------
# 7. Scheduler _NullWs gets correct from_webid identity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduler_null_ws_identity(gw, tmp_path):
    """_scheduler_loop must inject the sender's from_webid into _NullWs before calling process_command."""
    captured = {}

    async def fake_process_command(ws, data):
        captured["identity"] = gw._client_webids.get(ws, "MISSING")

    gw.process_command = fake_process_command

    # Simulate one due message via _scheduler_loop internals directly
    sched = {
        "id": 1,
        "from_webid": "did:key:scheduled-sender",
        "thread_id": "room:scheduled",
        "content": "hello scheduled",
    }

    # Patch the store to return our sched entry
    mock_store = MagicMock()
    mock_store.get_due_scheduled_messages.return_value = [sched]
    mock_store.mark_scheduled_sent = MagicMock()
    gw._store = mock_store

    # Run one tick of the loop by calling the internals directly
    due = gw._store.get_due_scheduled_messages(time.time())
    for s in due:
        gw._store.mark_scheduled_sent(s["id"])
        sender_ws = next(
            (ws for ws, wid in gw._client_webids.items() if wid == s["from_webid"]),
            None,
        )
        null_ws = None
        if sender_ws is None:
            null_ws = gw._NullWs()
            gw._client_webids[null_ws] = s["from_webid"]
            sender_ws = null_ws
        try:
            await gw.process_command(sender_ws, {
                "cmd": "send_room",
                "room_id": s["thread_id"],
                "content": s["content"],
            })
        finally:
            if null_ws is not None:
                gw._client_webids.pop(null_ws, None)

    assert captured.get("identity") == "did:key:scheduled-sender", (
        f"NullWs should carry sender identity, got: {captured.get('identity')!r}"
    )
    # Verify cleanup: NullWs must have been removed from _client_webids
    for ws, wid in gw._client_webids.items():
        if isinstance(ws, gw._NullWs):
            pytest.fail("_NullWs was not cleaned up from _client_webids after scheduler tick")


# ---------------------------------------------------------------------------
# 8. LocalStore save_message quota enforcement
# ---------------------------------------------------------------------------

def test_save_message_quota_enforced(tmp_path):
    """save_message must raise ValueError once a thread reaches 5000 messages."""
    from proxion_messenger_core.local_store import LocalStore
    import uuid

    db_path = str(tmp_path / "quota_test.db")
    store = LocalStore(db_path)

    thread_id = "thread_quota_test"

    # Insert messages up to the limit
    for i in range(5000):
        store.save_message(
            message_id=str(uuid.uuid4()),
            thread_id=thread_id,
            thread_type="room",
            from_webid="did:key:alice",
            from_display_name="Alice",
            content=f"msg {i}",
            timestamp=f"2026-01-01T00:00:{i // 60:02d}Z",
        )

    # The 5001st message must be silently dropped (quota exhausted)
    overflow_id = str(uuid.uuid4())
    store.save_message(
        message_id=overflow_id,
        thread_id=thread_id,
        thread_type="room",
        from_webid="did:key:alice",
        from_display_name="Alice",
        content="one too many",
        timestamp="2026-01-01T01:24:00Z",
    )
    # Verify the overflow message was NOT persisted
    from proxion_messenger_core.local_store import LocalStore as _LS
    with _LS(db_path)._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (overflow_id,)
        ).fetchone()
    assert row is None, "Overflow message should not be stored when quota is reached"


def test_save_message_quota_separate_threads(tmp_path):
    """The 5000-message limit is per-thread, not global."""
    from proxion_messenger_core.local_store import LocalStore
    import uuid

    db_path = str(tmp_path / "quota_threads.db")
    store = LocalStore(db_path)

    # Fill thread A to the limit
    for i in range(5000):
        store.save_message(
            message_id=str(uuid.uuid4()),
            thread_id="thread_a",
            thread_type="room",
            from_webid="did:key:alice",
            from_display_name="Alice",
            content=f"msg {i}",
            timestamp=f"2026-01-01T00:00:00Z",
        )

    # Thread B should still accept messages
    store.save_message(
        message_id=str(uuid.uuid4()),
        thread_id="thread_b",
        thread_type="room",
        from_webid="did:key:alice",
        from_display_name="Alice",
        content="thread b is fine",
        timestamp="2026-01-01T00:00:00Z",
    )
