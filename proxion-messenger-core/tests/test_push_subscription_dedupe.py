"""A browser re-subscribing on every connect keeps one push subscription row,
so each offline message is pushed to it once, and one owner's subscribe can
never overwrite another owner's row."""
from __future__ import annotations

import json

import pytest

EP = "https://fcm.googleapis.com/fcm/send/abc123"


def test_same_endpoint_replaces_the_older_row(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    s = LocalStore(str(tmp_path / "p.db"))
    assert s.save_push_subscription("old1", "did:key:zA", EP, "p", "a") == []
    assert s.save_push_subscription("new1", "did:key:zA", EP, "p2", "a2") == ["old1"]
    subs = s.get_push_subscriptions("did:key:zA")
    assert [x["subscription_id"] for x in subs] == ["new1"]


def test_an_id_owned_by_someone_else_is_not_overwritten(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    s = LocalStore(str(tmp_path / "p.db"))
    s.save_push_subscription("shared", "did:key:zA", EP, "p", "a")
    s.save_push_subscription("shared", "did:key:zEve", "https://evil.example/push", "p", "a")
    assert [x["endpoint"] for x in s.get_push_subscriptions("did:key:zA")] == [EP]
    assert s.get_push_subscriptions("did:key:zEve") == []


@pytest.mark.asyncio
async def test_resubscribing_on_reconnect_keeps_one_row(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    monkeypatch.setattr("proxion_messenger_core.webpush.is_safe_push_endpoint", lambda e: True)
    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))
    ids = []
    for _ in range(3):
        ws = AsyncMock()
        gw._client_webids[ws] = "did:key:zA"
        await gw._handle_subscribe_push(ws, {"endpoint": EP, "p256dh_b64": "p", "auth_b64": "a"})
        ids.append(json.loads(ws.send.call_args[0][0])["subscription_id"])
    assert len(set(ids)) == 1
    assert len(gw._store.get_push_subscriptions("did:key:zA")) == 1
