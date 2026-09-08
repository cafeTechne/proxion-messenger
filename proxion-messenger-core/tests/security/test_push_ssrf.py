"""SSRF and abuse hardening for the WebPush subscription path.

The push `endpoint` is client-supplied and later POSTed server-side, so it is
validated at registration, at send time, and on pod-restore with the same SSRF
check the relay path uses. Subscriptions are also capped per owner and can only
be removed by their owner.
"""
from __future__ import annotations

import json
import time

import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.webpush import is_safe_push_endpoint, send_web_push
from proxion_messenger_core._store.devices import MAX_PUSH_SUBSCRIPTIONS_PER_OWNER

WEBID = "https://alice.pod/profile/card#me"
OTHER = "https://mallory.pod/profile/card#me"
_PUBLIC = [(None, None, None, None, ("93.184.216.34", 0))]


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "push.db"))


@pytest.fixture
def gw(tmp_path):
    a = AgentState.generate()
    a.webid = "https://gw.pod/profile/card#me"
    g = ProxionGateway(agent=a, dm_clients={}, room_memberships={},
                       config=GatewayConfig(db_path=str(tmp_path / "gw.db")))
    g._vapid_private_pem = "pem"
    g._vapid_subject = "mailto:admin@example.com"
    return g


def _ws():
    ws = MagicMock()
    sent = []
    async def _send(m):
        sent.append(m)
    ws.send = _send
    ws._sent = sent
    return ws


# ── endpoint validator ─────────────────────────────────────────────────────

class TestIsSafePushEndpoint:
    def test_metadata_ip_rejected(self):
        assert is_safe_push_endpoint("https://169.254.169.254/latest/meta-data/") is False

    def test_loopback_rejected(self):
        assert is_safe_push_endpoint("https://127.0.0.1/push") is False

    def test_ipv6_loopback_rejected(self):
        assert is_safe_push_endpoint("https://[::1]/push") is False

    def test_private_rejected(self):
        assert is_safe_push_endpoint("https://10.0.0.1/push") is False
        assert is_safe_push_endpoint("https://192.168.1.10/push") is False

    def test_http_scheme_rejected(self):
        # Even a public host must be https for a push endpoint.
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_PUBLIC):
            assert is_safe_push_endpoint("http://push.example.com/abc") is False

    def test_empty_rejected(self):
        assert is_safe_push_endpoint("") is False
        assert is_safe_push_endpoint(None) is False  # type: ignore[arg-type]

    def test_legitimate_https_allowed(self):
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_PUBLIC):
            assert is_safe_push_endpoint("https://fcm.googleapis.com/fcm/send/abc") is True

    def test_split_horizon_rejected(self):
        mixed = [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
        ]
        with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=mixed):
            assert is_safe_push_endpoint("https://tricky.example.com/push") is False


def test_send_web_push_skips_unsafe_endpoint():
    """send_web_push refuses an SSRF-unsafe endpoint before any network call."""
    assert send_web_push(
        subscription={"endpoint": "https://169.254.169.254/x", "keys": {"p256dh": "p", "auth": "a"}},
        payload={"type": "message"},
        vapid_private_pem="pem",
        vapid_subject="mailto:x@y",
    ) is False


# ── registration guard ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscribe_rejects_ssrf_endpoint(gw):
    ws = _ws()
    gw._client_webids[ws] = WEBID
    await gw._handle_subscribe_push(ws, {
        "endpoint": "http://169.254.169.254/latest/meta-data/",
        "p256dh_b64": "p", "auth_b64": "a",
    })
    assert json.loads(ws._sent[-1]) == {"type": "error", "message": "invalid_push_endpoint"}
    assert gw._store.get_push_subscriptions(WEBID) == []


@pytest.mark.asyncio
async def test_subscribe_accepts_legit_https_endpoint(gw):
    ws = _ws()
    gw._client_webids[ws] = WEBID
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_PUBLIC):
        await gw._handle_subscribe_push(ws, {
            "subscription_id": "sub-ok",
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "p256dh_b64": "p", "auth_b64": "a",
        })
    assert json.loads(ws._sent[-1])["type"] == "push_subscribed"
    subs = gw._store.get_push_subscriptions(WEBID)
    assert any(s["subscription_id"] == "sub-ok" for s in subs)


# ── per-owner cap ───────────────────────────────────────────────────────────

def test_per_owner_subscription_cap_evicts_oldest(store):
    n = MAX_PUSH_SUBSCRIPTIONS_PER_OWNER
    for i in range(n + 5):
        store.save_push_subscription(f"s{i:03d}", WEBID, f"https://push.example/{i}", "p", "a")
        time.sleep(0.001)  # keep created_at ordering distinct
    subs = store.get_push_subscriptions(WEBID)
    assert len(subs) == n
    ids = {s["subscription_id"] for s in subs}
    # Oldest evicted, newest kept.
    assert "s000" not in ids
    assert f"s{n + 4:03d}" in ids


def test_cap_is_scoped_per_owner(store):
    for i in range(MAX_PUSH_SUBSCRIPTIONS_PER_OWNER + 3):
        store.save_push_subscription(f"a{i:03d}", WEBID, f"https://push.example/{i}", "p", "a")
    store.save_push_subscription("b0", OTHER, "https://push.example/other", "p", "a")
    assert len(store.get_push_subscriptions(OTHER)) == 1


# ── unsubscribe owner-scope ─────────────────────────────────────────────────

def test_delete_scoped_to_owner_no_cross_delete(store):
    store.save_push_subscription("mine", WEBID, "https://push.example/m", "p", "a")
    # A caller who is not the owner cannot delete by guessing the id.
    store.delete_push_subscription("mine", OTHER)
    assert len(store.get_push_subscriptions(WEBID)) == 1
    # The real owner can.
    store.delete_push_subscription("mine", WEBID)
    assert store.get_push_subscriptions(WEBID) == []


@pytest.mark.asyncio
async def test_unsubscribe_handler_only_removes_own(gw):
    gw._store.save_push_subscription("victim", WEBID, "https://push.example/v", "p", "a")
    attacker = _ws()
    gw._client_webids[attacker] = OTHER
    await gw._handle_unsubscribe_push(attacker, {"subscription_id": "victim"})
    # Still there — the attacker does not own it.
    assert len(gw._store.get_push_subscriptions(WEBID)) == 1


# ── send-time skip + offload timeout ────────────────────────────────────────

def test_send_inbox_push_skips_unsafe_endpoint(gw):
    gw._store.save_push_subscription("s1", WEBID, "https://127.0.0.1/ep", "p", "a")
    import proxion_messenger_core.webpush as wp
    calls = []
    orig = wp.send_web_push
    wp.send_web_push = lambda **k: (calls.append(1) or True)
    try:
        assert gw._send_inbox_push(WEBID) is False
    finally:
        wp.send_web_push = orig
    assert calls == []  # never attempted the unsafe endpoint


@pytest.mark.asyncio
async def test_push_fanout_offloaded_does_not_block_loop(gw):
    """A slow push runs in an executor: the event loop keeps ticking while it does."""
    import asyncio
    import proxion_messenger_core.webpush as wp

    def _slow(**kw):
        time.sleep(0.3)
        return True

    orig = wp.send_web_push
    wp.send_web_push = _slow
    ticks = [0]

    async def _ticker():
        for _ in range(30):
            ticks[0] += 1
            await asyncio.sleep(0.01)

    subs = [{"endpoint": "https://push.example/ep", "p256dh_b64": "p", "auth_b64": "a"}]
    try:
        t = asyncio.create_task(_ticker())
        await gw._push_offline_fanout(subs, {"type": "message"}, "pem", "mailto:x@y")
        await t
    finally:
        wp.send_web_push = orig
    # The ticker advanced while the blocking send ran off-loop.
    assert ticks[0] >= 10
