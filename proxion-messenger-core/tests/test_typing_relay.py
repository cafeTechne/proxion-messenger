"""Tests: cross-gateway typing indicator relay."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    return gw


@pytest.mark.asyncio
async def test_typing_relay_delivers_to_local_socket(gateway):
    """_handle_typing_relay delivers typing event to local DM peer."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    local_webid = "did:key:zLocal"
    gateway.clients.add(ws)
    gateway._client_webids[ws] = local_webid
    gateway._webid_sockets[local_webid] = {ws}

    store = MagicMock()
    # Must mock get_all_dm_threads: get_dm_threads(no-owner) queries
    # WHERE owner_webid='' and is empty in production — mocking it hid the bug.
    store.get_all_dm_threads = MagicMock(return_value=[
        {"thread_id": "cert-abc", "peer_webid": local_webid, "owner_webid": "did:key:zRemote"}
    ])
    # A bare MagicMock returns truthy for is_blocked_by; make block state explicit so the
    # per-owner block check (R90 B) does not treat the sender as blocked.
    store.is_blocked_by = MagicMock(return_value=False)
    store.get_relationship_by_did = MagicMock(return_value={"certificate_id": "cert-abc"})
    store.get_relationship_owner = MagicMock(return_value=local_webid)
    gateway._store = store

    await gateway._handle_typing_relay({
        "from_webid": "did:key:zRemote",
        "cert_id": "cert-abc",
    })

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "typing"
    assert sent["from_webid"] == "did:key:zRemote"


@pytest.mark.asyncio
async def test_typing_relay_ignores_missing_from_webid(gateway):
    """_handle_typing_relay returns 400 if from_webid is missing."""
    status, _ = await gateway._handle_typing_relay({"cert_id": "cert-xyz"})
    assert status.startswith("400")


@pytest.mark.asyncio
async def test_set_presence_relays_to_known_peer_gateways(gateway):
    """_handle_set_presence triggers relay to all known peer gateways."""
    ws = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    gateway._client_webids[ws] = "did:key:zSelf"
    gateway.clients.add(ws)
    gateway._peer_gateway_urls["did:key:zPeer"] = "https://peer.example.com"

    with patch.object(gateway, "_relay_ephemeral", new_callable=AsyncMock) as mock_relay:
        mock_relay.return_value = None
        with patch("asyncio.create_task") as mock_task:
            await gateway._handle_set_presence(ws, {"status": "online"})
            # create_task should be called for the relay (no exception is success)


@pytest.mark.asyncio
async def test_typing_relay_rejects_unknown_peer_spoof(tmp_path):
    """Typing from a webid we have no relationship with is ignored (anti-spoof)."""
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "t.db")))
    ws = AsyncMock(); ws.send = AsyncMock(); ws.__hash__ = lambda s: id(s)
    gw.clients.add(ws)
    gw._client_webids[ws] = "did:key:zLocal"
    gw._webid_sockets["did:key:zLocal"] = {ws}
    gw._store.save_dm_thread("cert-x", "did:key:zStranger", None, owner_webid="did:key:zLocal")
    # No relationship seeded for zStranger -> ignored.
    status, _ = await gw._handle_typing_relay({"from_webid": "did:key:zStranger", "cert_id": "cert-x"})
    assert status.startswith("200")
    ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_typing_reaches_a_friend_on_another_gateway(tmp_path, monkeypatch):
    """Two gateways, one friendship, each browser registered under its own session
    did. Typing in the friend's DM (keyed by the sender's cert_id, no dm_threads row
    yet) was dropped on send, and the relay named the session did, which the peer's
    relationship does not know. It must arrive under the receiver's own cert_id."""
    import asyncio
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from proxion_messenger_core.didkey import pub_key_to_did
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")

    def _gw(name):
        return ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                              config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / f"{name}.db")))

    def _ws():
        w = AsyncMock(); w.send = AsyncMock(); w.close = AsyncMock()
        w.__hash__ = lambda s: id(s); w.__eq__ = lambda s, o: s is o
        w.remote_address = ("127.0.0.1", 1)
        return w

    def _sess():
        return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())

    gw_a, gw_b = _gw("a"), _gw("b")
    a_gw_did, b_gw_did = gw_a._own_gateway_did(), gw_b._own_gateway_did()
    a_sess, b_sess = _sess(), _sess()
    gw_a._store.save_relationship({"certificate_id": "cert-A", "subject": gw_b.agent.identity_pub_bytes.hex(),
                                   "created_at": 0, "expires_at": 2**31 - 1}, peer_did=b_gw_did, owner_webid=a_sess)
    gw_b._store.save_relationship({"certificate_id": "cert-B", "subject": gw_a.agent.identity_pub_bytes.hex(),
                                   "created_at": 0, "expires_at": 2**31 - 1}, peer_did=a_gw_did, owner_webid=b_sess)
    gw_a._peer_gateway_urls[b_gw_did] = "http://gw-b.test"

    async def _fake_post(url, payload):
        status, _ = await gw_b._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("200")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _fake_post)

    ws_a, ws_b = _ws(), _ws()
    for gw, ws, did in ((gw_a, ws_a, a_sess), (gw_b, ws_b, b_sess)):
        gw.clients.add(ws)
        await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})
    ws_b.send.reset_mock()
    await gw_a.process_command(ws_a, {"cmd": "typing", "cert_id": "cert-A"})
    await asyncio.sleep(0.1)
    got = [json.loads(c[0][0]) for c in ws_b.send.call_args_list]
    typing = [e for e in got if e.get("type") == "typing"]
    assert typing, "the friend must see the typing indicator"
    assert typing[-1]["cert_id"] == "cert-B"
    assert typing[-1]["from_webid"] == a_gw_did
