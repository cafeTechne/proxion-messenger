"""get_dms lists DM threads from the store, so a conversation with a contact on
another gateway is still in the sidebar after a reload or a gateway restart.

It used to answer from the in-memory dm_clients map, which is empty unless a
pod-backed DM client is live; the web client then replaced its DM list with
that empty answer, so cross-gateway DMs vanished until the peer wrote again.
"""
import asyncio
import json
import time

import pytest

pytest.importorskip("websockets")
import websockets
from gwharness import free_port, start_gateway
from gwharness import register as _gw_register

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

CERT_ID = "cert-get-dms-01"


@pytest.fixture(autouse=True)
def _allow_private_relay(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")


def _gateway(agent, db_path):
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path)
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    return gw, start_gateway(gw, ws_port, http_port)


def _befriend(gw, me, peer, peer_http):
    gw._store.save_relationship(
        {"certificate_id": CERT_ID, "issuer": me.identity_pub_bytes.hex(),
         "subject": peer.identity_pub_bytes.hex(), "signature": "dummy"},
        peer_did=pub_key_to_did(peer.identity_pub_bytes),
    )
    gw._record_peer_gateway(pub_key_to_did(peer.identity_pub_bytes), peer_http)


async def _wait_for(conn, pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = json.loads(await asyncio.wait_for(conn.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            continue
        if pred(msg):
            return msg
    return None


async def _get_dms(ws_port, did, name):
    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as conn:
        await _gw_register(conn, did, display_name=name)
        await conn.send(json.dumps({"cmd": "get_dms"}))
        msg = await _wait_for(conn, lambda m: m.get("type") == "dms")
    assert msg is not None, "no dms reply"
    return {d["cert_id"]: d for d in msg["dms"]}


@pytest.mark.asyncio
async def test_cross_gateway_dm_survives_reconnect_and_restart(tmp_path):
    agent_a, agent_b = AgentState.generate(), AgentState.generate()
    did_a, did_b = (pub_key_to_did(x.identity_pub_bytes) for x in (agent_a, agent_b))
    gw_a, h_a = _gateway(agent_a, str(tmp_path / "a.db"))
    gw_b, h_b = _gateway(agent_b, str(tmp_path / "b.db"))
    _befriend(gw_a, agent_a, agent_b, f"http://127.0.0.1:{h_b.http_port}")
    _befriend(gw_b, agent_b, agent_a, f"http://127.0.0.1:{h_a.http_port}")
    # What the friend handshake records for the new contact.
    gw_b._save_peer_name_if_unknown(did_a, "Alice")

    # Before any message there is no conversation to list.
    assert CERT_ID not in await _get_dms(h_b.ws_port, did_b, "Bob")

    async with websockets.connect(f"ws://127.0.0.1:{h_a.ws_port}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{h_b.ws_port}") as conn_b:
        await _gw_register(conn_a, did_a, display_name="Alice")
        await _gw_register(conn_b, did_b, display_name="Bob")
        await conn_a.send(json.dumps({"cmd": "send_dm", "cert_id": CERT_ID,
                                      "content": "hello across gateways"}))
        got = await _wait_for(conn_b, lambda m: m.get("type") == "message"
                              and m.get("content") == "hello across gateways")
        assert got is not None, "relay DM never reached B"

    # Reconnect (a reload): the peer on gateway A is not connected to B.
    dms = await _get_dms(h_b.ws_port, did_b, "Bob")
    assert dms[CERT_ID]["peer_webid"] == did_a
    assert dms[CERT_ID]["display_name"] == "Alice"
    assert dms[CERT_ID]["local"] is False
    # The sender's side lists it too.
    assert CERT_ID in await _get_dms(h_a.ws_port, did_a, "Alice")

    # Restart gateway B on the same database with A stopped.
    h_a.stop()
    h_b.stop()
    _gw_b2, h_b2 = _gateway(agent_b, str(tmp_path / "b.db"))
    dms = await _get_dms(h_b2.ws_port, did_b, "Bob")
    assert dms[CERT_ID]["peer_webid"] == did_a
    assert dms[CERT_ID]["display_name"] == "Alice"


@pytest.mark.asyncio
async def test_get_dms_lists_own_local_threads_only(tmp_path):
    from unittest.mock import AsyncMock
    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "r.db")))
    me, other, peer = "did:key:zMe", "did:key:zOther", "did:key:zPeer"
    gw._store.save_dm_thread("local-peer", peer, "Peer", owner_webid=me)
    gw._store.save_dm_thread("local-peer-other", peer, "Peer", owner_webid=other)
    ws = AsyncMock()
    gw._client_webids[ws] = me
    await gw._handle_get_dms(ws, {})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "dms"
    assert [(d["cert_id"], d["peer_webid"], d["local"]) for d in sent["dms"]] == [
        ("local-peer", peer, True)]
