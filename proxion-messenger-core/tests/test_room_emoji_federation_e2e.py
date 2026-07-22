"""E2E: room-emoji federation over the REAL /relay HTTP path (R60D).

The unit tests in test_room_emoji.py call the inbound handler directly,
bypassing the relay envelope signing + verification layer — historically the
layer where federation bugs actually live. Here two in-process gateways run
their real HTTP servers: the host (A) fans an emoji delta out via
_relay_room_emoji → signed envelope → HTTP POST /relay → member gateway (B)
verifies and applies it.
"""
import asyncio
import json
import socket
import time

import pytest

pytest.importorskip("websockets")
import websockets  # noqa: F401  (gateway import path expects it)
from gwharness import start_gateway as _serve_gw


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(agent, ws_port: int, http_port: int, db_path: str):
    import asyncio as _asyncio
    import websockets as _ws
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path,
    )
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.loop, handle.ready


@pytest.fixture(autouse=True)
def allow_private_relay(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    # Loopback-http test gateways: opt out of the HTTPS enforcement on
    # federation mutations (same env the two-gateway browser smoke sets).
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg==")


@pytest.mark.asyncio
async def test_room_emoji_delta_federates_over_real_relay(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    ws_a, ws_b = _free_port(), _free_port()
    http_a, http_b = _free_port(), _free_port()
    agent_a, agent_b = AgentState.generate(), AgentState.generate()

    gw_a, loop_a, ready_a = _start_gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, loop_b, ready_b = _start_gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))
    assert ready_a.wait(timeout=5) and ready_b.wait(timeout=5)
    await asyncio.sleep(0.2)

    owner_did = pub_key_to_did(agent_a.identity_pub_bytes)
    member_did = pub_key_to_did(agent_b.identity_pub_bytes)
    room_id = "room-fed-emoji"

    # Host (A): room + a federated member homed on B.
    def _setup_a():
        gw_a._local_rooms[room_id] = {
            "name": "Fed", "code": "x" * 64, "members": set(),
            "invite_url": "", "history_mode": "none", "messages": [],
            "creator_webid": owner_did,
        }
        gw_a._store.add_federated_room_member(room_id, member_did, f"http://127.0.0.1:{http_b}")
    loop_a.call_soon_threadsafe(_setup_a)

    # Member gateway (B): local mirror of the room (created at join time in
    # production) — the inbound handler's authz reads creator_webid from it.
    def _setup_b():
        gw_b._local_rooms[room_id] = {
            "name": "Fed", "code": "y" * 64, "members": set(),
            "invite_url": "", "history_mode": "none", "messages": [],
            "creator_webid": owner_did,
        }
    loop_b.call_soon_threadsafe(_setup_b)
    await asyncio.sleep(0.2)

    # Fire the outbound delta on A's loop (what _handle_add_room_emoji does
    # after a successful admin add).
    def _fire():
        gw_a._store.save_room_emoji(room_id, "fedmoji", "image/png", PNG_B64, owner_did)
        gw_a._relay_room_emoji(room_id, "add", "fedmoji", owner_did,
                               mime="image/png", data_b64=PNG_B64)
    loop_a.call_soon_threadsafe(_fire)

    # B applies it (signed envelope verified by the real /relay pipeline).
    deadline = time.monotonic() + 5.0
    applied = []
    while time.monotonic() < deadline:
        applied = gw_b._store.get_room_emoji(room_id)
        if applied:
            break
        await asyncio.sleep(0.2)
    assert [e["name"] for e in applied] == ["fedmoji"], (
        "emoji delta did not arrive/apply on the member gateway")
    assert applied[0]["mime"] == "image/png"
    assert applied[0]["data_b64"] == PNG_B64

    # Remove federates too.
    loop_a.call_soon_threadsafe(
        gw_a._relay_room_emoji, room_id, "remove", "fedmoji", owner_did)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not gw_b._store.get_room_emoji(room_id):
            break
        await asyncio.sleep(0.2)
    assert gw_b._store.get_room_emoji(room_id) == []
