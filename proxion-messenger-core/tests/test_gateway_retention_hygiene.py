"""Retention purge, small state leaks, and input hygiene on the gateway."""
import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gwharness import free_port, start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _gw(tmp_path, **kw):
    cfg = GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "gw.db"), **kw)
    return ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())


def _key_did():
    k = Ed25519PrivateKey.generate()
    return k, pub_key_to_did(k.public_key().public_bytes_raw())


def _ws():
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 1)
    return ws


def _sent(ws):
    return [json.loads(c.args[0]) for c in ws.send.call_args_list]


# ── retention ──────────────────────────────────────────────────────────────

def test_retention_purge_prunes_logs_and_calls_dormant_prunes(tmp_path):
    gw = _gw(tmp_path)
    st = gw._store
    old = time.time() - 400 * 86400
    db = sqlite3.connect(str(tmp_path / "gw.db"))
    db.execute("INSERT INTO webhook_delivery_logs (webhook_id, thread_id, status_code, success,"
               " latency_ms, created_at) VALUES ('w','t',200,1,1,?)", (old,))
    db.execute("INSERT INTO notification_fallback_events (pod_origin, reason_code, detail, created_at)"
               " VALUES ('p','r','d',?)", (old,))
    db.execute("INSERT INTO credential_anomalies (anomaly_type, identity, detail, created_at)"
               " VALUES ('a','i','d',?)", (old,))
    db.execute("INSERT INTO wg_connectivity_events (peer_webid, old_mode, new_mode, reason, created_at)"
               " VALUES ('p','a','b','r',?)", (old,))
    db.execute("INSERT INTO relay_delivery_chain (relay_id, peer_did, status, prev_hash, entry_hash,"
               " created_at) VALUES ('r-old','did:key:zP','delivered','','h',?)", (old,))
    db.execute("INSERT INTO dpop_seen_jti (jti, seen_at) VALUES ('j', ?)", (old,))
    db.execute("INSERT INTO scheduled_messages (id, thread_id, from_webid, content, send_at,"
               " created_at, cancelled) VALUES ('s-sent','t','f','secret',?,?,1)", (old, old))
    db.execute("INSERT INTO scheduled_messages (id, thread_id, from_webid, content, send_at,"
               " created_at, cancelled) VALUES ('s-pending','t','f','later',?,?,0)",
               (time.time() + 3600, time.time()))
    db.commit()
    st.append_relay_delivery_event("r-new", "did:key:zP", "accepted")

    gw._run_retention_purge()

    def n(table, where="1=1"):
        return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    assert n("webhook_delivery_logs") == 0
    assert n("notification_fallback_events") == 0
    assert n("credential_anomalies") == 0
    assert n("wg_connectivity_events") == 0
    assert n("dpop_seen_jti") == 0
    assert n("relay_delivery_chain", "relay_id='r-old'") == 0
    assert n("relay_delivery_chain", "relay_id='r-new'") == 1
    # The remaining chain still verifies after its head was pruned.
    assert st.verify_relay_delivery_chain("did:key:zP")["valid"] is True
    # Sent scheduled messages (with their plaintext) go; pending ones stay.
    assert n("scheduled_messages", "id='s-sent'") == 0
    assert n("scheduled_messages", "id='s-pending'") == 1


@pytest.mark.asyncio
async def test_retention_loop_runs_soon_after_start(tmp_path):
    gw = _gw(tmp_path)
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    with patch.object(gw, "_run_retention_purge") as run, \
            patch("proxion_messenger_core.gateway.asyncio.sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await gw._retention_purge_loop()
    assert sleeps[0] <= 600, "first purge should not wait a full day"
    assert run.call_count == 1


def test_relay_delivery_chain_keeps_every_status(tmp_path):
    st = _gw(tmp_path)._store
    st.append_relay_delivery_event("r1", "did:key:zP", "accepted")
    st.append_relay_delivery_event("r1", "did:key:zP", "delivered")
    db = sqlite3.connect(str(tmp_path / "gw.db"))
    rows = [r[0] for r in db.execute(
        "SELECT status FROM relay_delivery_chain WHERE relay_id='r1' ORDER BY created_at")]
    assert rows == ["accepted", "delivered"]
    assert st.verify_relay_delivery_chain("did:key:zP")["valid"] is True


def test_failed_migration_blocks_writes(tmp_path, monkeypatch):
    from proxion_messenger_core.local_store import LocalStore
    path = str(tmp_path / "m.db")
    LocalStore(path)
    db = sqlite3.connect(path)
    # Migration 61 copies dm_threads.owner_webid; a table without it fails.
    db.execute("UPDATE schema_version SET version = 60")
    db.execute("DROP TABLE dm_threads")
    db.execute("CREATE TABLE dm_threads (thread_id TEXT PRIMARY KEY, peer_webid TEXT,"
               " created_at REAL)")
    db.commit()
    db.close()
    st = LocalStore(path)
    assert st._integrity_ok is False
    assert getattr(st, "_migration_error", "")


# ── state leaks ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disconnect_drops_session_signing_did(tmp_path):
    import websockets
    ws_port, http_port = free_port(), free_port()
    gw = _gw(tmp_path, port=ws_port, http_port=http_port)
    start_gateway(gw, ws_port, http_port)
    _, did = _key_did()
    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as c:
        await c.send(json.dumps({"cmd": "register", "did": did, "display_name": "x"}))
        for _ in range(20):
            if did in gw._session_signing_did.values():
                break
            await asyncio.sleep(0.05)
        assert did in gw._session_signing_did.values()
    for _ in range(40):
        if not gw._session_signing_did:
            break
        await asyncio.sleep(0.05)
    assert gw._session_signing_did == {}
    assert not gw._relay_identity_is_local(did)


@pytest.mark.asyncio
async def test_schedule_message_keeps_no_in_memory_copy(tmp_path):
    gw = _gw(tmp_path)
    ws = _ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": _key_did()[1], "display_name": "a"})
    await gw.process_command(ws, {"cmd": "chat_room_create", "name": "r"})
    room_id = next(iter(gw._local_rooms))
    await gw.process_command(ws, {"cmd": "schedule_message", "thread_id": room_id,
                                  "content": "later",
                                  "send_at": datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()})
    assert any(e.get("type") == "message_scheduled" for e in _sent(ws)), _sent(ws)
    assert not getattr(gw, "_scheduled_messages", [])


@pytest.mark.asyncio
async def test_room_slow_mode_counters_are_pruned(tmp_path):
    from collections import deque
    gw = _gw(tmp_path)
    old = datetime.now(timezone.utc).timestamp() - 10 * 86400
    for i in range(2000):
        gw._rate_counters[(f"did:key:z{i}", "room")] = deque([old])
    gw._prune_room_rate_counters(datetime.now(timezone.utc).timestamp())
    assert len(gw._rate_counters) < 100


# ── input hygiene ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "1e5", "9" * 5000, 2 ** 70],
                         ids=["word", "float-str", "huge-str", "huge-int"])
async def test_bad_seq_num_is_a_schema_error_not_internal(tmp_path, bad):
    gw = _gw(tmp_path)
    ws = _ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": _key_did()[1], "display_name": "a"})
    await gw.process_command(ws, {"cmd": "chat_room_create", "name": "r"})
    room_id = next(iter(gw._local_rooms))
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "send_room", "room_id": room_id,
                                  "content": "hi", "seq_num": bad})
    codes = [e.get("code") for e in _sent(ws) if e.get("type") == "error"]
    assert "E_INTERNAL" not in codes
    assert "E_SCHEMA" in codes


@pytest.mark.asyncio
async def test_digit_string_seq_num_still_accepted(tmp_path):
    gw = _gw(tmp_path)
    ws = _ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": _key_did()[1], "display_name": "a"})
    await gw.process_command(ws, {"cmd": "chat_room_create", "name": "r"})
    room_id = next(iter(gw._local_rooms))
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "send_room", "room_id": room_id,
                                  "content": "hi", "seq_num": "7"})
    assert not [e for e in _sent(ws) if e.get("type") == "error"], _sent(ws)


def _signed_relay(priv, frm, to, ts, msg_id="m1"):
    from proxion_messenger_core.relay import sign_relay_message
    return json.dumps({
        "from_webid": frm, "to_webid": to, "message_id": msg_id, "content": "hi",
        "timestamp": ts, "signature": sign_relay_message(priv, frm, to, msg_id, "hi", ts),
    }).encode()


@pytest.mark.asyncio
async def test_offline_relay_dm_is_stored_under_the_sender_thread(tmp_path):
    gw = _gw(tmp_path)
    priv, sender = _key_did()
    _, receiver = _key_did()
    ts = datetime.now(timezone.utc).isoformat()
    status, _ = await gw._handle_relay_post(_signed_relay(priv, sender, receiver, ts))
    assert status.startswith("202")
    # The same thread a live delivery (and the queued drain) uses.
    assert [m["message_id"] for m in gw._store.get_messages(sender)] == ["m1"]
    assert gw._store.get_messages(receiver) == []


@pytest.mark.asyncio
async def test_relay_dm_timestamp_is_stored_in_utc(tmp_path):
    gw = _gw(tmp_path)
    priv, sender = _key_did()
    _, receiver = _key_did()
    ts = "2026-10-19T12:00:00+05:00"
    with patch("proxion_messenger_core.relay.verify_relay_message", return_value=True):
        await gw._handle_relay_post(_signed_relay(priv, sender, receiver, ts))
    [m] = gw._store.get_messages(sender)
    assert m["timestamp"] == "2026-10-19T07:00:00+00:00"


@pytest.mark.asyncio
async def test_room_relay_timestamp_is_normalized(tmp_path):
    gw = _gw(tmp_path)
    _, owner = _key_did()
    _, peer = _key_did()
    gw._local_rooms["r1"] = {"name": "r", "code": "c", "members": set(), "invite_url": "",
                             "history_mode": "all", "messages": [], "creator_webid": owner}
    gw._store.save_room("r1", "r", "c", "", "all", owner)
    gw._store.add_room_member("r1", owner)
    gw._store.add_federated_room_member("r1", peer, "https://peer.example")
    for mid, ts in (("a1", "2026-10-19T12:00:00+05:00"), ("a2", "not a time"),
                    ("a3", "2026-10-19T12:00:00")):
        await gw._handle_room_relay({"room_id": "r1", "from_webid": peer, "message_id": mid,
                                     "content": "x", "timestamp": ts})
    got = {m["message_id"]: m["timestamp"] for m in gw._store.get_messages("r1")}
    assert got["a1"] == "2026-10-19T07:00:00+00:00"
    assert got["a3"] == "2026-10-19T12:00:00+00:00"
    assert datetime.fromisoformat(got["a2"]).tzinfo is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", 2 ** 70, [1]], ids=["word", "huge", "list"])
async def test_relay_dm_with_a_bad_seq_num_still_stores(tmp_path, bad):
    gw = _gw(tmp_path)
    priv, sender = _key_did()
    _, receiver = _key_did()
    body = json.loads(_signed_relay(priv, sender, receiver, datetime.now(timezone.utc).isoformat()))
    body["seq_num"] = bad   # not covered by the relay signature
    status, _ = await gw._handle_relay_post(json.dumps(body).encode())
    assert status.startswith("202")
    assert [m["message_id"] for m in gw._store.get_messages(sender)] == ["m1"]
