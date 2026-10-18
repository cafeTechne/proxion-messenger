"""A disappearing-message timer applies from when it was turned on.

Setting a timer used to delete every older message in the thread at once,
including messages sent long before anyone turned disappearing messages on.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState


@pytest.fixture(autouse=True)
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _events(ws, type_):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("type") == type_]


def _gw(tmp_path, name="g"):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / f"{name}.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _seed_rel(gw, cert_id, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": cert_id, "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=peer_did, owner_webid=owner)


async def _one_sweep(gw):
    n = {"i": 0}

    async def fake_sleep(_):
        n["i"] += 1
        if n["i"] >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gw._expire_messages_loop()
        except asyncio.CancelledError:
            pass


def _iso(seconds_from_now):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)).isoformat()


@pytest.mark.asyncio
async def test_dm_timer_spares_messages_sent_before_it_was_on(tmp_path):
    gw = _gw(tmp_path)
    me = _did()
    _seed_rel(gw, "cert-A", "did:key:zPeer", owner=me)
    gw._store.save_dm_thread("cert-A", "did:key:zPeer", None, owner_webid=me)
    ws = _mock_ws()
    await _register(gw, ws, me)
    gw._store.save_message("before", "cert-A", "dm", me, "M", "old", _iso(-3600))

    await gw.process_command(ws, {"cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 500})
    since = gw._disappear_since["cert-A"]
    # Sent after the timer went on and already past its 500 ms.
    gw._store.save_message("after", "cert-A", "dm", me, "M", "new",
                           (datetime.fromisoformat(since) + timedelta(milliseconds=1)).isoformat())
    await asyncio.sleep(0.6)
    await _one_sweep(gw)

    assert gw._store.get_message("after") is None
    assert gw._store.get_message("before") is not None, "a message from before the timer expired"
    ev = _events(ws, "dm_messages_expired")
    assert ev and ev[-1]["since_timestamp"] == since


@pytest.mark.asyncio
async def test_room_timer_spares_messages_sent_before_it_was_on(tmp_path):
    gw = _gw(tmp_path)
    owner = _did()
    ws = _mock_ws()
    await _register(gw, ws, owner)
    room_id = "room-since"
    gw._local_rooms[room_id] = {"name": "r", "members": {ws}, "messages": [],
                                "history_mode": "none", "creator_webid": owner,
                                "owner_webid": owner}
    gw._check_room_permission = lambda *_a, **_k: True
    gw._store.save_message("r-before", room_id, "room", owner, "O", "old", _iso(-3600))
    await gw.process_command(ws, {"cmd": "set_disappear_timer", "room_id": room_id, "ms": 500})
    since = gw._disappear_since[room_id]
    gw._store.save_message("r-after", room_id, "room", owner, "O", "new",
                           (datetime.fromisoformat(since) + timedelta(milliseconds=1)).isoformat())
    await asyncio.sleep(0.6)
    await _one_sweep(gw)
    assert gw._store.get_message("r-after") is None
    assert gw._store.get_message("r-before") is not None


@pytest.mark.asyncio
async def test_timer_start_kept_on_change_cleared_on_off_and_survives_restart(tmp_path):
    gw = _gw(tmp_path, "p")
    me = _did()
    _seed_rel(gw, "cert-A", "did:key:zPeer", owner=me)
    gw._store.save_dm_thread("cert-A", "did:key:zPeer", None, owner_webid=me)
    ws = _mock_ws()
    await _register(gw, ws, me)
    await gw.process_command(ws, {"cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 60_000})
    first = gw._disappear_since["cert-A"]
    await asyncio.sleep(0.01)
    await gw.process_command(ws, {"cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 300_000})
    assert gw._disappear_since["cert-A"] == first

    gw2 = _gw(tmp_path, "p")
    assert gw2._disappear_since.get("cert-A") == first

    await gw.process_command(ws, {"cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 0})
    assert "cert-A" not in gw._disappear_since
    assert "cert-A" not in _gw(tmp_path, "p")._disappear_since


@pytest.mark.asyncio
async def test_relayed_timer_starts_on_the_peer_gateway_too(tmp_path, monkeypatch):
    gw_a, gw_b = _gw(tmp_path, "a"), _gw(tmp_path, "b")
    a_did, b_did = _did(), _did()
    _seed_rel(gw_a, "cert-A", b_did, owner=a_did)
    gw_a._peer_gateway_urls[b_did] = "http://gw-b.test"
    gw_a._store.save_dm_thread("cert-A", b_did, None, owner_webid=a_did)
    _seed_rel(gw_b, "cert-B", pub_key_to_did(gw_a.agent.identity_pub_bytes), owner=b_did)

    async def _fake_post(url, payload):
        status, _ = await gw_b._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("200")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _fake_post)

    ws_a = _mock_ws()
    await _register(gw_a, ws_a, a_did)
    await gw_a.process_command(ws_a, {"cmd": "set_disappear_timer", "room_id": "cert-A", "ms": 3_600_000})
    await asyncio.sleep(0.1)
    assert gw_b._dm_disappear_timers.get("cert-B") == 3_600_000
    assert gw_b._disappear_since.get("cert-B"), "the peer gateway applied the timer retroactively"
