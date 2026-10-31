"""Withdrawing a sent friend request, and requests to a gateway that is down.

Two live gateways. A withdrawal is signed by the requester's gateway and reaches
the target whether it is up (direct) or down (outbound queue, retried when it is
back); only the original requester can withdraw; a withdrawn request cannot be
accepted. A request to a gateway that is down waits on the outbound queue and is
delivered when the peer is back, or ends when it expires.
"""
import asyncio
import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("websockets")
import websockets

from gwharness import free_port, start_gateway
from test_contacts_presence_federation import _client_did, _wait, _drain, _register, _invite, _post


@pytest.fixture(autouse=True)
def _local_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")
    for var in ("PROXION_CSS_URL", "PROXION_CSS_EMAIL", "PROXION_CSS_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def _start(tmp_path, name, agent=None, ports=None):
    """A live gateway; pass the agent and ports of a stopped one to bring it back."""
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    ws_port, http_port = ports or (free_port(), free_port())
    agent = agent or AgentState.generate()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / f"{name}.db"))
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    handle = start_gateway(gw, ws_port, http_port)
    return SimpleNamespace(gw=gw, agent=agent, ws=ws_port, http=f"http://127.0.0.1:{http_port}",
                           ports=(ws_port, http_port), handle=handle, name=name,
                           did=gw._own_gateway_did())


def _restart(tmp_path, g):
    return _start(tmp_path, g.name, agent=g.agent, ports=g.ports)


async def _on_loop(g, coro):
    """Run a coroutine on the gateway's own event loop."""
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, g.handle.loop))


def _addr(g):
    return f"{g.did}@{g.http}"


def _status(g, iid):
    return (g.gw._store.get_pending_invite_row(iid) or {}).get("status")


def _relay_ids(g):
    return [r["id"] for r in g.gw._store.get_pending_relays()]


async def _send_request(conn, target):
    await conn.send(json.dumps({"cmd": "send_friend_request", "target_address": _addr(target)}))
    sent = await _wait(conn, lambda m: m.get("type") in ("friend_request_sent", "error"))
    assert sent and sent["type"] == "friend_request_sent", sent
    return sent


async def _sent_list(conn):
    await conn.send(json.dumps({"cmd": "list_friend_requests"}))
    resp = await _wait(conn, lambda m: m.get("type") == "friend_requests" and "sent" in m)
    return resp["sent"]


def _withdraw_body(signer_agent, iid, from_did):
    from proxion_messenger_core.gateway import invite_withdraw_signing_input
    ts = datetime.now(timezone.utc).isoformat()
    sig = signer_agent.identity_key.sign(invite_withdraw_signing_input(iid, from_did, ts))
    return {"invitation_id": iid, "from_did": from_did, "timestamp": ts,
            "signature": base64.urlsafe_b64encode(sig).rstrip(b"=").decode()}


@pytest.mark.asyncio
async def test_withdraw_reaches_an_online_recipient_and_accept_is_refused(tmp_path):
    a, b = _start(tmp_path, "a"), _start(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a.ws}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b.ws}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        sent = await _send_request(conn_a, b)
        assert sent["queued"] is False
        iid = sent["invitation_id"]
        assert await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")
        assert [s["invitation_id"] for s in await _sent_list(conn_a)] == [iid]

        await conn_a.send(json.dumps({"cmd": "withdraw_friend_request", "invitation_id": iid}))
        done = await _wait(conn_a, lambda m: m.get("type") in ("friend_request_withdrawn", "error"))
        assert done["type"] == "friend_request_withdrawn" and done["invitation_id"] == iid
        gone = await _wait(conn_b, lambda m: m.get("type") == "friend_request_dismissed")
        assert gone["invitation_id"] == iid and gone["withdrawn"] is True
        assert _status(a, iid) == "withdrawn" and _status(b, iid) == "withdrawn"
        assert await _sent_list(conn_a) == []

        # A stale card on Bob's side cannot add Alice.
        await conn_b.send(json.dumps({"cmd": "accept_friend_request", "invitation_id": iid}))
        err = await _wait(conn_b, lambda m: m.get("type") in ("error", "friend_request_accepted"))
        assert err["type"] == "error" and err["message"] == "invite_not_found"
        # Withdrawing twice is refused, not repeated.
        await conn_a.send(json.dumps({"cmd": "withdraw_friend_request", "invitation_id": iid}))
        err = await _wait(conn_a, lambda m: m.get("type") in ("error", "friend_request_withdrawn"))
        assert err["type"] == "error" and err["message"] == "invite_not_found"
    assert b.gw._store.list_relationships() == []
    assert a.gw._store.list_relationships() == []


@pytest.mark.asyncio
async def test_withdraw_while_the_recipient_is_down_is_delivered_when_it_is_back(tmp_path):
    a, b = _start(tmp_path, "a"), _start(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a.ws}") as conn_a:
        await _register(conn_a, _client_did(), "alice")
        async with websockets.connect(f"ws://127.0.0.1:{b.ws}") as conn_b:
            await _register(conn_b, _client_did(), "bob")
            iid = (await _send_request(conn_a, b))["invitation_id"]
            assert await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")
        b.handle.stop()

        await conn_a.send(json.dumps({"cmd": "withdraw_friend_request", "invitation_id": iid}))
        assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_withdrawn")
        for _ in range(50):
            if _relay_ids(a):
                break
            await asyncio.sleep(0.1)
        assert _relay_ids(a) == [f"invite_withdraw:{iid}"]
        row = a.gw._store.get_pending_relays()[0]
        assert row["expires_at"] == pytest.approx(a.gw._store.get_pending_invite_row(iid)["invite"]["expires_at"])

        b2 = _restart(tmp_path, b)
        assert _status(b2, iid) == "pending"
        async with websockets.connect(f"ws://127.0.0.1:{b2.ws}") as conn_b:
            await _register(conn_b, _client_did(), "bob")
            await _drain(conn_b)
            await _on_loop(a, a.gw._flush_relays_to_peer(b.did, ""))
            gone = await _wait(conn_b, lambda m: m.get("type") == "friend_request_dismissed")
            assert gone and gone["invitation_id"] == iid
        assert _status(b2, iid) == "withdrawn"
        assert _relay_ids(a) == []


@pytest.mark.asyncio
async def test_only_the_original_requester_can_withdraw(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    a, b = _start(tmp_path, "a"), _start(tmp_path, "b")
    async with websockets.connect(f"ws://127.0.0.1:{a.ws}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{b.ws}") as conn_b:
        await _register(conn_a, _client_did(), "alice")
        await _register(conn_b, _client_did(), "bob")
        iid = (await _send_request(conn_a, b))["invitation_id"]
        assert await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")

        mallory = AgentState.generate()
        mallory_did = pub_key_to_did(mallory.identity_pub_bytes)
        # Mallory signing as herself: not the issuer, ignored.
        assert _post(f"{b.http}/invite/withdraw", _withdraw_body(mallory, iid, mallory_did)).status_code == 200
        # Mallory claiming to be Alice: the signature does not hold.
        assert _post(f"{b.http}/invite/withdraw", _withdraw_body(mallory, iid, a.did)).status_code == 400
        # Bob cannot "withdraw" a request he received; that is Ignore.
        await conn_b.send(json.dumps({"cmd": "withdraw_friend_request", "invitation_id": iid}))
        err = await _wait(conn_b, lambda m: m.get("type") in ("error", "friend_request_withdrawn"))
        assert err["type"] == "error" and err["message"] == "invite_not_found"
        assert await _wait(conn_b, lambda m: m.get("type") == "friend_request_dismissed", timeout=1) is None
        assert _status(b, iid) == "pending"

        # The real one still works afterwards.
        assert _post(f"{b.http}/invite/withdraw", _withdraw_body(a.agent, iid, a.did)).status_code == 200
        assert _status(b, iid) == "withdrawn"


@pytest.mark.asyncio
async def test_a_request_to_a_gateway_that_is_down_is_delivered_when_it_is_back(tmp_path):
    a, b = _start(tmp_path, "a"), _start(tmp_path, "b")
    b.handle.stop()
    async with websockets.connect(f"ws://127.0.0.1:{a.ws}") as conn_a:
        await _register(conn_a, _client_did(), "alice")
        sent = await _send_request(conn_a, b)
        iid = sent["invitation_id"]
        assert sent["queued"] is True
        assert _relay_ids(a) == [f"friend_request:{iid}"]
        row = a.gw._store.get_pending_relays()[0]
        assert row["expires_at"] == pytest.approx(sent["expires_at"])
        listed = await _sent_list(conn_a)
        assert [(s["invitation_id"], s["queued"]) for s in listed] == [(iid, True)]

        # Still down: a retry keeps it queued.
        await _on_loop(a, a.gw._flush_relays_to_peer(b.did, ""))
        assert _relay_ids(a) == [f"friend_request:{iid}"]

        b2 = _restart(tmp_path, b)
        async with websockets.connect(f"ws://127.0.0.1:{b2.ws}") as conn_b:
            await _register(conn_b, _client_did(), "bob")
            await _drain(conn_b)
            await _on_loop(a, a.gw._flush_relays_to_peer(b.did, ""))
            got = await _wait(conn_b, lambda m: m.get("type") == "friend_request_received")
            assert got and got["invitation_id"] == iid and got["from_did"] == a.did
            assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_delivered"
                               and m.get("invitation_id") == iid)
            assert _relay_ids(a) == []
            assert [s["queued"] for s in await _sent_list(conn_a)] == [False]

            # And it is a normal request from here on.
            await conn_b.send(json.dumps({"cmd": "accept_friend_request", "invitation_id": iid}))
            assert await _wait(conn_b, lambda m: m.get("type") == "friend_request_accepted")
            assert await _wait(conn_a, lambda m: m.get("type") == "contact_added")


@pytest.mark.asyncio
async def test_withdrawing_a_queued_request_drops_it_from_the_queue(tmp_path, monkeypatch):
    a, b = _start(tmp_path, "a"), _start(tmp_path, "b")
    b.handle.stop()
    async with websockets.connect(f"ws://127.0.0.1:{a.ws}") as conn_a:
        await _register(conn_a, _client_did(), "alice")
        iid = (await _send_request(conn_a, b))["invitation_id"]
        await conn_a.send(json.dumps({"cmd": "withdraw_friend_request", "invitation_id": iid}))
        assert await _wait(conn_a, lambda m: m.get("type") == "friend_request_withdrawn")
        await asyncio.sleep(0.3)
    # Never delivered, so there is nobody to tell.
    assert _relay_ids(a) == []
    assert _status(a, iid) == "withdrawn"


@pytest.mark.asyncio
async def test_a_queued_request_ends_when_it_expires_or_is_refused(tmp_path, monkeypatch):
    from proxion_messenger_core import network
    a, b = _start(tmp_path, "a"), _start(tmp_path, "b")
    b.handle.stop()
    async with websockets.connect(f"ws://127.0.0.1:{a.ws}") as conn_a:
        await _register(conn_a, _client_did(), "alice")
        first = (await _send_request(conn_a, b))["invitation_id"]
        second = (await _send_request(conn_a, b))["invitation_id"]
        with a.gw._store._conn() as db:
            db.execute("UPDATE pending_relays SET expires_at = 1 WHERE id = ?",
                       (f"friend_request:{first}",))
        await _on_loop(a, a.gw._expire_stale_relays())
        ev = await _wait(conn_a, lambda m: m.get("type") == "friend_request_failed")
        assert ev["invitation_id"] == first and ev["reason"] == "expired"
        assert _status(a, first) == "expired"

        async def refused(url, payload, **kw):
            return 403
        monkeypatch.setattr(network, "async_safe_post_status", refused)
        await _on_loop(a, a.gw._flush_relays_to_peer(b.did, ""))
        ev = await _wait(conn_a, lambda m: m.get("type") == "friend_request_failed")
        assert ev["invitation_id"] == second and ev["reason"] == "failed"
        assert _relay_ids(a) == []
        assert await _sent_list(conn_a) == []
