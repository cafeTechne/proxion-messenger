"""Linked devices share the account's display name, mutes and settings.

A delegated device registers AS the account (its delegation cert is verified),
so the gateway keeps one settings record per account identity and pushes every
change to the account's other live sessions. Nothing is ever read or written
for an identity other than the caller's own account.
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


def _did(priv: Ed25519PrivateKey) -> str:
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


@pytest.fixture(autouse=True)
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _gateway(db_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(db_path)),
    )


@pytest.fixture
def gateway(tmp_path):
    return _gateway(tmp_path / "sync.db")


async def _register(gw, ws, did, cert=None, name=None):
    gw.clients.add(ws)
    payload = {"cmd": "register", "did": did}
    if name:
        payload["display_name"] = name
    if cert is not None:
        payload["delegation_cert"] = cert
    await gw.process_command(ws, payload)


def _of_type(ws, type_):
    out = []
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        if msg.get("type") == type_:
            out.append(msg)
    return out


async def _account_with_device(gw):
    account, device = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    ws_p, ws_d = _mock_ws(), _mock_ws()
    await _register(gw, ws_p, account_did, name="Alice")
    await _register(gw, ws_d, device_did, cert=issue_device_cert(account, device_did))
    assert gw._client_webids[ws_d] == account_did
    return account_did, ws_p, ws_d


@pytest.mark.asyncio
async def test_new_device_reads_account_name_mutes_and_prefs(gateway):
    account_did, ws_p, ws_d = await _account_with_device(gateway)
    await gateway.process_command(ws_p, {"cmd": "set_thread_mute", "mute_key": "room-1",
                                         "thread_id": "room-1", "muted": True})
    await gateway.process_command(ws_p, {"cmd": "set_account_pref",
                                         "key": "proxion_theme", "value": "dark"})
    ws_d.send.reset_mock()
    await gateway.process_command(ws_d, {"cmd": "get_account_sync"})
    snap = _of_type(ws_d, "account_sync")[-1]
    assert snap["full"] is True
    assert snap["webid"] == account_did
    assert snap["display_name"] == "Alice"
    assert snap["muted_threads"] == ["room-1"]
    assert snap["prefs"] == {"proxion_theme": "dark"}


@pytest.mark.asyncio
async def test_changes_push_to_the_accounts_other_devices_only(gateway):
    account_did, ws_p, ws_d = await _account_with_device(gateway)
    ws_other = _mock_ws()
    await _register(gateway, ws_other, _did(Ed25519PrivateKey.generate()), name="Mallory")
    for ws in (ws_p, ws_d, ws_other):
        ws.send.reset_mock()

    await gateway.process_command(ws_p, {"cmd": "set_identity", "display_name": "Alice B"})
    await gateway.process_command(ws_p, {"cmd": "set_thread_mute", "mute_key": "did:key:zPeer",
                                         "thread_id": "dm-1", "muted": True})
    await gateway.process_command(ws_d, {"cmd": "set_account_pref",
                                         "key": "proxion_receipts_enabled", "value": "0"})

    to_device = _of_type(ws_d, "account_sync")
    assert {"display_name": "Alice B"}.items() <= to_device[0].items()
    assert to_device[1]["mutes"] == {"dm-1": True}
    to_primary = _of_type(ws_p, "account_sync")
    assert to_primary[-1]["prefs"] == {"proxion_receipts_enabled": "0"}
    # The sender is not echoed its own change, and another account hears nothing.
    assert not any("prefs" in m for m in to_device)
    assert _of_type(ws_other, "account_sync") == []
    # The device session's name follows the account rename.
    assert gateway._display_names[ws_d] == "Alice B"
    # Receipts pref also drives the gateway's receipt gate for the account.
    assert gateway._client_receipts_prefs[account_did] is False


@pytest.mark.asyncio
async def test_unmute_clears_the_synced_mute(gateway):
    _, ws_p, ws_d = await _account_with_device(gateway)
    await gateway.process_command(ws_p, {"cmd": "set_thread_mute", "mute_key": "room-1",
                                         "thread_id": "room-1", "muted": True})
    await gateway.process_command(ws_d, {"cmd": "set_thread_mute", "mute_key": "room-1",
                                         "thread_id": "room-1", "muted": False})
    assert _of_type(ws_p, "account_sync")[-1]["mutes"] == {"room-1": False}
    ws_p.send.reset_mock()
    await gateway.process_command(ws_p, {"cmd": "get_account_sync"})
    assert _of_type(ws_p, "account_sync")[-1]["muted_threads"] == []


@pytest.mark.asyncio
async def test_another_account_cannot_read_or_write_the_accounts_settings(gateway):
    account_did, ws_p, ws_d = await _account_with_device(gateway)
    await gateway.process_command(ws_p, {"cmd": "set_account_pref",
                                         "key": "proxion_theme", "value": "light"})
    ws_x = _mock_ws()
    await _register(gateway, ws_x, _did(Ed25519PrivateKey.generate()))
    # Naming the victim's account in the payload changes nothing: scope is the caller.
    await gateway.process_command(ws_x, {"cmd": "set_account_pref", "key": "proxion_theme",
                                         "value": "dark", "webid": account_did})
    await gateway.process_command(ws_x, {"cmd": "get_account_sync", "webid": account_did})
    snap = _of_type(ws_x, "account_sync")[-1]
    assert snap["prefs"] == {"proxion_theme": "dark"}
    assert snap["webid"] != account_did
    assert gateway._store.get_account_sync(account_did) == {"pref:proxion_theme": "light"}


@pytest.mark.asyncio
async def test_device_with_a_forged_cert_gets_no_account_settings(gateway):
    account, device = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    account_did = _did(account)
    ws_p = _mock_ws()
    await _register(gateway, ws_p, account_did, name="Alice")
    await gateway.process_command(ws_p, {"cmd": "set_account_pref",
                                         "key": "proxion_theme", "value": "dark"})
    impostor = Ed25519PrivateKey.generate()
    forged = issue_device_cert(impostor, _did(device))
    forged["account_did"] = account_did
    ws_bad = _mock_ws()
    await _register(gateway, ws_bad, _did(device), cert=forged)
    ws_bad.send.reset_mock()
    await gateway.process_command(ws_bad, {"cmd": "get_account_sync"})
    assert _of_type(ws_bad, "account_sync") == []


@pytest.mark.asyncio
async def test_only_allowlisted_short_prefs_sync(gateway):
    _, ws_p, ws_d = await _account_with_device(gateway)
    ws_d.send.reset_mock()
    for key, value in (("proxion_gateway_http_url", "https://evil"),
                       ("proxion_dm_history", "0"),
                       ("proxion_locale", "x" * 200),
                       ("proxion_theme", 5)):
        await gateway.process_command(ws_p, {"cmd": "set_account_pref", "key": key, "value": value})
    assert _of_type(ws_d, "account_sync") == []
    assert gateway._store.get_account_sync(gateway._client_webids[ws_p]) == {}


@pytest.mark.asyncio
async def test_linked_device_register_does_not_overwrite_the_account_name(gateway):
    account, device = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    account_did, device_did = _did(account), _did(device)
    ws_p, ws_d = _mock_ws(), _mock_ws()
    await _register(gateway, ws_p, account_did, name="Alice")
    await _register(gateway, ws_d, device_did, cert=issue_device_cert(account, device_did),
                    name="Old name on this device")
    assert gateway._store.get_display_name(account_did) == "Alice"
    assert gateway._display_names[ws_d] == "Alice"


@pytest.mark.asyncio
async def test_settings_survive_a_gateway_restart(tmp_path):
    gw = _gateway(tmp_path / "s.db")
    _, ws_p, _ = await _account_with_device(gw)
    owner = gw._client_webids[ws_p]
    await gw.process_command(ws_p, {"cmd": "set_account_pref", "key": "proxion_locale", "value": "de"})
    await gw.process_command(ws_p, {"cmd": "set_thread_mute", "mute_key": "r", "thread_id": "r",
                                    "muted": True})
    gw2 = _gateway(tmp_path / "s.db")
    assert gw2._store.get_account_sync(owner) == {"pref:proxion_locale": "de", "mute:r": "1"}
