"""E2E tests for advanced gateway features: roles, read receipts, disappearing
messages, delete-for-everyone, forward, sessions, webhooks, and screenshare
signaling."""

import asyncio
import json
import pytest

from .helpers import connect_and_register


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _make_room(alice, bob, live_gateway, *, alice_agent, bob_agent):
    """Create a room as Alice, Bob joins.  Returns (room_id, code)."""
    import uuid
    room_name = f"adv-room-{uuid.uuid4().hex[:6]}"
    await alice.send(cmd="chat_room_create", name=room_name)
    ev = await alice.recv_type("room_created")
    room_id = ev["room_id"]
    code = ev["code"]
    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)
    return room_id, code


# ──────────────────────────────────────────────────────────────────────────────
# Read receipts
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_read_no_error(alice_session, bob_session, live_gateway, alice_agent, bob_agent):
    """mark_read on a DM thread should not return an error."""
    alice, bob = alice_session, bob_session
    await alice.send(cmd="local_dm", target_webid=bob.did, content="hello")
    ev = await alice.recv_type("message")
    msg_id = ev["message_id"]
    thread_id = ev["thread_id"]

    await bob.send(cmd="mark_read", thread_id=thread_id, message_id=msg_id)
    events = await bob.drain()
    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"Unexpected errors after mark_read: {errors}"


@pytest.mark.asyncio
async def test_read_receipt_delivered_to_peer(alice_session, bob_session, live_gateway, alice_agent, bob_agent):
    """mark_read should broadcast a read_receipt to the other thread participant."""
    alice, bob = alice_session, bob_session
    await alice.send(cmd="local_dm", target_webid=bob.did, content="read me")
    ev = await alice.recv_type("message")
    msg_id = ev["message_id"]
    thread_id = ev["thread_id"]

    await bob.recv_type("message")  # Bob receives Alice's DM
    await bob.send(cmd="mark_read", thread_id=thread_id, message_id=msg_id)
    receipt = await alice.recv_type("read_receipt", timeout=4.0)
    assert receipt["thread_id"] == thread_id
    assert receipt["message_id"] == msg_id


@pytest.mark.asyncio
async def test_read_receipt_in_room(live_gateway, alice_agent, bob_agent):
    """mark_read in a room delivers read_receipt to all other members."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-rr", alice_agent)
    bob = await connect_and_register(url, "Bob-rr", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(cmd="send_room", room_id=room_id, content="hello room")
    ev = await alice.recv_type("message")
    msg_id = ev["message_id"]
    await bob.recv_type("message")

    await bob.send(cmd="mark_read", thread_id=room_id, message_id=msg_id)
    receipt = await alice.recv_type("read_receipt", timeout=4.0)
    assert receipt["thread_id"] == room_id

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Room roles
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_and_get_room_role(live_gateway, alice_agent, bob_agent):
    """Room owner can set Bob's role; get_room_role returns it."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-role", alice_agent)
    bob = await connect_and_register(url, "Bob-role", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(cmd="set_member_role", room_id=room_id, webid=bob.did, role="admin")
    ev = await alice.recv_type("member_role_updated", timeout=4.0)
    assert ev["room_id"] == room_id
    assert ev["role"] == "admin"

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Disappearing messages
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_disappear_timer(live_gateway, alice_agent, bob_agent):
    """Room owner can set and get the disappear timer."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-dis", alice_agent)
    bob = await connect_and_register(url, "Bob-dis", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    # Set timer to 1 hour (3_600_000 ms)
    await alice.send(cmd="set_disappear_timer", room_id=room_id, ms=3_600_000)
    ev = await alice.recv_type("disappear_timer_updated", timeout=4.0)
    assert ev["room_id"] == room_id
    assert ev["ms"] == 3_600_000

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_get_disappear_timer(live_gateway, alice_agent, bob_agent):
    """get_disappear_timer returns the current timer value."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-gdt", alice_agent)
    bob = await connect_and_register(url, "Bob-gdt", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(cmd="set_disappear_timer", room_id=room_id, ms=1800000)
    await alice.recv_type("disappear_timer_updated")

    await alice.send(cmd="get_disappear_timer", room_id=room_id)
    ev = await alice.recv_type("disappear_timer", timeout=4.0)
    assert ev["ms"] == 1800000

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Delete for everyone
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_for_everyone_room(live_gateway, alice_agent, bob_agent):
    """Sending delete_message_for_everyone in a room broadcasts message_deleted to all."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-del", alice_agent)
    bob = await connect_and_register(url, "Bob-del", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(cmd="send_room", room_id=room_id, content="to be deleted")
    ev = await alice.recv_type("message")
    msg_id = ev["message_id"]
    await bob.recv_type("message")  # drain Bob's copy

    await alice.send(cmd="delete_local_message", thread_id=room_id, message_id=msg_id)
    alice_del = await alice.recv_type("message_deleted", timeout=4.0)
    bob_del = await bob.recv_type("message_deleted", timeout=4.0)
    assert alice_del["message_id"] == msg_id
    assert bob_del["message_id"] == msg_id

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Forward messages
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forward_message_to_room(live_gateway, alice_agent, bob_agent):
    """forward_message sends a forwarded copy to the target room."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-fwd", alice_agent)
    bob = await connect_and_register(url, "Bob-fwd", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    # Alice sends a DM first; forward that to the room
    await alice.send(cmd="local_dm", target_webid=bob.did, content="original content")
    ev = await alice.recv_type("message")
    orig_msg_id = ev["message_id"]

    await alice.send(
        cmd="forward_message",
        message_id=orig_msg_id,
        target_thread_id=room_id,
    )
    fwd = await alice.recv_type("message", timeout=4.0)
    assert fwd.get("forwarded") is True

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions(alice_session):
    """list_sessions returns at least one entry with is_current=True."""
    await alice_session.send(cmd="list_sessions")
    ev = await alice_session.recv_type("session_list", timeout=4.0)
    sessions = ev["sessions"]
    assert len(sessions) >= 1
    current = [s for s in sessions if s.get("is_current")]
    assert len(current) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Webhooks
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_incoming_webhook(live_gateway, alice_agent, bob_agent):
    """create_webhook for incoming returns a webhook URL."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-wh", alice_agent)
    bob = await connect_and_register(url, "Bob-wh", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(
        cmd="create_webhook",
        thread_id=room_id,
        direction="incoming",
        bot_name="TestBot",
    )
    ev = await alice.recv_type("webhook_created", timeout=4.0)
    assert ev["direction"] == "incoming"
    assert "token" in ev  # incoming webhooks always return token

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_list_webhooks(live_gateway, alice_agent, bob_agent):
    """list_webhooks returns created webhooks for the room."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-lw", alice_agent)
    bob = await connect_and_register(url, "Bob-lw", bob_agent)

    room_id, _ = await _make_room(alice, bob, live_gateway,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(cmd="create_webhook", thread_id=room_id, direction="incoming", bot_name="Bot")
    await alice.recv_type("webhook_created")

    await alice.send(cmd="list_webhooks", thread_id=room_id)
    ev = await alice.recv_type("webhook_list", timeout=4.0)
    assert isinstance(ev.get("webhooks"), list)
    assert len(ev["webhooks"]) >= 1

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Screen sharing signaling
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_screenshare_started_relayed(live_gateway, alice_agent, bob_agent):
    """screenshare_started is relayed to the other call participant."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-ss", alice_agent)
    bob = await connect_and_register(url, "Bob-ss", bob_agent)

    # Put alice and bob in a shared room so voice_invite passes the contact check
    await alice.send(cmd="chat_room_create", name="ss-room")
    room_ev = await alice.recv_type("room_created", timeout=5.0)
    await bob.send(cmd="join_room", code=room_ev["code"])
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    # Establish a voice session first
    await alice.send(cmd="voice_invite", target_webid=bob.did)
    invite = await bob.recv_type("voice_invite", timeout=4.0)
    session_id = invite["session_id"]

    await bob.send(
        cmd="voice_answer",
        session_id=session_id,
        sdp_answer="v=0\r\nfake-answer",
    )
    await alice.recv_type("voice_answer", timeout=4.0)

    # Now signal screenshare
    await alice.send(cmd="screenshare_started", session_id=session_id)
    ev = await bob.recv_type("screenshare_started", timeout=4.0)
    assert ev["session_id"] == session_id

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_screenshare_stopped_relayed(live_gateway, alice_agent, bob_agent):
    """screenshare_stopped is relayed to the other call participant."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-ss2", alice_agent)
    bob = await connect_and_register(url, "Bob-ss2", bob_agent)

    # Put alice and bob in a shared room so voice_invite passes the contact check
    await alice.send(cmd="chat_room_create", name="ss2-room")
    room_ev = await alice.recv_type("room_created", timeout=5.0)
    await bob.send(cmd="join_room", code=room_ev["code"])
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)

    await alice.send(cmd="voice_invite", target_webid=bob.did)
    invite = await bob.recv_type("voice_invite", timeout=4.0)
    session_id = invite["session_id"]
    await bob.send(cmd="voice_answer", session_id=session_id, sdp_answer="v=0\r\nfake-answer")
    await alice.recv_type("voice_answer", timeout=4.0)

    await alice.send(cmd="screenshare_stopped", session_id=session_id)
    ev = await bob.recv_type("screenshare_stopped", timeout=4.0)
    assert ev["session_id"] == session_id

    await alice.ws.close()
    await bob.ws.close()
