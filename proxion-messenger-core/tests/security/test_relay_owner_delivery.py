"""F9: relationship-gated relays (file_relay, voice_signal) must deliver to the
relationship OWNER derived from from_webid, never the wire's to_webid.

On a multi-account gateway a contact of user X must not be able to push spoofed
file offers/chunks or call signals at user Y by naming Y in to_webid. The
delivery target is derived from get_relationship_owner(from_webid), matching the
DM handlers. The co-channel path of voice_signal is bounded by channel membership
and keeps the wire target.
"""
import json

import pytest
from unittest.mock import AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rod.db")),
        read_state=ReadState(),
    )


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


def _register(gw, ws, webid):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._webid_sockets[webid] = {ws}


def _seed_rel(gw, peer_did, owner):
    gw._store.save_relationship(
        {"certificate_id": "c-" + peer_did[-4:], "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=peer_did, owner_webid=owner)


@pytest.mark.asyncio
async def test_voice_signal_delivered_to_relationship_owner_not_wire_target(gateway):
    owner = "did:key:zUserY"
    spoof_target = "did:key:zUserZ"
    contact = "did:key:zBob"
    _seed_rel(gateway, contact, owner=owner)
    ws_owner = _ws(); _register(gateway, ws_owner, owner)
    ws_spoof = _ws(); _register(gateway, ws_spoof, spoof_target)

    status, _ = await gateway._handle_voice_signal_relay({
        "to_webid": spoof_target,          # attacker names an unrelated local user
        "from_webid": contact,
        "signal_type": "offer",
        "session_id": "s1",
        "signal_data": {"sdp": "v=0"},
    })

    assert status == "200 OK"
    ws_owner.send.assert_called_once()
    ws_spoof.send.assert_not_called()
    assert json.loads(ws_owner.send.call_args[0][0])["signal_type"] == "offer"


@pytest.mark.asyncio
async def test_file_relay_delivered_to_relationship_owner_not_wire_target(gateway):
    owner = "did:key:zUserY"
    spoof_target = "did:key:zUserZ"
    contact = "did:key:zBob"
    _seed_rel(gateway, contact, owner=owner)
    ws_owner = _ws(); _register(gateway, ws_owner, owner)
    ws_spoof = _ws(); _register(gateway, ws_spoof, spoof_target)

    status, _ = await gateway._handle_file_relay({
        "content_type": "file_offer",
        "to_webid": spoof_target,          # attacker names an unrelated local user
        "from_webid": contact,
        "file_id": "f1",
        "filename": "x.txt",
    })

    assert status.startswith("200")
    ws_owner.send.assert_called_once()
    ws_spoof.send.assert_not_called()
    assert json.loads(ws_owner.send.call_args[0][0])["type"] == "file_offer"
