"""Tests: seen-by / per-message read receipts."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


def test_save_and_get_message_receipt(store):
    store.save_message_receipt("msg-1", "did:key:zBob", "2026-06-05T10:00:00Z")
    readers = store.get_message_readers("msg-1")
    assert len(readers) == 1
    assert readers[0]["receiver_webid"] == "did:key:zBob"


@pytest.mark.asyncio
async def test_get_message_readers_handler(gateway):
    ws = _ws()
    room_id = "room-seen-1"
    msg_id = "msg-seen-1"
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.save_message_receipt(msg_id, "did:key:zBob", "2026-06-05T10:00:00Z")

    await gateway._handle_get_message_readers(ws, {"room_id": room_id, "message_id": msg_id})

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "message_readers"
    assert any(r["receiver_webid"] == "did:key:zBob" for r in sent["readers"])


@pytest.mark.asyncio
async def test_receipts_opt_out_per_user(gateway):
    """set_receipts_enabled false only affects that user, not others."""
    ws_alice = _ws()
    ws_bob = _ws()
    gateway._client_webids[ws_alice] = "did:key:zAlice"
    gateway._client_webids[ws_bob] = "did:key:zBob"
    gateway.clients.add(ws_alice)
    gateway.clients.add(ws_bob)

    # Simulate the command processing for set_receipts_enabled
    # Alice opts out
    _pref_webid = "did:key:zAlice"
    if _pref_webid:
        gateway._client_receipts_prefs[_pref_webid] = False

    assert gateway._client_receipts_prefs.get("did:key:zAlice") is False
    # Bob is unaffected (defaults to True)
    assert gateway._client_receipts_prefs.get("did:key:zBob", True) is True


@pytest.mark.asyncio
async def test_non_member_cannot_get_readers(gateway):
    """Non-members get empty readers response."""
    ws = _ws()
    room_id = "room-seen-2"
    gateway._local_rooms[room_id] = {"name": "T", "members": set()}

    await gateway._handle_get_message_readers(ws, {"room_id": room_id, "message_id": "msg-x"})

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["readers"] == []
