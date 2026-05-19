"""Round 1: get_message DM authorization fix tests."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def gateway(store):
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9973, db_path=None), read_state=ReadState(),
    )
    gw._store = store
    return gw


def _ws(gw, webid):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


@pytest.mark.asyncio
async def test_sender_can_fetch_message(gateway, store):
    """Message sender can always retrieve their own message."""
    sender = "did:key:zSender"
    store.save_message(
        "msg-1", "thread-abc", "relay", sender, None, "hello", "2030-01-01T00:00:00Z"
    )
    ws = _ws(gateway, sender)
    await gateway._handle_get_message(ws, {"message_id": "msg-1"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("type") == "message_fetched"
    assert sent.get("message") is not None


@pytest.mark.asyncio
async def test_dm_participant_can_fetch_message(gateway, store):
    """A DM thread participant (non-sender) can retrieve the message."""
    sender = "did:key:zSender"
    participant = "did:key:zParticipant"
    thread_id = "thread-dm-1"

    store.save_message("msg-2", thread_id, "relay", sender, None, "hi", "2030-01-01T00:00:00Z")
    # Create a DM thread for the participant with this thread_id
    store.save_dm_thread(thread_id, peer_webid=sender, owner_webid=participant)

    ws = _ws(gateway, participant)
    await gateway._handle_get_message(ws, {"message_id": "msg-2"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("type") == "message_fetched"
    assert sent.get("message") is not None


@pytest.mark.asyncio
async def test_non_participant_cannot_fetch_message(gateway, store):
    """A user who is neither sender nor DM participant cannot retrieve the message."""
    sender = "did:key:zSender"
    thread_id = "thread-private-1"

    store.save_message("msg-3", thread_id, "relay", sender, None, "secret", "2030-01-01T00:00:00Z")

    stranger = "did:key:zStranger"
    ws = _ws(gateway, stranger)
    await gateway._handle_get_message(ws, {"message_id": "msg-3"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("type") == "error"
    assert "unauthorized" in sent.get("message", "").lower()
