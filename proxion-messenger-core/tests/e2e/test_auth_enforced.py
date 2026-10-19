"""The E2E gateway runs with auth forced on. These pin that it really is, and
cover handler branches that only run when auth is enforced."""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.persist import AgentState
from .helpers import answer_auth_challenge, connect_and_register


async def _recv(ws, timeout=5.0):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _until(ws, *types, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        ev = await _recv(ws, max(0.05, deadline - asyncio.get_event_loop().time()))
        if ev.get("type") in types:
            return ev


@pytest.mark.asyncio
async def test_register_requires_challenge_and_commands_wait_for_it(live_gateway):
    agent = AgentState.generate()
    did = pub_key_to_did(agent.identity_pub_bytes)
    ws = await websockets.connect(live_gateway["url"])
    try:
        await _recv(ws)  # config
        await ws.send(json.dumps({"cmd": "register", "did": did, "display_name": "Eve"}))
        challenge = await _until(ws, "auth_challenge", "registered")
        assert challenge["type"] == "auth_challenge"

        # Nothing runs for a session that has not answered the challenge.
        await ws.send(json.dumps({"cmd": "chat_room_create", "name": "early"}))
        refused = await _until(ws, "error", "room_created")
        assert refused == {"type": "error", "message": "Not registered"}

        assert await answer_auth_challenge(ws, challenge, agent.identity_key)
        registered = await _until(ws, "registered", "auth_failed")
        assert registered["type"] == "registered"
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_challenge_signed_by_another_key_is_refused(live_gateway):
    claimed = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    ws = await websockets.connect(live_gateway["url"])
    try:
        await _recv(ws)
        await ws.send(json.dumps({"cmd": "register", "did": claimed, "display_name": "Mallory"}))
        challenge = await _until(ws, "auth_challenge", "registered")
        assert challenge["type"] == "auth_challenge"
        await answer_auth_challenge(ws, challenge, Ed25519PrivateKey.generate())
        result = await _until(ws, "auth_failed", "registered")
        assert result == {"type": "auth_failed", "reason": "invalid_signature"}
        assert claimed not in live_gateway["gateway"]._client_webids.values()
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_reaction_into_a_dm_the_caller_is_not_in_is_refused(live_gateway, alice_session, bob_session):
    carol = await connect_and_register(live_gateway["url"], "Carol", AgentState.generate())
    try:
        await alice_session.send(cmd="local_dm", target_webid=bob_session.did, content="just us")
        msg = await alice_session.recv_type("message")
        await bob_session.recv_type("message")

        # Carol has no DM with Alice, so naming Alice's did as the thread is refused.
        await carol.send(cmd="add_reaction", cert_id=alice_session.did,
                         message_id=msg["message_id"], emoji="x")
        err = await carol.recv_type("error")
        assert err["message"] == "Not a participant in this DM"

        # Bob is in the DM, so his reaction goes through and reaches Alice.
        await bob_session.send(cmd="add_reaction", cert_id=alice_session.did,
                               message_id=msg["message_id"], emoji="y")
        got = await alice_session.recv_type("reaction_added")
        assert (got["emoji"], got["from_webid"]) == ("y", bob_session.did)
        assert all(e.get("emoji") != "x" for e in alice_session._buf)
    finally:
        await carol.ws.close()


@pytest.mark.asyncio
async def test_history_of_another_accounts_cert_dm_is_empty(live_gateway, alice_session):
    gw = live_gateway["gateway"]
    cert_id = "cert-" + uuid.uuid4().hex
    peer = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    gw._store.save_relationship(
        {"certificate_id": cert_id, "subject": "cd" * 32, "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=peer, owner_webid=alice_session.did,
    )
    gw._store.save_message(str(uuid.uuid4()), cert_id, "dm", peer, "Peer", "private words",
                           datetime.now(timezone.utc).isoformat())

    await alice_session.send(cmd="get_local_history", thread_id=cert_id)
    mine = await alice_session.recv_type("local_history")
    assert [m["content"] for m in mine["messages"]] == ["private words"]

    carol = await connect_and_register(live_gateway["url"], "Carol", AgentState.generate())
    try:
        await carol.send(cmd="get_local_history", thread_id=cert_id)
        theirs = await carol.recv_type("local_history")
        assert theirs["messages"] == []
    finally:
        await carol.ws.close()
