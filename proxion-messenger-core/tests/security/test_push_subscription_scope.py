"""Push subscription ids are per account, on the gateway and on the pod.

The local row for another account's id was already refused, but the handler still
synced the pod copy (push/<id>.json) and so overwrote the victim's record; and an
unsubscribe for someone else's id deleted their pod copy even though the scoped
local delete matched nothing.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

_PUBLIC = [(2, 1, 6, "", ("142.250.0.1", 443))]
EP = "https://fcm.googleapis.com/fcm/send/abc"


def _gw(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "s.db")))
    gw._sync_push_subscription_to_pod = AsyncMock()
    gw._delete_push_subscription_from_pod = AsyncMock()
    return gw


def _ws(gw, did):
    w = AsyncMock()
    w.__hash__ = lambda s: id(s)
    gw.clients.add(w)
    gw._client_webids[w] = did
    return w


def _last(ws):
    return json.loads(ws.send.call_args_list[-1].args[0])


@pytest.mark.asyncio
async def test_foreign_subscription_id_is_not_synced_to_pod(tmp_path):
    gw = _gw(tmp_path)
    gw._store.save_push_subscription("victim-sub", "did:key:zVictim", EP, "p", "a")
    attacker = _ws(gw, "did:key:zAttacker")
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_PUBLIC):
        await gw._handle_subscribe_push(attacker, {
            "subscription_id": "victim-sub", "endpoint": EP + "evil",
            "p256dh_b64": "p", "auth_b64": "a"})
    await asyncio.sleep(0)
    gw._sync_push_subscription_to_pod.assert_not_called()
    assert _last(attacker)["type"] == "error"
    assert gw._store.get_push_subscription_owner("victim-sub") == "did:key:zVictim"


@pytest.mark.asyncio
async def test_subscription_id_must_be_a_plain_token(tmp_path):
    gw = _gw(tmp_path)
    ws = _ws(gw, "did:key:zA")
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_PUBLIC):
        await gw._handle_subscribe_push(ws, {
            "subscription_id": "../inbox/x", "endpoint": EP,
            "p256dh_b64": "p", "auth_b64": "a"})
    await asyncio.sleep(0)
    gw._sync_push_subscription_to_pod.assert_not_called()
    assert _last(ws)["type"] == "error"


@pytest.mark.asyncio
async def test_own_subscription_still_syncs(tmp_path):
    gw = _gw(tmp_path)
    ws = _ws(gw, "did:key:zA")
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_PUBLIC):
        await gw._handle_subscribe_push(ws, {"endpoint": EP, "p256dh_b64": "p", "auth_b64": "a"})
    await asyncio.sleep(0)
    assert _last(ws)["type"] == "push_subscribed"
    gw._sync_push_subscription_to_pod.assert_called_once()


@pytest.mark.asyncio
async def test_unsubscribe_foreign_id_leaves_pod_copy(tmp_path):
    gw = _gw(tmp_path)
    gw._store.save_push_subscription("victim-sub", "did:key:zVictim", EP, "p", "a")
    attacker = _ws(gw, "did:key:zAttacker")
    await gw._handle_unsubscribe_push(attacker, {"subscription_id": "victim-sub"})
    await asyncio.sleep(0)
    gw._delete_push_subscription_from_pod.assert_not_called()
    assert gw._store.get_push_subscription_owner("victim-sub") == "did:key:zVictim"

    owner = _ws(gw, "did:key:zVictim")
    await gw._handle_unsubscribe_push(owner, {"subscription_id": "victim-sub"})
    await asyncio.sleep(0)
    gw._delete_push_subscription_from_pod.assert_called_once_with("victim-sub")
