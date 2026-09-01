"""DM/room read IDOR guards: read_dm, get_local_history, get_pins, get_disappear_timer.

Several read-only handlers returned a whole thread (messages, pins, timer) to any
registered socket without checking the caller participates. get_messages(thread_id)
is `WHERE thread_id=?` only, so a non-party could read another account's DM just by
naming its thread id. These assert the participant/membership gate holds when auth is
enforced, and that the loopback (non-enforced) path still returns data.
"""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


PARTICIPANT = "did:key:zParticipant"
PEER = "did:key:zPeer"
STRANGER = "did:key:zStranger"
DM_THREAD = "dm-thread-secret"


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "idor.db"))


def _make_gateway(store, host, port):
    gw = ProxionGateway(
        agent=AgentState.generate(), dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port, host=host, db_path=None), read_state=ReadState(),
    )
    gw._store = store
    return gw


@pytest.fixture
def gateway(store, monkeypatch):
    # host 0.0.0.0 is routable → _auth_enforced() is True (the guarded path).
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    return _make_gateway(store, "0.0.0.0", 9961)


@pytest.fixture
def loopback_gateway(store, monkeypatch):
    # genuine loopback → _auth_enforced() is False (dev; guard must not apply).
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    return _make_gateway(store, "127.0.0.1", 9962)


def _ws(gw, webid):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _seed_dm(store):
    """A DM thread owned by PARTICIPANT (peer PEER), with one message + one pin."""
    store.save_dm_thread(DM_THREAD, peer_webid=PEER, owner_webid=PARTICIPANT)
    store.save_message(
        "m-1", DM_THREAD, "dm", PEER, None, "secret text", "2030-01-01T00:00:00Z"
    )
    store.save_pin(DM_THREAD, "m-1", PARTICIPANT, content="secret text")


def _last(ws):
    return json.loads(ws.send.call_args[0][0])


# ── read_dm ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_dm_participant_gets_messages(gateway, store):
    _seed_dm(store)
    ws = _ws(gateway, PARTICIPANT)
    await gateway._handle_read_dm(ws, {"cert_id": DM_THREAD})
    sent = _last(ws)
    assert sent["type"] == "history"
    assert [m["message_id"] for m in sent["messages"]] == ["m-1"]


@pytest.mark.asyncio
async def test_read_dm_stranger_gets_empty(gateway, store):
    _seed_dm(store)
    ws = _ws(gateway, STRANGER)
    await gateway._handle_read_dm(ws, {"cert_id": DM_THREAD})
    sent = _last(ws)
    # Same empty-history shape (not a distinct error) — no existence leak.
    assert sent == {"type": "history", "thread_id": DM_THREAD, "messages": []}


@pytest.mark.asyncio
async def test_read_dm_loopback_stranger_still_gets_data(loopback_gateway, store):
    _seed_dm(store)
    ws = _ws(loopback_gateway, STRANGER)
    await loopback_gateway._handle_read_dm(ws, {"cert_id": DM_THREAD})
    sent = _last(ws)
    assert [m["message_id"] for m in sent["messages"]] == ["m-1"]


# ── get_local_history ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_history_participant_gets_messages(gateway, store):
    _seed_dm(store)
    ws = _ws(gateway, PARTICIPANT)
    await gateway._handle_get_local_history(ws, {"thread_id": DM_THREAD})
    sent = _last(ws)
    assert sent["type"] == "local_history"
    assert [m["message_id"] for m in sent["messages"]] == ["m-1"]


@pytest.mark.asyncio
async def test_local_history_stranger_gets_empty(gateway, store):
    _seed_dm(store)
    ws = _ws(gateway, STRANGER)
    await gateway._handle_get_local_history(ws, {"thread_id": DM_THREAD})
    sent = _last(ws)
    assert sent == {"type": "local_history", "thread_id": DM_THREAD, "messages": []}


@pytest.mark.asyncio
async def test_local_history_loopback_stranger_still_gets_data(loopback_gateway, store):
    _seed_dm(store)
    ws = _ws(loopback_gateway, STRANGER)
    await loopback_gateway._handle_get_local_history(ws, {"thread_id": DM_THREAD})
    sent = _last(ws)
    assert [m["message_id"] for m in sent["messages"]] == ["m-1"]


# ── get_pins ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_pins_participant_gets_pins(gateway, store):
    _seed_dm(store)
    ws = _ws(gateway, PARTICIPANT)
    await gateway._handle_get_pins(ws, {"thread_id": DM_THREAD})
    sent = _last(ws)
    assert sent["type"] == "pins"
    assert [p["message_id"] for p in sent["pins"]] == ["m-1"]


@pytest.mark.asyncio
async def test_get_pins_stranger_gets_empty(gateway, store):
    _seed_dm(store)
    ws = _ws(gateway, STRANGER)
    await gateway._handle_get_pins(ws, {"thread_id": DM_THREAD})
    sent = _last(ws)
    assert sent == {"type": "pins", "thread_id": DM_THREAD, "pins": []}


@pytest.mark.asyncio
async def test_get_pins_loopback_stranger_still_gets_pins(loopback_gateway, store):
    _seed_dm(store)
    ws = _ws(loopback_gateway, STRANGER)
    await loopback_gateway._handle_get_pins(ws, {"thread_id": DM_THREAD})
    sent = _last(ws)
    assert [p["message_id"] for p in sent["pins"]] == ["m-1"]


# ── get_disappear_timer ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disappear_timer_stranger_gets_default(gateway, store):
    _seed_dm(store)
    gateway._dm_disappear_timers[DM_THREAD] = 60_000
    ws = _ws(gateway, STRANGER)
    await gateway._handle_get_disappear_timer(ws, {"room_id": DM_THREAD})
    sent = _last(ws)
    assert sent == {"type": "disappear_timer", "room_id": DM_THREAD, "ms": 0}


@pytest.mark.asyncio
async def test_disappear_timer_participant_gets_value(gateway, store):
    _seed_dm(store)
    gateway._dm_disappear_timers[DM_THREAD] = 60_000
    ws = _ws(gateway, PARTICIPANT)
    await gateway._handle_get_disappear_timer(ws, {"room_id": DM_THREAD})
    sent = _last(ws)
    assert sent["ms"] == 60_000


# ── room membership fold-in (pod-backed room absent from _local_rooms) ────────

@pytest.mark.asyncio
async def test_local_history_room_member_via_store(gateway, store):
    """A room member recorded only in the store (not _local_rooms) is authorized."""
    room_id = "room-store-only"
    store.add_room_member(room_id, PARTICIPANT)
    store.save_message("rm-1", room_id, "room", PEER, None, "hi", "2030-01-01T00:00:00Z")
    ws = _ws(gateway, PARTICIPANT)
    await gateway._handle_get_local_history(ws, {"thread_id": room_id})
    sent = _last(ws)
    assert [m["message_id"] for m in sent["messages"]] == ["rm-1"]


@pytest.mark.asyncio
async def test_local_history_room_nonmember_gets_empty(gateway, store):
    room_id = "room-store-only"
    store.add_room_member(room_id, PARTICIPANT)
    store.save_message("rm-1", room_id, "room", PEER, None, "hi", "2030-01-01T00:00:00Z")
    ws = _ws(gateway, STRANGER)
    await gateway._handle_get_local_history(ws, {"thread_id": room_id})
    sent = _last(ws)
    assert sent == {"type": "local_history", "thread_id": room_id, "messages": []}
