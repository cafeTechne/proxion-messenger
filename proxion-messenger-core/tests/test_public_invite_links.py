"""Invite links, room links and the Proxion address use the gateway's public
URL once a tunnel or UPnP mapping is up, not the loopback host:port the
desktop app binds. A room created before the tunnel hands out the live URL
too. A browser-build room invite pasted into the gateway gets a clear error
instead of a lookup that can never succeed.
"""
import asyncio
import json

import pytest

pytest.importorskip("websockets")
import websockets

from gwharness import free_port, register, start_gateway

TUNNEL = "https://abc.trycloudflare.com"
WEB_LINK = ("https://example.github.io/proxion-messenger/app/?join=room-abc~"
            "https%3A%2F%2Falice.example%2Fprofile%2Fcard%23me&mode=web")
WEB_TOKEN = "room-abc~https%3A%2F%2Falice.example%2Fprofile%2Fcard%23me"


def _gateway(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    start_gateway(gw, ws_port, http_port)
    return gw, ws_port, http_port


async def _recv_type(conn, want, timeout=5.0):
    while True:
        msg = json.loads(await asyncio.wait_for(conn.recv(), timeout))
        if msg.get("type") in (want, "error"):
            return msg


@pytest.mark.asyncio
async def test_links_follow_the_tunnel_url(tmp_path):
    gw, ws_port, http_port = _gateway(tmp_path)
    local = f"http://127.0.0.1:{http_port}"
    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as c:
        await register(c, "did:key:z6MkLinkOwner1")
        await c.send(json.dumps({"cmd": "chat_room_create", "name": "before"}))
        before = await _recv_type(c, "room_created")
        assert before["invite_url"].startswith(local + "/?join=")

        gw.config.public_url = TUNNEL   # what a successful start_tunnel sets
        await c.send(json.dumps({"cmd": "chat_room_create", "name": "after"}))
        after = await _recv_type(c, "room_created")
        assert after["invite_url"] == f"{TUNNEL}/?join={after['code']}"

        await c.send(json.dumps({"cmd": "get_my_address"}))
        addr = await _recv_type(c, "my_address")
        assert addr["gateway_http_url"] == TUNNEL
        assert addr["proxion_address"].endswith("@" + TUNNEL)
        assert addr["invite_link"].startswith(TUNNEL + "/invite?from=")
        if addr["short_invite_url"]:
            assert addr["short_invite_url"].startswith(TUNNEL + "/i/")

        # The room minted before the tunnel is served with the live URL.
        await c.send(json.dumps({"cmd": "get_rooms"}))
        rooms = (await _recv_type(c, "rooms"))["rooms"]
        old = next(r for r in rooms if r["id"] == before["room_id"])
        assert old["invite_url"] == f"{TUNNEL}/?join={before['code']}"

        # A link minted on loopback before the tunnel still joins this gateway.
        await c.send(json.dumps({"cmd": "join_remote_room", "url": before["invite_url"]}))
        joined = await _recv_type(c, "room_joined")
        assert joined.get("room_id") == before["room_id"], joined


@pytest.mark.asyncio
async def test_upnp_url_is_upgraded_to_https_when_tls_is_on(tmp_path):
    gw, _ws, _http = _gateway(tmp_path)
    gw.config.public_url = "http://203.0.113.7:8080"
    assert gw._gateway_http_url() == "http://203.0.113.7:8080"
    gw.config.ssl_certfile, gw.config.ssl_keyfile = "c.pem", "k.pem"
    assert gw._gateway_http_url() == "https://203.0.113.7:8080"
    # A websocket public URL names the WS port, never the HTTP base.
    gw.config.ssl_certfile = gw.config.ssl_keyfile = None
    gw.config.public_url = "wss://chat.example.com/ws"
    assert gw._gateway_http_url() == f"http://127.0.0.1:{gw.config.http_port}"


@pytest.mark.asyncio
async def test_browser_room_invite_is_refused_clearly(tmp_path):
    _gw, ws_port, _http = _gateway(tmp_path)
    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as c:
        await register(c, "did:key:z6MkLinkOwner2")
        await c.send(json.dumps({"cmd": "join_remote_room", "url": WEB_LINK}))
        e1 = await _recv_type(c, "room_joined")
        assert e1 == {"type": "error", "message": "web_room_invite"}
        await c.send(json.dumps({"cmd": "join_room", "code": WEB_TOKEN}))
        e2 = await _recv_type(c, "room_joined")
        assert e2 == {"type": "error", "message": "web_room_invite"}
