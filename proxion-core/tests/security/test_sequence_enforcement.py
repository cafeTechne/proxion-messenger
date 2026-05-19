"""Round 4: Deterministic per-thread seq_num enforcement."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9860, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def _ws(gw, webid="did:key:alice"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _room(gw, ws):
    room_id = "room-seq-test"
    webid = gw._client_webids[ws]
    gw._local_rooms[room_id] = {
        "name": "Test", "code": "x" * 64, "members": {ws},
        "invite_url": "", "history_mode": "none", "messages": [],
        "creator_webid": webid,
    }
    return room_id


@pytest.mark.asyncio
async def test_accept_message_without_seq_num_for_compatibility(gw):
    """Messages without seq_num are accepted (backward compatible)."""
    ws = _ws(gw)
    room_id = _room(gw, ws)
    await gw._handle_send_voice_message(ws, {
        "thread_id": room_id,
        "audio_b64": __import__("base64").b64encode(b"\x00" * 100).decode(),
        "duration_ms": 1000,
        # No seq_num provided
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    errors = [c for c in calls if c.get("type") == "error" and "sequence" in c.get("message", "")]
    assert not errors, "Message without seq_num should be accepted"


@pytest.mark.asyncio
async def test_reject_non_incrementing_seq_num_in_room(gw):
    """seq_num that is not greater than the current max is rejected."""
    ws = _ws(gw)
    room_id = _room(gw, ws)
    # Insert a message with seq_num=5 into the store
    import uuid
    gw._store.save_message(
        message_id=str(uuid.uuid4()),
        thread_id=room_id,
        thread_type="room",
        from_webid="did:key:alice",
        from_display_name="Alice",
        content="first",
        timestamp=datetime.now(timezone.utc).isoformat(),
        seq_num=5,
    )
    # Now try to send with seq_num=5 (not incrementing)
    await gw._handle_send_room(ws, {
        "room_id": room_id,
        "content": "second",
        "seq_num": 5,
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    errors = [c for c in calls if c.get("message") == "invalid_sequence"]
    assert errors, f"Non-incrementing seq_num should be rejected: {calls}"


@pytest.mark.asyncio
async def test_reject_non_incrementing_seq_num_in_dm(gw):
    """seq_num that is not greater than current max is rejected in DM."""
    ws = _ws(gw)
    target = "did:key:bob"
    thread_id = f"dm-{gw._client_webids[ws]}-{target}"
    # Insert existing message with seq_num=3
    import uuid
    gw._store.save_message(
        message_id=str(uuid.uuid4()),
        thread_id=thread_id,
        thread_type="dm",
        from_webid="did:key:alice",
        from_display_name="Alice",
        content="existing",
        timestamp=datetime.now(timezone.utc).isoformat(),
        seq_num=3,
    )
    await gw._handle_local_dm(ws, {
        "target_webid": target,
        "content": "duplicate seq",
        "seq_num": 3,
        "thread_id": thread_id,
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    errors = [c for c in calls if c.get("message") == "invalid_sequence"]
    assert errors, f"Non-incrementing DM seq_num should be rejected: {calls}"
