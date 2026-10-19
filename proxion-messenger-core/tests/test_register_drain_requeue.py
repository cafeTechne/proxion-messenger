"""Messages queued for an offline identity are drained on register. If the socket
dies part-way through the drain, the undelivered ones go back in the queue
instead of being lost."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

DID = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"


def _ws(fail_types=()):
    ws = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    sent = []

    async def _send(raw):
        evt = json.loads(raw)
        if evt.get("type") in fail_types:
            raise ConnectionError("socket closed")
        sent.append(evt)
    ws.send = AsyncMock(side_effect=_send)
    ws.sent = sent
    return ws


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "q.db")),
    )


async def _register(gw, ws):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": DID, "display_name": "D"})


@pytest.mark.asyncio
async def test_relay_queue_survives_a_failed_drain(gw):
    gw._relay_queue[DID] = [
        {"from_webid": "did:key:zPeer", "message_id": f"m{i}", "content": f"hi {i}",
         "timestamp": "2026-01-01T00:00:00+00:00", "cert_id": "c1"}
        for i in range(3)
    ]
    await _register(gw, _ws(fail_types=("message",)))
    assert [m["message_id"] for m in gw._relay_queue.get(DID, [])] == ["m0", "m1", "m2"]

    good = _ws()
    await _register(gw, good)
    got = [e["message_id"] for e in good.sent if e.get("type") == "message"]
    assert got == ["m0", "m1", "m2"]
    assert not gw._relay_queue.get(DID)


@pytest.mark.asyncio
async def test_fanout_queue_survives_a_failed_drain(gw):
    key = (DID, DID)
    gw._fanout_queue[key] = [
        {"message_id": f"f{i}", "from_webid": "did:key:zPeer", "payload": {"e2e": True}}
        for i in range(2)
    ]
    await _register(gw, _ws(fail_types=("dm_fanout",)))
    assert [e["message_id"] for e in gw._fanout_queue.get(key, [])] == ["f0", "f1"]

    good = _ws()
    await _register(gw, good)
    assert [e["message_id"] for e in good.sent if e.get("type") == "dm_fanout"] == ["f0", "f1"]
    assert not gw._fanout_queue.get(key)
