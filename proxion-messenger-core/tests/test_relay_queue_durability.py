"""Queued cross-gateway relays survive a long peer outage.

A relay queued while the peer gateway is down used to be retried by re-POSTing
the originally signed payload, which the receiver rejects once its timestamp is
more than 5 minutes old. Retries are now re-signed with a fresh transport
timestamp (relay_ts) while the message keeps its original timestamp.
"""
import asyncio
import json
import sqlite3
import time
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("websockets")
import websockets
from gwharness import free_port, start_gateway
from gwharness import register as _gw_register

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


@pytest.fixture(autouse=True)
def allow_private_relay(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")


def _key_did():
    from proxion_messenger_core.didkey import pub_key_to_did
    k = Ed25519PrivateKey.generate()
    return k, pub_key_to_did(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def _iso(minutes_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ── signature freshness ─────────────────────────────────────────────────────

def test_resigned_relay_verifies_hours_later_and_keeps_its_timestamp():
    from proxion_messenger_core.relay import sign_relay_message, verify_relay_message
    k, did = _key_did()
    old_ts = _iso(180)
    # The originally signed payload is stale.
    sig = sign_relay_message(k, did, "did:key:zPeer", "m1", "hi", old_ts, "abcdef0123")
    assert not verify_relay_message(did, "did:key:zPeer", "m1", "hi", old_ts, sig, "abcdef0123")
    # Re-signed at send time: verifies, message timestamp unchanged.
    rts = _iso()
    sig2 = sign_relay_message(k, did, "did:key:zPeer", "m1", "hi", old_ts, "0123abcdef", relay_ts=rts)
    assert verify_relay_message(did, "did:key:zPeer", "m1", "hi", old_ts, sig2, "0123abcdef",
                                relay_ts=rts)
    # relay_ts is bound into the signature and must itself be fresh.
    assert not verify_relay_message(did, "did:key:zPeer", "m1", "hi", old_ts, sig2, "0123abcdef",
                                    relay_ts=_iso(1))
    assert not verify_relay_message(did, "did:key:zPeer", "m1", "hi", old_ts, sig2, "0123abcdef")
    stale_rts = _iso(30)
    sig3 = sign_relay_message(k, did, "did:key:zPeer", "m1", "hi", old_ts, "0123abcdef",
                              relay_ts=stale_rts)
    assert not verify_relay_message(did, "did:key:zPeer", "m1", "hi", old_ts, sig3, "0123abcdef",
                                    relay_ts=stale_rts)
    # The message timestamp is still bounded: not in the future, not older than 2 days.
    for bad_ts in (_iso(-60), _iso(3 * 24 * 60)):
        s = sign_relay_message(k, did, "did:key:zPeer", "m1", "hi", bad_ts, "0123abcdef", relay_ts=rts)
        assert not verify_relay_message(did, "did:key:zPeer", "m1", "hi", bad_ts, s, "0123abcdef",
                                        relay_ts=rts)


# ── live two-gateway outage ─────────────────────────────────────────────────

def _make_gw(agent, ws_port, http_port, db_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState
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


async def _run_on(handle, coro):
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, handle.loop))


async def _queue_dm_while_peer_down(tmp_path, monkeypatch, *, sealed):
    """A's user sends B a DM an hour ago while B's gateway is down."""
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    import proxion_messenger_core._gateway_dm as gdm

    agent_a, agent_b = AgentState.generate(), AgentState.generate()
    ws_a, http_a, ws_b, http_b = free_port(), free_port(), free_port(), free_port()
    gw_a = _make_gw(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b = _make_gw(agent_b, ws_b, http_b, str(tmp_path / "b.db"))
    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)
    if sealed:
        gw_a._store.save_gateway_seal_key(did_b, gw_b._own_x25519_pub_b64)
    h_a = start_gateway(gw_a, ws_a, http_a)

    _, browser_did = _key_did()        # A's user (browser session identity)
    _, other_did = _key_did()          # another account on gateway A
    sent_at = datetime.now(timezone.utc) - timedelta(hours=1)

    class _HourAgo(datetime):
        @classmethod
        def now(cls, tz=None):
            return sent_at if tz else sent_at.replace(tzinfo=None)

    conn_a = await websockets.connect(f"ws://127.0.0.1:{ws_a}")
    other = await websockets.connect(f"ws://127.0.0.1:{ws_a}")
    await _gw_register(conn_a, browser_did)
    await _gw_register(other, other_did)
    await _drain(conn_a)
    await _drain(other)

    monkeypatch.setattr(gdm, "datetime", _HourAgo)
    try:
        await conn_a.send(json.dumps({
            "cmd": "local_dm", "target_webid": did_b,
            "target_gateway_url": f"http://127.0.0.1:{http_b}",
            "content": "sent while you were down", "thread_id": "t-outage",
        }))
        pending, _ = await _recv_until(conn_a, lambda m: m.get("type") == "relay_pending")
    finally:
        monkeypatch.setattr(gdm, "datetime", datetime)
    assert pending is not None, "the DM should have been queued (peer down)"
    rows = gw_a._store.get_pending_relays()
    assert [r["id"] for r in rows] == [pending["message_id"]]
    return types.SimpleNamespace(
        gw_a=gw_a, gw_b=gw_b, h_a=h_a, ws_b=ws_b, http_b=http_b, http_a=http_a,
        did_a=did_a, did_b=did_b, conn_a=conn_a, other=other,
        message_id=pending["message_id"], sent_at=sent_at)


@pytest.mark.asyncio
@pytest.mark.parametrize("sealed", [False, True])
async def test_dm_queued_during_long_outage_is_delivered_and_sender_told(tmp_path, monkeypatch, sealed):
    s = await _queue_dm_while_peer_down(tmp_path, monkeypatch, sealed=sealed)
    try:
        start_gateway(s.gw_b, s.ws_b, s.http_b)  # B comes back an hour later
        async with websockets.connect(f"ws://127.0.0.1:{s.ws_b}") as conn_b:
            await _gw_register(conn_b, s.did_b)
            await _drain(conn_b)

            await _run_on(s.h_a, s.gw_a._relay_retry_pass())

            got, seen = await _recv_until(
                conn_b, lambda m: m.get("type") == "message"
                and m.get("content") == "sent while you were down")
            assert got is not None, f"B never got the queued DM: {seen}"
            assert got["message_id"] == s.message_id
            assert datetime.fromisoformat(got["timestamp"]) == s.sent_at  # original time kept

        # F11: the sender (and only the sender) sees it delivered.
        ok, seen = await _recv_until(s.conn_a, lambda m: m.get("type") == "relay_delivered")
        assert ok is not None and ok["message_id"] == s.message_id, seen
        stray, _ = await _recv_until(s.other, lambda m: m.get("type", "").startswith("relay_"),
                                     timeout=0.8)
        assert stray is None, "another account on the gateway was told about this relay"
        assert s.gw_a._store.get_pending_relays() == []
    finally:
        await s.conn_a.close()
        await s.other.close()


@pytest.mark.asyncio
async def test_queue_flushes_when_peer_makes_contact(tmp_path, monkeypatch):
    """When the peer gateway relays something to us, its queue goes right away
    instead of after the row's backoff (up to an hour)."""
    s = await _queue_dm_while_peer_down(tmp_path, monkeypatch, sealed=True)
    try:
        with s.gw_a._store._conn() as c:  # just retried: backoff says wait
            c.execute("UPDATE pending_relays SET attempt_count = 6, last_attempt_at = ?",
                      (time.time(),))
        start_gateway(s.gw_b, s.ws_b, s.http_b)
        async with websockets.connect(f"ws://127.0.0.1:{s.ws_b}") as conn_b:
            await _gw_register(conn_b, s.did_b)
            await _drain(conn_b)
            # B's user writes to A: a verified inbound relay from B's gateway.
            await conn_b.send(json.dumps({
                "cmd": "local_dm", "target_webid": s.did_a,
                "target_gateway_url": f"http://127.0.0.1:{s.http_a}",
                "content": "I'm back", "thread_id": "t-back",
            }))
            got, seen = await _recv_until(
                conn_b, lambda m: m.get("type") == "message"
                and m.get("content") == "sent while you were down")
            assert got is not None, f"queue was not flushed on contact: {seen}"
    finally:
        await s.conn_a.close()
        await s.other.close()


# ── D7: state-changing secondary ops are queued, not dropped ────────────────

@pytest.mark.asyncio
async def test_dm_edit_to_down_peer_is_queued_and_delivered_later(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    agent_a, agent_b = AgentState.generate(), AgentState.generate()
    ws_a, http_a, ws_b, http_b = free_port(), free_port(), free_port(), free_port()
    gw_a = _make_gw(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b = _make_gw(agent_b, ws_b, http_b, str(tmp_path / "b.db"))
    h_a = start_gateway(gw_a, ws_a, http_a)
    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)
    peer_url = f"http://127.0.0.1:{http_b}"

    await _run_on(h_a, gw_a._relay_ephemeral(peer_url, {
        "content_type": "dm_edit", "from_webid": did_a, "to_webid": did_b,
        "message_id": "msg-1", "new_content": "fixed typo"}))
    await _run_on(h_a, gw_a._relay_ephemeral(peer_url, {
        "content_type": "typing", "from_webid": did_a, "to_webid": did_b}))
    rows = gw_a._store.get_pending_relays()
    assert [r["id"].split(":")[0] for r in rows] == ["dm_edit"], "only the edit is durable"

    # Make the queued envelope stale, as after a long outage.
    with gw_a._store._conn() as c:
        p = json.loads(rows[0]["payload_json"])
        p["relay_ts"] = _iso(120)
        c.execute("UPDATE pending_relays SET payload_json = ?", (json.dumps(p),))

    edits = []

    async def _fake_edit(data):
        edits.append(data)
        return "200 OK", '{"status":"delivered"}'
    gw_b._handle_dm_edit_relay = _fake_edit
    start_gateway(gw_b, ws_b, http_b)
    await _run_on(h_a, gw_a._relay_retry_pass())
    assert [e["new_content"] for e in edits] == ["fixed typo"], "edit did not verify on the peer"
    assert gw_a._store.get_pending_relays() == []


# ── D5: background loops survive transient errors ───────────────────────────

def _fast_sleep_shim(monkeypatch, module):
    real_sleep = asyncio.sleep
    shim = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
    shim.sleep = lambda s: real_sleep(0)
    monkeypatch.setattr(module, "asyncio", shim)
    return real_sleep


@pytest.mark.asyncio
async def test_relay_retry_loop_survives_a_locked_database(tmp_path, monkeypatch):
    import proxion_messenger_core._gateway_pod as gp
    from proxion_messenger_core.persist import AgentState
    gw = _make_gw(AgentState.generate(), free_port(), free_port(), str(tmp_path / "x.db"))
    real_sleep = _fast_sleep_shim(monkeypatch, gp)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(gw._store, "get_pending_relays", boom)
    t = asyncio.create_task(gw._relay_retry_loop())
    await real_sleep(0.2)
    try:
        assert not t.done(), f"retry loop died: {t.exception() if t.done() else ''}"
        assert len(calls) > 1, "the loop should keep retrying"
    finally:
        t.cancel()


@pytest.mark.asyncio
async def test_flush_loop_survives_an_outbox_error(tmp_path, monkeypatch):
    import proxion_messenger_core._gateway_pod as gp
    from proxion_messenger_core.persist import AgentState
    gw = _make_gw(AgentState.generate(), free_port(), free_port(), str(tmp_path / "x.db"))
    real_sleep = _fast_sleep_shim(monkeypatch, gp)
    calls = []

    def boom():
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(gw.outbox, "get_items", boom)
    t = asyncio.create_task(gw.flush_loop())
    await real_sleep(0.2)
    try:
        assert not t.done(), f"flush loop died: {t.exception() if t.done() else ''}"
        assert len(calls) > 1
    finally:
        t.cancel()


@pytest.mark.asyncio
async def test_supervised_background_task_restarts_instead_of_raising(tmp_path, monkeypatch):
    import proxion_messenger_core.gateway as gmod
    from proxion_messenger_core.persist import AgentState
    gw = _make_gw(AgentState.generate(), free_port(), free_port(), str(tmp_path / "x.db"))
    _fast_sleep_shim(monkeypatch, gmod)
    runs = []

    async def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise sqlite3.OperationalError("database is locked")

    await asyncio.wait_for(gw._supervised(flaky), timeout=5)
    assert len(runs) == 3
