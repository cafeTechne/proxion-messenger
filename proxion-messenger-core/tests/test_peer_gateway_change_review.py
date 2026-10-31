"""Reviewing a contact's unproven gateway URL change.

A contact seen at a new gateway URL without a claim signed by their key stays
pinned to the old URL until the account holding that contact approves the move.
Only that account may list, approve or reject it; rejecting clears the pending
change and keeps the old route.
"""
import asyncio
import json
import time

import pytest
import websockets

from gwharness import free_port, start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

T = 5.0
OLD, NEW = "https://old.example", "https://new.example"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("PROXION_CSS_URL", "PROXION_CSS_EMAIL", "PROXION_CSS_PASSWORD"):
        monkeypatch.setenv(k, "")


def _did():
    return pub_key_to_did(AgentState.generate().identity_pub_bytes)


@pytest.fixture
def gw(tmp_path):
    from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        db_path=str(tmp_path / "proxion.db"))
    g = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    h = start_gateway(g, ws_port, http_port)
    yield g, ws_port, h
    h.stop()


def _seed(g, owner, peer):
    now = int(time.time())
    g._store.save_relationship({"certificate_id": "cert-" + peer[-8:], "subject": "00" * 32,
                                "created_at": now, "expires_at": now + 86400},
                               peer_did=peer, owner_webid=owner)
    g._store.save_display_name(peer, "Alice")
    g._store.upsert_peer_gateway_pin(peer_did=peer, pinned_gateway_url=OLD, pinned_at=now,
                                     last_seen_gateway_url=NEW, last_seen_at=now,
                                     pending_change=True)
    g._store.save_peer_gateway(peer, OLD)
    g._peer_gateway_urls[peer] = OLD


async def _recv(conn, pred):
    deadline = time.monotonic() + T
    while time.monotonic() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(conn.recv(), timeout=0.3))
        except asyncio.TimeoutError:
            continue
        if pred(m):
            return m
    return None


async def _reg(conn, did):
    await conn.send(json.dumps({"cmd": "register", "did": did}))
    assert await _recv(conn, lambda m: m.get("type") == "registered")
    log = []
    while True:  # the connect snapshot
        try:
            log.append(json.loads(await asyncio.wait_for(conn.recv(), timeout=0.3)))
        except asyncio.TimeoutError:
            return log


async def _ask(conn, cmd, **kw):
    await conn.send(json.dumps({"cmd": cmd, **kw}))
    want = {"get_peer_gateway_changes": "peer_gateway_changes",
            "approve_peer_gateway_change": "peer_gateway_change_approved",
            "reject_peer_gateway_change": "peer_gateway_change_rejected"}[cmd]
    return await _recv(conn, lambda m: m.get("type") in (want, "error"))


def test_only_the_contacts_account_reviews_and_approves(gw):
    g, port, h = gw
    owner, stranger, peer = _did(), _did(), _did()
    _seed(g, owner, peer)
    g._store.enqueue_relay("r1", peer, OLD, {"to_webid": peer})

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}") as mine, \
                websockets.connect(f"ws://127.0.0.1:{port}") as other:
            snap = await _reg(mine, owner)
            # The connect snapshot already carries it for review.
            pushed = [m for m in snap if m.get("type") == "peer_gateway_changes"]
            assert pushed and pushed[0]["changes"][0]["new_url"] == NEW
            await _reg(other, stranger)
            # Another account on the gateway neither sees nor decides it.
            seen = await _ask(other, "get_peer_gateway_changes")
            assert seen["type"] == "peer_gateway_changes" and seen["changes"] == []
            for cmd in ("approve_peer_gateway_change", "reject_peer_gateway_change"):
                err = await _ask(other, cmd, peer_did=peer)
                assert err["type"] == "error" and err["code"] == "E_FORBIDDEN"
            pin = g._store.get_peer_gateway_pin(peer)
            assert pin["pinned_gateway_url"] == OLD and pin["pending_change"]

            listed = await _ask(mine, "get_peer_gateway_changes")
            assert listed["changes"] == [{
                "peer_did": peer, "display_name": "Alice", "old_url": OLD, "new_url": NEW,
                "observed_at": listed["changes"][0]["observed_at"]}]
            ok = await _ask(mine, "approve_peer_gateway_change", peer_did=peer)
            assert ok["type"] == "peer_gateway_change_approved" and ok["approved"] is True
            pin = g._store.get_peer_gateway_pin(peer)
            assert pin["pinned_gateway_url"] == NEW and not pin["pending_change"]
            assert g._resolve_peer_gateway(peer) == NEW
            assert [r["to_gateway_url"] for r in g._store.get_pending_relays()] == [NEW]
            after = await _recv(mine, lambda m: m.get("type") == "peer_gateway_changes")
            assert after["changes"] == []
    asyncio.run(run())


def test_reject_keeps_the_old_route_and_clears_the_pending_change(gw):
    g, port, h = gw
    owner, peer = _did(), _did()
    _seed(g, owner, peer)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}") as mine:
            await _reg(mine, owner)
            no = await _ask(mine, "reject_peer_gateway_change", peer_did=peer)
            assert no["type"] == "peer_gateway_change_rejected" and no["rejected"] is True
            pin = g._store.get_peer_gateway_pin(peer)
            assert pin["pinned_gateway_url"] == OLD and not pin["pending_change"]
            assert pin["last_seen_gateway_url"] == OLD
            assert g._resolve_peer_gateway(peer) == OLD
            assert (await _ask(mine, "get_peer_gateway_changes"))["changes"] == []
            # Nothing left to approve after a rejection.
            again = await _ask(mine, "approve_peer_gateway_change", peer_did=peer)
            assert again["approved"] is False
            assert g._store.get_peer_gateway_pin(peer)["pinned_gateway_url"] == OLD
            reqs = g._store.list_peer_gateway_change_requests(peer_did=peer)
            assert all(r["approved"] != 1 for r in reqs)
    asyncio.run(run())


def test_new_pending_change_is_pushed_to_the_account(gw):
    """The unproven URL change itself reaches the account for review."""
    g, port, h = gw
    owner, peer = _did(), _did()
    now = int(time.time())
    g._store.save_relationship({"certificate_id": "cert-x", "subject": "00" * 32,
                                "created_at": now, "expires_at": now + 86400},
                               peer_did=peer, owner_webid=owner)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}") as mine:
            await _reg(mine, owner)
            h.loop.call_soon_threadsafe(g._record_peer_gateway, peer, OLD)
            h.loop.call_soon_threadsafe(g._record_peer_gateway, peer, NEW)
            m = await _recv(mine, lambda m: m.get("type") == "peer_gateway_changes"
                            and m.get("changes"))
            assert m and m["changes"][0]["old_url"] == OLD and m["changes"][0]["new_url"] == NEW
            assert g._resolve_peer_gateway(peer) == OLD
    asyncio.run(run())
