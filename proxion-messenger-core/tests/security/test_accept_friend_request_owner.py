"""F4: only the gateway owner's authenticated session may accept a friend
request. Acceptance issues the owner's RelationshipCertificate, so a non-owner
registered session holding a known invitation_id must not be able to force the
owner to friend a peer. The check is gated on _auth_enforced: the loopback
single-user path (auth not enforced) registers under a session DID and is
unenforceable anyway, so it keeps the existing single-user behaviour.
"""
import json

import pytest
from unittest.mock import AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "afr.db")),
        read_state=ReadState(),
    )


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


def _last(ws):
    return json.loads(ws.send.call_args[0][0])


@pytest.mark.asyncio
async def test_non_owner_session_refused_when_auth_enforced(gateway):
    gateway._force_auth = True
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zNotTheOwner"
    await gateway._handle_accept_friend_request(ws, {"invitation_id": "inv-1"})
    msg = _last(ws)
    assert msg["type"] == "error"
    assert msg["message"] == "not_owner"


@pytest.mark.asyncio
async def test_owner_session_passes_owner_check_when_auth_enforced(gateway):
    gateway._force_auth = True
    owner_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
    ws = _ws()
    gateway._client_webids[ws] = owner_did
    await gateway._handle_accept_friend_request(ws, {"invitation_id": "inv-missing"})
    msg = _last(ws)
    # Owner check passed → we reach the invite lookup (no invite seeded).
    assert msg["message"] != "not_owner"
    assert msg["message"] == "invite_not_found"


@pytest.mark.asyncio
async def test_local_single_user_not_blocked_when_auth_not_enforced(gateway):
    # Loopback host + no forced auth → _auth_enforced() is False → the owner check
    # is skipped and the legitimate local user (session DID != owner DID) still
    # reaches the accept flow.
    gateway._force_auth = False
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zLocalSessionDid"
    await gateway._handle_accept_friend_request(ws, {"invitation_id": "inv-missing"})
    msg = _last(ws)
    assert msg["message"] != "not_owner"
    assert msg["message"] == "invite_not_found"
