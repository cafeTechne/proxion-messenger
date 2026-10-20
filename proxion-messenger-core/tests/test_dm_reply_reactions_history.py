"""DM replies, reactions and edits survive the trip across gateways and a reload.

A cross-gateway reply arrived without its reply_to_id (the relay dropped it, so
the quote was lost), and the DM history the browser loads on reload carried no
reactions and no edited marker.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("websockets")
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gwharness import free_port, start_gateway
from gwharness import register as _gw_register

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_envelope


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    ws.remote_address = ("127.0.0.1", 1)
    return ws


def _sent(ws, type_):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("type") == type_]


def _make_gw(agent, ws_port, http_port, db_path):
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path)
    return ProxionGateway(agent, {}, {}, cfg, ReadState())


async def _recv_until(ws, pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            continue
        seen.append(msg)
        if pred(msg):
            return msg, seen
    return None, seen


async def _drain(ws):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.15)
        except Exception:
            return


# ── cross-gateway DM reply, through two real gateways ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("sealed", [False, True])
async def test_cross_gateway_dm_reply_keeps_reply_to_id(tmp_path, sealed):
    agent_a, agent_b = AgentState.generate(), AgentState.generate()
    ws_a, http_a, ws_b, http_b = free_port(), free_port(), free_port(), free_port()
    gw_a = _make_gw(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b = _make_gw(agent_b, ws_b, http_b, str(tmp_path / "b.db"))
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)
    if sealed:
        gw_a._store.save_gateway_seal_key(did_b, gw_b._own_x25519_pub_b64)
    start_gateway(gw_a, ws_a, http_a)
    start_gateway(gw_b, ws_b, http_b)
    browser_a = _did()

    async with websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a, \
            websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b:
        await _gw_register(conn_a, browser_a)
        await _gw_register(conn_b, did_b)
        await _drain(conn_a)
        await _drain(conn_b)

        for content, reply_to, expect in (
            ("a reply", "parent-msg-1", "parent-msg-1"),
            # A malformed pointer is dropped; the message itself still arrives.
            ("bad pointer", "../../x y", None),
        ):
            await conn_a.send(json.dumps({
                "cmd": "local_dm", "target_webid": did_b,
                "target_gateway_url": f"http://127.0.0.1:{http_b}",
                "content": content, "thread_id": "t-reply", "reply_to_id": reply_to,
            }))
            got, seen = await _recv_until(
                conn_b, lambda m: m.get("type") == "message" and m.get("content") == content)
            assert got is not None, f"B never got {content!r}: {seen}"
            assert got.get("reply_to_id") == expect, got
            # B persists right after delivering to the socket.
            stored = None
            for _ in range(40):
                stored = gw_b._store.get_message(got["message_id"])
                if stored:
                    break
                await asyncio.sleep(0.05)
            assert stored is not None
            assert stored.get("reply_to_id") == expect


# ── cross-gateway room reply (signed room_message relay) ───────────────────

@pytest.mark.asyncio
async def test_relayed_room_message_keeps_reply_to_id(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "r.db")))
    room_id = "room-R"
    member = _did()
    peer_key = Ed25519PrivateKey.generate()
    peer_did = pub_key_to_did(peer_key.public_key().public_bytes_raw())
    ws = _mock_ws()
    gw._local_rooms[room_id] = {"name": "R", "members": {ws}}
    gw._store.add_federated_room_member(room_id, member, "https://a.example")
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "content_type": "room_message", "room_id": room_id, "thread_id": room_id,
        "from_webid": member, "from_display_name": "M", "content": "re: hi",
        "message_id": "rm-reply", "timestamp": now, "reply_to_id": "rm-parent",
        "relay_sig_did": peer_did, "relay_ts": now, "relay_nonce": "rn-reply1",
    }
    payload["signature"] = sign_relay_envelope(peer_key, payload)
    status, body = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200"), body
    live = _sent(ws, "message")
    assert live and live[0]["reply_to_id"] == "rm-parent"
    assert gw._store.get_message("rm-reply")["reply_to_id"] == "rm-parent"


@pytest.mark.asyncio
async def test_outbound_room_relay_carries_reply_to_id(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "o.db")))
    sent = []

    async def _capture(url, payload):
        sent.append(payload)
        return True

    gw._post_or_queue_relay = _capture
    base = {"type": "message", "source": "local_room", "thread_id": "room-O",
            "from_webid": _did(), "content": "x", "timestamp": "2026-10-18T00:00:00+00:00",
            "message_id": "m-o"}
    await gw._relay_room_message("http://peer.example", "room-O", {**base, "reply_to_id": "m-p"})
    await gw._relay_room_message("http://peer.example", "room-O", {**base, "reply_to_id": None})
    assert sent[0]["reply_to_id"] == "m-p"
    assert "reply_to_id" not in sent[1]


# ── DM history after reload ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dm_history_carries_reactions_and_edited_marker(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "h.db")))
    me, peer = _did(), _did()
    cert_id = "11111111-2222-3333-4444-555555555555"
    gw._store.save_message("dm-1", cert_id, "dm", me, "Me", "hello", "2026-10-18T00:00:00+00:00")
    gw._store.update_message("dm-1", "hello again", edited_at="2026-10-18T00:01:00+00:00")
    gw._store.save_reaction(cert_id, "dm-1", "~sealedtoken", peer)
    ws = _mock_ws()
    gw._client_webids[ws] = me

    # Cert DM threads load through read_dm ...
    await gw._handle_read_dm(ws, {"cert_id": cert_id})
    hist = _sent(ws, "history")[-1]
    assert hist["messages"][0]["edited_at"] == "2026-10-18T00:01:00+00:00"
    assert hist["reactions"] == [{"message_id": "dm-1", "emoji": "~sealedtoken", "sender_webid": peer}]

    # ... and local DM threads through get_local_history.
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": cert_id})
    lh = _sent(ws, "local_history")
    assert lh, [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert [r["message_id"] for r in lh[-1]["reactions"]] == ["dm-1"]
