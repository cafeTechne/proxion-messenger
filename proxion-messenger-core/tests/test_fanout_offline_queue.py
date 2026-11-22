"""Fanout envelopes to OFFLINE devices are queued + pushed, not silently lost.

dm_device_deliveries recorded attempts but nothing ever redelivered: an offline
device's envelope was dropped (fanout has no server-side history to recover
from), and no web push fired. Worse, per-ACCOUNT delivery hid the case where a
sibling device was online — the offline device's envelope went to sockets that
discard it. Now: delivery is device-addressed; a missing device's envelope is
queued (drained on its next register, mirroring _relay_queue) and the account
is web-pushed when nothing at all is online.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.device_cert import issue_device_cert
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _events(ws, type_):
    return [json.loads(c[0][0]) for c in ws.send.call_args_list
            if json.loads(c[0][0]).get("type") == type_]


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "foq.db")),
    )


async def _register(gw, ws, did, cert=None):
    gw.clients.add(ws)
    p = {"cmd": "register", "did": did, "display_name": "D"}
    if cert is not None:
        p["delegation_cert"] = cert
    await gw.process_command(ws, p)


async def _send_fanout(gw, ws_sender, message_id, to_webid, to_device_id, content):
    await gw.process_command(ws_sender, {
        "cmd": "send_dm_fanout", "message_id": message_id,
        "fanout": [{"to_webid": to_webid, "to_device_id": to_device_id,
                    "payload": {"content": content, "e2e": True}}],
    })


@pytest.mark.asyncio
async def test_offline_account_envelope_queued_pushed_and_drained(gateway, noauth_env, monkeypatch):
    """All of the recipient's devices offline: queue + push, then deliver on register."""
    sends = []
    import proxion_messenger_core.webpush as webpush
    monkeypatch.setattr(webpush, "send_web_push", lambda **kw: sends.append(kw))
    gateway._vapid_private_pem = "pem"
    gateway._vapid_subject = "mailto:x@y"

    recipient = _did(Ed25519PrivateKey.generate())
    gateway._store.save_push_subscription("sub-1", recipient, "https://push/r", "p", "a")

    sender = Ed25519PrivateKey.generate()
    ws_s = _mock_ws()
    await _register(gateway, ws_s, _did(sender))
    await _send_fanout(gateway, ws_s, "m-off-1", recipient, recipient, "CIPHER")

    assert gateway._store.pending_device_fanout(recipient, recipient), "envelope must be queued for the offline device"
    assert sends, "the offline account must be web-pushed"

    # Recipient's device comes back — the queued envelope is delivered.
    ws_r = _mock_ws()
    await _register(gateway, ws_r, recipient)
    evs = _events(ws_r, "dm_fanout")
    assert evs and evs[0]["message_id"] == "m-off-1"
    assert evs[0]["payload"]["content"] == "CIPHER"
    assert not gateway._store.pending_device_fanout(recipient, recipient), "queue must be drained"


@pytest.mark.asyncio
async def test_offline_sibling_device_queued_while_primary_online(gateway, noauth_env, monkeypatch):
    """Primary online, delegated device offline: the primary gets its envelope
    live; the offline device's envelope is queued (NOT lost to the primary's
    socket) and NOT pushed (someone is online); drained when the device returns."""
    sends = []
    import proxion_messenger_core.webpush as webpush
    monkeypatch.setattr(webpush, "send_web_push", lambda **kw: sends.append(kw))
    gateway._vapid_private_pem = "pem"
    gateway._vapid_subject = "mailto:x@y"

    account = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    cert = issue_device_cert(account, device_did)
    gateway._store.save_push_subscription("sub-2", account_did, "https://push/a", "p", "a")

    ws_primary = _mock_ws()
    await _register(gateway, ws_primary, account_did)   # delegated device NOT connected

    sender = Ed25519PrivateKey.generate()
    ws_s = _mock_ws()
    await _register(gateway, ws_s, _did(sender))
    ws_primary.send.reset_mock()
    await gateway.process_command(ws_s, {
        "cmd": "send_dm_fanout", "message_id": "m-sib-1",
        "fanout": [
            {"to_webid": account_did, "to_device_id": account_did, "payload": {"content": "P"}},
            {"to_webid": account_did, "to_device_id": device_did, "payload": {"content": "D"}},
        ],
    })

    prim = _events(ws_primary, "dm_fanout")
    assert {e["to_device_id"] for e in prim} == {account_did}, "primary gets only its envelope"
    assert gateway._store.pending_device_fanout(account_did, device_did), "offline device's envelope must be queued"
    assert not sends, "no push while a device of the account is online"

    ws_dev = _mock_ws()
    await _register(gateway, ws_dev, device_did, cert=cert)
    evs = _events(ws_dev, "dm_fanout")
    assert evs and evs[0]["payload"]["content"] == "D"
    assert evs[0]["to_device_id"] == device_did


@pytest.mark.asyncio
async def test_relayed_fanout_to_offline_device_queued(gateway, monkeypatch):
    """Receive side: a relayed-in envelope for an offline device queues instead
    of vanishing behind the 200 the sending gateway takes as delivered."""
    from proxion_messenger_core.relay import sign_relay_message
    from datetime import datetime, timezone
    import secrets

    sender_gw = Ed25519PrivateKey.generate()
    from_did = _did(sender_gw)
    to_webid = "did:key:zOfflineAccount"
    inner = json.dumps({"message_id": "m-rel-1", "to_device_id": "did:key:zDev",
                        "payload": {"content": "C"}}, sort_keys=True, separators=(",", ":"))
    ts = datetime.now(timezone.utc).isoformat()
    nonce = secrets.token_hex(8)
    sig = sign_relay_message(sender_gw, from_did, to_webid, "m-rel-1:did:key:zDev",
                             inner, ts, nonce, message_scope="dm-fanout")
    status, _ = await gateway._handle_relay_post(json.dumps({
        "content_type": "dm_fanout", "from_webid": from_did, "to_webid": to_webid,
        "message_id": "m-rel-1:did:key:zDev", "content": inner, "timestamp": ts,
        "relay_nonce": nonce, "signature": sig, "message_scope": "dm-fanout",
    }).encode())
    assert status.startswith("200")
    assert gateway._store.pending_device_fanout(to_webid, "did:key:zDev")


@pytest.mark.asyncio
async def test_offline_push_respects_mute(gateway, noauth_env, monkeypatch):
    sends = []
    import proxion_messenger_core.webpush as webpush
    monkeypatch.setattr(webpush, "send_web_push", lambda **kw: sends.append(kw))
    gateway._vapid_private_pem = "pem"
    gateway._vapid_subject = "mailto:x@y"

    recipient = _did(Ed25519PrivateKey.generate())
    sender = Ed25519PrivateKey.generate()
    sender_did = _did(sender)
    gateway._store.save_push_subscription("sub-3", recipient, "https://push/r", "p", "a")
    gateway._store.set_thread_mute(recipient, sender_did, True)

    ws_s = _mock_ws()
    await _register(gateway, ws_s, sender_did)
    await _send_fanout(gateway, ws_s, "m-mute-1", recipient, recipient, "X")
    assert not sends, "muted sender must not trigger offline push"
    assert gateway._store.pending_device_fanout(recipient, recipient), "but the envelope still queues"


@pytest.mark.asyncio
async def test_full_relay_queue_refuses_without_spending_replay_id(gateway):
    from proxion_messenger_core.relay import sign_relay_message
    from datetime import datetime, timezone
    import secrets

    sender = Ed25519PrivateKey.generate()
    from_did, recipient, device = _did(sender), "did:key:zOfflineAccount", "did:key:zDev"
    for index in range(100):
        assert gateway._queue_fanout_envelope(str(index), from_did, recipient, device, {"content": str(index)})
    content = json.dumps({"message_id": "retry-after-cap", "to_device_id": device, "payload": {"content": "C"}})
    transport_id, timestamp, nonce = "retry-after-cap:dev", datetime.now(timezone.utc).isoformat(), secrets.token_hex(8)
    signature = sign_relay_message(sender, from_did, recipient, transport_id, content, timestamp, nonce, message_scope="dm-fanout")
    data = {"content_type": "dm_fanout", "from_webid": from_did, "to_webid": recipient, "message_id": transport_id,
        "content": content, "timestamp": timestamp, "relay_nonce": nonce, "signature": signature, "message_scope": "dm-fanout"}
    status, _ = await gateway._handle_relay_post(json.dumps(data).encode())
    assert status.startswith("503")
    import hashlib
    replay_key = f"{from_did}:{transport_id}:" + hashlib.sha256(content.encode()).hexdigest()
    assert not gateway._store.has_seen_relay_id(replay_key)
    gateway._store.remove_device_fanout(recipient, device, gateway._store.pending_device_fanout(recipient, device)[0]["queue_id"])
    status, _ = await gateway._handle_relay_post(json.dumps(data).encode())
    assert status.startswith("200")
    assert gateway._store.has_seen_relay_id(replay_key)
    assert len(gateway._store.pending_device_fanout(recipient, device)) == 100
    # A new signature/nonce cannot turn a conflicting envelope into an accepted
    # duplicate merely by reusing the earlier transport id.
    changed = json.dumps({"message_id": "retry-after-cap", "to_device_id": device, "payload": {"content": "changed"}})
    new_nonce = secrets.token_hex(8)
    conflicting = {**data, "content": changed, "relay_nonce": new_nonce,
        "signature": sign_relay_message(sender, from_did, recipient, transport_id, changed, timestamp, new_nonce, message_scope="dm-fanout")}
    status, _ = await gateway._handle_relay_post(json.dumps(conflicting).encode())
    assert status.startswith("503")
    assert [r["payload"] for r in gateway._store.pending_device_fanout(recipient, device)
            if r["message_id"] == "retry-after-cap"] == [{"content": "C"}]


def _restart(gateway):
    """Reopen the same database with the same gateway identity; no RAM queue."""
    gateway._store.close()
    return ProxionGateway(agent=gateway.agent, dm_clients={}, room_memberships={}, config=gateway.config)


@pytest.mark.asyncio
async def test_acknowledged_offline_message_survives_gateway_restart(gateway, noauth_env):
    sender, recipient = _did(Ed25519PrivateKey.generate()), _did(Ed25519PrivateKey.generate())
    ws_s = _mock_ws()
    await _register(gateway, ws_s, sender)
    await _send_fanout(gateway, ws_s, "durable", recipient, recipient, "EXACT-CIPHER")
    assert _events(ws_s, "send_dm_fanout_ack")[-1]["delivered"]
    reopened = _restart(gateway)
    try:
        ws_r = _mock_ws()
        await _register(reopened, ws_r, recipient)
        assert _events(ws_r, "dm_fanout")[0]["payload"]["content"] == "EXACT-CIPHER"
        assert reopened._store.pending_device_fanout(recipient, recipient) == []
    finally:
        reopened._store.close()


@pytest.mark.asyncio
async def test_sqlite_acceptance_failure_never_acknowledges(gateway, noauth_env):
    ws_s = _mock_ws()
    await _register(gateway, ws_s, _did(Ed25519PrivateKey.generate()))
    with gateway._store._conn() as conn:
        conn.execute("CREATE TRIGGER queue_failure BEFORE INSERT ON pending_device_fanout BEGIN SELECT RAISE(ABORT,'disk full'); END")
    recipient = _did(Ed25519PrivateKey.generate())
    await _send_fanout(gateway, ws_s, "uncommitted", recipient, recipient, "CIPHER")
    assert _events(ws_s, "send_dm_fanout_ack")[-1]["delivered"] == []
    assert gateway._store.pending_device_fanout(recipient, recipient) == []


@pytest.mark.asyncio
async def test_crash_after_commit_before_ack_is_exactly_retryable(gateway, noauth_env):
    sender, recipient = _did(Ed25519PrivateKey.generate()), _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, sender)
    ws.send.side_effect = ConnectionError("lost ack")
    data = {"message_id": "lost-ack", "fanout": [{"to_webid": recipient,
        "to_device_id": recipient, "payload": {"content": "CIPHER"}}]}
    with pytest.raises(ConnectionError):
        await gateway._handle_send_dm_fanout(ws, data)
    reopened = _restart(gateway)
    try:
        ws_retry = _mock_ws()
        await _register(reopened, ws_retry, sender)
        await reopened._handle_send_dm_fanout(ws_retry, data)
        assert len(reopened._store.pending_device_fanout(recipient, recipient)) == 1
        assert _events(ws_retry, "send_dm_fanout_ack")[-1]["delivered"]
    finally:
        reopened._store.close()


@pytest.mark.asyncio
async def test_crash_after_socket_write_replays_same_envelope_until_removal(gateway, noauth_env, monkeypatch):
    recipient = _did(Ed25519PrivateKey.generate())
    assert gateway._queue_fanout_envelope("write-before-remove", "sender", recipient, recipient, {"content": "CIPHER"})
    monkeypatch.setattr(gateway._store, "remove_device_fanout", lambda *args: (_ for _ in ()).throw(OSError("crash")))
    first = _mock_ws()
    await _register(gateway, first, recipient)
    assert len(gateway._store.pending_device_fanout(recipient, recipient)) == 1
    first_envelope = _events(first, "dm_fanout")[0]
    reopened = _restart(gateway)
    try:
        second = _mock_ws()
        await _register(reopened, second, recipient)
        assert _events(second, "dm_fanout")[0] == first_envelope
        assert reopened._store.pending_device_fanout(recipient, recipient) == []
    finally:
        reopened._store.close()


@pytest.mark.asyncio
async def test_account_cleanup_during_queue_read_does_not_deliver_stale_rows(gateway, noauth_env, monkeypatch):
    recipient = _did(Ed25519PrivateKey.generate())
    assert gateway._queue_fanout_envelope("deleted", "sender", recipient, recipient, {"content": "CIPHER"})
    read = gateway._store.pending_device_fanout

    def read_then_delete(owner, device):
        rows = read(owner, device)
        gateway._store.delete_account_data("sender")
        return rows

    monkeypatch.setattr(gateway._store, "pending_device_fanout", read_then_delete)
    receiver = _mock_ws()
    await _register(gateway, receiver, recipient)
    assert _events(receiver, "dm_fanout") == []


def test_no_persistent_store_cannot_acknowledge_offline_acceptance(gateway):
    original = gateway._store
    try:
        gateway._store = None
        assert not gateway._queue_fanout_envelope("m", "s", "o", "d", {"content": "C"})
    finally:
        gateway._store = original


@pytest.mark.asyncio
async def test_gateway_alias_queue_drains_only_under_recorded_device_owner(gateway, noauth_env):
    owner, device = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    owner_id, device_id = _did(owner), _did(device)
    cert = issue_device_cert(owner, device_id)
    assert gateway._store.register_device(device_id, owner_id, "pub", "attestation")
    assert gateway._queue_fanout_envelope("aliased", "sender", gateway._own_gateway_did(), device_id, {"content": "CIPHER"})
    assert gateway._store.pending_device_fanout(gateway._own_gateway_did(), device_id) == []
    assert gateway._store.pending_device_fanout(owner_id, device_id)
    stranger = _mock_ws()
    await _register(gateway, stranger, _did(Ed25519PrivateKey.generate()))
    assert _events(stranger, "dm_fanout") == []
    receiver = _mock_ws()
    await _register(gateway, receiver, device_id, cert=cert)
    assert _events(receiver, "dm_fanout")[0]["message_id"] == "aliased"
    assert gateway._store.pending_device_fanout(owner_id, device_id) == []


def test_unknown_gateway_alias_owner_is_not_accepted(gateway, monkeypatch):
    monkeypatch.setattr(gateway._store, "get_relationship_owner", lambda _: None)
    assert not gateway._queue_fanout_envelope("unknown", "sender", gateway._own_gateway_did(), "unknown-device", {"content": "C"})
