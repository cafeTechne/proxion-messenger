"""E2E tests for WebRTC voice call signaling relay."""

import asyncio
import pytest

from proxion_messenger_core.persist import AgentState
from .helpers import connect_and_register


@pytest.fixture
async def voice_room(alice_session, bob_session):
    """Create a shared room so alice and bob pass the voice invite contact check."""
    await alice_session.send(cmd="chat_room_create", name="VoiceRoom")
    ev = await alice_session.recv_type("room_created", timeout=5.0)
    code = ev["code"]
    await bob_session.send(cmd="join_room", code=code)
    await bob_session.recv_type("room_joined", timeout=5.0)
    await alice_session.recv_type("room_member_joined", timeout=5.0)
    return ev["room_id"]


@pytest.mark.asyncio
async def test_voice_invite_delivered(alice_session, bob_session, voice_room):
    """Alice sends voice_invite to Bob; Bob receives voice_invite event."""
    import secrets
    session_id = secrets.token_hex(16)

    await alice_session.send(
        cmd="voice_invite",
        target_webid=bob_session.did,
        session_id=session_id,
        sdp_offer="v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n",
    )

    invite = await bob_session.recv_type("voice_invite", timeout=5.0)
    assert invite.get("session_id") == session_id
    assert invite.get("caller_webid") == alice_session.did
    assert "sdp_offer" in invite


@pytest.mark.asyncio
async def test_voice_answer_delivered(alice_session, bob_session, voice_room):
    """Bob answers Alice's call; Alice receives voice_answer."""
    import secrets
    session_id = secrets.token_hex(16)

    # Alice invites
    await alice_session.send(
        cmd="voice_invite",
        target_webid=bob_session.did,
        session_id=session_id,
        sdp_offer="v=0\r\n",
    )
    await bob_session.recv_type("voice_invite", timeout=5.0)

    # Bob answers
    await bob_session.send(
        cmd="voice_answer",
        session_id=session_id,
        sdp_answer="v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n",
    )

    answer = await alice_session.recv_type("voice_answer", timeout=5.0)
    assert answer.get("session_id") == session_id
    assert "sdp_answer" in answer


@pytest.mark.asyncio
async def test_ice_candidate_relay(alice_session, bob_session, voice_room):
    """ICE candidates are relayed between caller and callee."""
    import secrets
    session_id = secrets.token_hex(16)

    await alice_session.send(
        cmd="voice_invite",
        target_webid=bob_session.did,
        session_id=session_id,
        sdp_offer="v=0\r\n",
    )
    await bob_session.recv_type("voice_invite", timeout=5.0)

    await bob_session.send(
        cmd="voice_answer",
        session_id=session_id,
        sdp_answer="v=0\r\n",
    )
    await alice_session.recv_type("voice_answer", timeout=5.0)

    # Alice sends an ICE candidate
    await alice_session.send(
        cmd="ice_candidate",
        session_id=session_id,
        candidate="candidate:1 1 UDP 2130706431 192.168.1.1 50000 typ host",
        sdp_mid="0",
        sdp_mline_index=0,
    )

    ice = await bob_session.recv_type("ice_candidate", timeout=5.0)
    assert ice.get("session_id") == session_id
    assert "candidate" in ice


@pytest.mark.asyncio
async def test_voice_hangup(alice_session, bob_session, voice_room):
    """Hanging up sends voice_hangup to the other party."""
    import secrets
    session_id = secrets.token_hex(16)

    await alice_session.send(
        cmd="voice_invite",
        target_webid=bob_session.did,
        session_id=session_id,
        sdp_offer="v=0\r\n",
    )
    await bob_session.recv_type("voice_invite", timeout=5.0)

    await bob_session.send(
        cmd="voice_answer",
        session_id=session_id,
        sdp_answer="v=0\r\n",
    )
    await alice_session.recv_type("voice_answer", timeout=5.0)

    # Alice hangs up
    await alice_session.send(cmd="voice_hangup", session_id=session_id)

    hangup = await bob_session.recv_type("voice_hangup", timeout=5.0)
    assert hangup.get("session_id") == session_id
