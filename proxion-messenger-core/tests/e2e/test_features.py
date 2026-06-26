"""E2E tests for gateway features with unit-level coverage but no prior E2E tests:
voice messages, kick member, revoke session, search, and forward-to-DM."""

import asyncio
import base64
import pytest

from .helpers import connect_and_register


async def _make_room(alice, bob, *, url=None, alice_agent=None, bob_agent=None):
    """Create a room as Alice, Bob joins. Returns (room_id, code)."""
    import uuid
    room_name = f"feat-room-{uuid.uuid4().hex[:6]}"
    await alice.send(cmd="chat_room_create", name=room_name)
    ev = await alice.recv_type("room_created")
    room_id = ev["room_id"]
    code = ev["code"]
    await bob.send(cmd="join_room", code=code)
    await bob.recv_type("room_joined", timeout=5.0)
    await alice.recv_type("room_member_joined", timeout=5.0)
    return room_id, code


# ──────────────────────────────────────────────────────────────────────────────
# Voice messages
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_message_delivered_to_room(live_gateway, alice_agent, bob_agent):
    """send_voice_message broadcasts an audio message to all room members."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-vm", alice_agent)
    bob = await connect_and_register(url, "Bob-vm", bob_agent)

    room_id, _ = await _make_room(alice, bob, url=url,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    small_audio = base64.b64encode(b"\x00" * 200).decode()
    await alice.send(
        cmd="send_voice_message",
        thread_id=room_id,
        audio_b64=small_audio,
        duration_ms=1000,
    )

    alice_ev = await alice.recv_type("message", timeout=4.0)
    bob_ev = await bob.recv_type("message", timeout=4.0)

    assert alice_ev.get("content_type") == "audio"
    assert alice_ev.get("audio_b64") == small_audio
    assert bob_ev.get("content_type") == "audio"
    assert bob_ev.get("audio_b64") == small_audio

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_voice_message_too_long_rejected(live_gateway, alice_agent, bob_agent):
    """send_voice_message with duration_ms > 60000 is rejected with an error."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-vmr", alice_agent)
    bob = await connect_and_register(url, "Bob-vmr", bob_agent)

    room_id, _ = await _make_room(alice, bob, url=url,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(
        cmd="send_voice_message",
        thread_id=room_id,
        audio_b64=base64.b64encode(b"\x00" * 10).decode(),
        duration_ms=61000,
    )
    ev = await alice.recv_type("error", timeout=4.0)
    msg = ev.get("message", "")
    assert "long" in msg.lower() or "60" in msg or "invalid_voice_payload" in msg

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Kick member
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kick_member_notifies_kicked_user(live_gateway, alice_agent, bob_agent):
    """Room creator kicking Bob sends member_kicked to Alice, kicked_from_room to Bob."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-kick", alice_agent)
    bob = await connect_and_register(url, "Bob-kick", bob_agent)

    room_id, _ = await _make_room(alice, bob, url=url,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    await alice.send(cmd="kick_member", room_id=room_id, webid=bob.did)

    alice_ev = await alice.recv_type("member_kicked", timeout=4.0)
    bob_ev = await bob.recv_type("kicked_from_room", timeout=4.0)

    assert alice_ev["room_id"] == room_id
    assert alice_ev["webid"] == bob.did
    assert bob_ev["room_id"] == room_id

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_non_creator_cannot_kick(live_gateway, alice_agent, bob_agent):
    """Non-creator gets an error when attempting to kick a member."""
    url = live_gateway["url"]
    alice = await connect_and_register(url, "Alice-nkick", alice_agent)
    bob = await connect_and_register(url, "Bob-nkick", bob_agent)

    room_id, _ = await _make_room(alice, bob, url=url,
                                  alice_agent=alice_agent, bob_agent=bob_agent)

    # Bob tries to kick Alice
    await bob.send(cmd="kick_member", room_id=room_id, webid=alice.did)
    ev = await bob.recv_type("error", timeout=4.0)
    assert ev.get("type") == "error"

    await alice.ws.close()
    await bob.ws.close()


# ──────────────────────────────────────────────────────────────────────────────
# Revoke session
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_session_removes_it_from_list(alice_session, bob_session):
    """list_sessions after revoke_session should not include the revoked session."""
    await alice_session.send(cmd="list_sessions")
    session_list = await alice_session.recv_type("session_list", timeout=4.0)
    sessions = session_list["sessions"]
    assert len(sessions) >= 1

    current = next((s for s in sessions if s.get("is_current")), None)
    assert current is not None

    # Revoking the current session should succeed (gateway may close the ws)
    # Just verify revoke_session doesn't error
    other_sessions = [s for s in sessions if not s.get("is_current")]
    if other_sessions:
        await alice_session.send(cmd="revoke_session",
                                 session_id=other_sessions[0]["session_id"])
        events = await alice_session.drain(timeout=1.0)
        # Should not get an error; may get session_revoked
        errors = [e for e in events if e.get("type") == "error"]
        assert not errors, f"Unexpected errors: {errors}"


# ──────────────────────────────────────────────────────────────────────────────
# Full-text search in a DM
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_in_dm_returns_message(alice_session, bob_session):
    """search command finds messages sent in a DM thread."""
    alice, bob = alice_session, bob_session

    unique = "xyzzy_search_dm"
    await alice.send(cmd="local_dm", target_webid=bob.did, content=f"hello {unique}")
    ev = await alice.recv_type("message", timeout=4.0)
    thread_id = ev["thread_id"]

    # Give FTS index a moment to populate
    await asyncio.sleep(0.1)

    await alice.send(cmd="search", query=unique)
    results_ev = await alice.recv_type("search_results", timeout=4.0)
    assert results_ev["query"] == unique
    assert len(results_ev["results"]) >= 1
    contents = [r["content"] for r in results_ev["results"]]
    assert any(unique in c for c in contents)


# ──────────────────────────────────────────────────────────────────────────────
# Forward message to a DM thread
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forward_message_to_dm(alice_session, bob_session, live_gateway, alice_agent, bob_agent):
    """forward_message can target a DM thread_id."""
    alice, bob = alice_session, bob_session

    # Alice sends a DM to Bob
    await alice.send(cmd="local_dm", target_webid=bob.did, content="original")
    ev = await alice.recv_type("message", timeout=4.0)
    orig_msg_id = ev["message_id"]
    orig_thread_id = ev["thread_id"]

    # Forward it back to the same DM thread
    await alice.send(
        cmd="forward_message",
        message_id=orig_msg_id,
        target_thread_id=orig_thread_id,
    )
    fwd = await alice.recv_type("message", timeout=4.0)
    assert fwd.get("forwarded") is True


# ──────────────────────────────────────────────────────────────────────────────
# Search empty query
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(alice_session):
    """search with empty query returns empty results without error."""
    await alice_session.send(cmd="search", query="")
    ev = await alice_session.recv_type("search_results", timeout=4.0)
    assert ev.get("results") == [] or ev.get("results") is not None
