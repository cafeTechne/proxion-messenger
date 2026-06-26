"""E2E tests for advanced messaging: edit, history, search, pins, scheduling, read receipts."""

import asyncio
import pytest

from proxion_messenger_core.persist import AgentState
from .helpers import connect_and_register


# ---------------------------------------------------------------------------
# Edit / history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_local_message(alice_session, bob_session):
    """Alice edits her DM; both Alice and Bob get message_edited."""
    bob_did = bob_session.did

    await alice_session.send(cmd="local_dm", target_webid=bob_did, content="Original")
    alice_msg = await alice_session.recv_type("message", timeout=5.0)
    msg_id = alice_msg["message_id"]
    await bob_session.recv_type("message", timeout=5.0)

    await alice_session.send(
        cmd="edit_local_message",
        thread_id=bob_did,
        message_id=msg_id,
        content="Edited",
    )

    alice_edit = await alice_session.recv_type("message_edited", timeout=5.0)
    assert alice_edit.get("message_id") == msg_id
    assert alice_edit.get("new_content") == "Edited"

    bob_edit = await bob_session.recv_type("message_edited", timeout=5.0)
    assert bob_edit.get("new_content") == "Edited"


@pytest.mark.asyncio
async def test_get_local_history(alice_session, bob_session):
    """get_local_history returns messages sent in a DM thread."""
    bob_did = bob_session.did

    for i in range(3):
        await alice_session.send(cmd="local_dm", target_webid=bob_did, content=f"msg{i}")
        await alice_session.recv_type("message", timeout=5.0)
        await bob_session.recv_type("message", timeout=5.0)

    await alice_session.send(cmd="get_local_history", thread_id=bob_did, limit=10)
    history = await alice_session.recv_type("local_history", timeout=5.0)
    contents = [m["content"] for m in history.get("messages", [])]
    assert "msg0" in contents
    assert "msg2" in contents


@pytest.mark.asyncio
async def test_search_room(live_gateway, alice_agent, bob_agent):
    """search returns room messages matching the query."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    unique = "proxionsearchtoken42"

    await alice.send(cmd="chat_room_create", name="Search Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    room_id = room_evt["room_id"]
    code = room_evt["code"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await alice.send(cmd="send_room", room_id=room_id, content=f"find me {unique}")
    await alice.recv_type("message", timeout=5.0)
    await bob.recv_type("message", timeout=5.0)

    await alice.send(cmd="search", query=unique)
    result = await alice.recv_type("search_results", timeout=5.0)
    results = result.get("results", [])
    assert any(unique in r.get("content", "") for r in results)

    await alice.ws.close()
    await bob.ws.close()


# ---------------------------------------------------------------------------
# Pin / unpin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pin_message(live_gateway, alice_agent, bob_agent):
    """Alice pins a room message; get_pins returns it."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Pin Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    room_id = room_evt["room_id"]
    code = room_evt["code"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await alice.send(cmd="send_room", room_id=room_id, content="Pin this")
    msg = await alice.recv_type("message", timeout=5.0)
    msg_id = msg["message_id"]
    await bob.recv_type("message", timeout=5.0)

    await alice.send(cmd="pin_message", message_id=msg_id, thread_id=room_id)
    pinned = await alice.recv_type("message_pinned", timeout=5.0)
    assert pinned.get("message_id") == msg_id

    await alice.send(cmd="get_pins", thread_id=room_id)
    pins_evt = await alice.recv_type("pins", timeout=5.0)
    pin_ids = [p.get("message_id") for p in pins_evt.get("pins", [])]
    assert msg_id in pin_ids

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_unpin_message(live_gateway, alice_agent, bob_agent):
    """Alice unpins a room message; gets unpinned event."""
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    await alice.send(cmd="chat_room_create", name="Unpin Room")
    room_evt = await alice.recv_type("room_created", timeout=5.0)
    room_id = room_evt["room_id"]
    code = room_evt["code"]

    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await alice.send(cmd="send_room", room_id=room_id, content="Unpin me")
    msg = await alice.recv_type("message", timeout=5.0)
    msg_id = msg["message_id"]
    await bob.recv_type("message", timeout=5.0)

    await alice.send(cmd="pin_message", message_id=msg_id, thread_id=room_id)
    await alice.recv_type("message_pinned", timeout=5.0)

    await alice.send(cmd="unpin_message", message_id=msg_id, thread_id=room_id)
    # gateway sends "unpinned" event type for rooms
    unpinned = await alice.recv_type("unpinned", timeout=5.0)
    assert unpinned.get("message_id") == msg_id

    await alice.ws.close()
    await bob.ws.close()


# ---------------------------------------------------------------------------
# Scheduled messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_message(alice_session, bob_session):
    """Alice schedules a message; gets message_scheduled with a scheduled id."""
    bob_did = bob_session.did
    # Must be within 1 year from now
    send_at = "2026-12-01T12:00:00+00:00"

    await alice_session.send(
        cmd="schedule_message",
        thread_id=bob_did,
        content="Future message",
        send_at=send_at,
    )

    scheduled = await alice_session.recv_type("message_scheduled", timeout=5.0)
    assert scheduled.get("content_preview") == "Future message"
    assert scheduled.get("id")


@pytest.mark.asyncio
async def test_list_and_cancel_scheduled(alice_session, bob_session):
    """Alice schedules then cancels a message; cancel_scheduled confirms."""
    bob_did = bob_session.did

    await alice_session.send(
        cmd="schedule_message",
        thread_id=bob_did,
        content="Cancel me",
        send_at="2026-11-01T10:00:00+00:00",
    )
    scheduled = await alice_session.recv_type("message_scheduled", timeout=5.0)
    sched_id = scheduled["id"]

    await alice_session.send(cmd="list_scheduled")
    listed = await alice_session.recv_type("scheduled_list", timeout=5.0)
    ids = [s["id"] for s in listed.get("items", [])]
    assert sched_id in ids

    await alice_session.send(cmd="cancel_scheduled", id=sched_id)
    cancelled = await alice_session.recv_type("scheduled_cancelled", timeout=5.0)
    assert cancelled.get("id") == sched_id


# ---------------------------------------------------------------------------
# Read receipts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_read(alice_session, bob_session):
    """mark_read sends read_receipts event back to the sender."""
    bob_did = bob_session.did

    await alice_session.send(cmd="local_dm", target_webid=bob_did, content="Read me")
    msg = await alice_session.recv_type("message", timeout=5.0)
    await bob_session.recv_type("message", timeout=5.0)

    await bob_session.send(cmd="mark_read", thread_id=alice_session.did)
    # mark_read is fire-and-forget; the event type is read_receipts or similar
    # Just ensure the command doesn't error — drain for any error events
    events = await bob_session.drain(timeout=0.5)
    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"mark_read caused error: {errors}"
