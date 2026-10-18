"""Send and connection resilience: the gateway side the web client relies on.

* ``ping`` is answered with ``pong`` so the client heartbeat can tell a live
  socket from a half-open one (web/connection.js).
* A refused write names the client's ``message_id``, so the pending bubble
  fails or retries at once instead of waiting out the confirm timeout.
* The client's send pacer mirrors the gateway's rate-limit exemptions.
"""
import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets

from proxion_messenger_core.command_validation import SNAPSHOT_READ_COMMANDS
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from gwharness import free_port, start_gateway

WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.fixture
def gateway():
    return ProxionGateway(agent=AgentState.generate(), dm_clients=[], room_memberships=[],
                          config=GatewayConfig(port=9981), read_state=ReadState())


def _registered_ws(gw, webid="did:key:zSender"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _frames(ws):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list]


@pytest.mark.asyncio
async def test_ping_gets_pong(gateway):
    ws = MagicMock()
    ws.send = AsyncMock()
    await gateway.process_command(ws, {"cmd": "ping"})
    assert _frames(ws) == [{"type": "pong"}]


@pytest.mark.asyncio
async def test_rate_refusal_names_the_message_id(gateway):
    ws = _registered_ws(gateway)
    for _ in range(60):
        gateway._check_ws_rate_limit(ws)
    await gateway.process_command(ws, {"cmd": "send_room", "room_id": "r", "content": "x",
                                       "message_id": "m-61"})
    err = _frames(ws)[-1]
    assert err["code"] == "E_RATE"
    assert err["cmd"] == "send_room"
    assert err["message_id"] == "m-61"
    assert err["retry_after"] >= 1


@pytest.mark.asyncio
async def test_rate_refusal_without_message_id_is_unchanged(gateway):
    ws = _registered_ws(gateway)
    for _ in range(60):
        gateway._check_ws_rate_limit(ws)
    await gateway.process_command(ws, {"cmd": "mark_read", "thread_id": "t"})
    err = _frames(ws)[-1]
    assert err["code"] == "E_RATE" and "message_id" not in err


@pytest.mark.asyncio
async def test_forbidden_room_send_names_the_message_id(gateway):
    ws = _registered_ws(gateway, "did:key:zOutsider")
    gateway._local_rooms["room-x"] = {"name": "x", "owner": "did:key:zOwner", "members": set()}
    await gateway.process_command(ws, {"cmd": "send_room", "room_id": "room-x", "content": "hi",
                                       "message_id": "m-1"})
    err = _frames(ws)[-1]
    assert err.get("code") == "E_FORBIDDEN"
    assert err.get("message_id") == "m-1"


def test_ping_over_a_real_socket(tmp_path):
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}", db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=cfg, read_state=ReadState())
    start_gateway(gw, ws_port, http_port)

    async def _go():
        async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as ws:
            # Heartbeats are rate exempt: a burst of them all get answered.
            for _ in range(80):
                await ws.send(json.dumps({"cmd": "ping"}))
            got = []
            while len(got) < 80:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if frame.get("type") == "pong":
                    got.append(frame)
                assert frame.get("type") != "error", frame
            return got

    loop = asyncio.new_event_loop()
    try:
        assert len(loop.run_until_complete(_go())) == 80
    finally:
        loop.close()


def _js_set(name):
    src = (WEB / "connection.js").read_text(encoding="utf-8")
    m = re.search(r"export const " + name + r" = new Set\(\[(.*?)\]\);", src, re.S)
    assert m, name
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_client_pacer_exemptions_match_the_gateway():
    unpaced = _js_set("UNPACED_CMDS")
    signal = set(ProxionGateway._SIGNAL_RATE_COMMANDS)
    assert unpaced == {"ping", "pong", "file_chunk"} | signal | set(SNAPSHOT_READ_COMMANDS)
