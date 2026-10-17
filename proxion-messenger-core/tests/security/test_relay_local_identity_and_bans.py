"""Room relay authorization: local identities, bans and stale admin roles.

- A federated member's gateway must not claim a LOCAL identity (the room owner,
  a local member, our own gateway DID) as from_webid: the first-use relaygw seed
  used to bind it to the attacker's key, after which privileged relays passed.
- Banning or kicking a cross-gateway member must drop their federated row so
  room fanout stops reaching their gateway, and a banned DID cannot join voice.
- A relayed room_update needs the admin to still be a member; roles can only be
  given to members.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.relay import sign_relay_envelope


def _gw(tmp_path, name="host"):
    return ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                          config=GatewayConfig(host="127.0.0.1",
                                               db_path=str(tmp_path / f"{name}.db")))


def _did_of(agent):
    return pub_key_to_did(agent.identity_pub_bytes)


def _signed(signer_agent, payload):
    payload = dict(payload)
    payload["relay_sig_did"] = _did_of(signer_agent)
    payload["relay_ts"] = datetime.now(timezone.utc).isoformat()
    payload["relay_nonce"] = secrets.token_hex(8)
    payload["signature"] = sign_relay_envelope(signer_agent.identity_key, payload)
    return json.dumps(payload).encode()


def _room(creator, members=()):
    return {"name": "Team", "code": "x", "members": set(members), "invite_url": "",
            "history_mode": "none", "messages": [], "creator_webid": creator}


def _setup_owner_room(tmp_path):
    host = _gw(tmp_path)
    attacker = AgentState.generate()          # a federated member's home gateway
    fed_member = _did_of(AgentState.generate())
    owner_did = _did_of(AgentState.generate())
    victim_did = _did_of(AgentState.generate())
    member_ws = AsyncMock()
    rid = "room-x"
    host._local_rooms[rid] = _room(owner_did, [member_ws])
    host._store.add_room_member(rid, owner_did)
    host._store.add_room_member(rid, victim_did)
    host._store.add_federated_room_member(rid, fed_member, "https://attacker.example")
    return host, attacker, fed_member, owner_did, victim_did, member_ws, rid


@pytest.mark.asyncio
async def test_member_gateway_cannot_impersonate_local_owner(tmp_path):
    host, attacker, _fed, owner_did, victim_did, member_ws, rid = _setup_owner_room(tmp_path)
    # Ordinary message claiming the owner: must not seed relaygw:<owner>.
    await host._handle_relay_post(_signed(attacker, {
        "content_type": "room_message", "room_id": rid, "from_webid": owner_did,
        "message_id": "m1", "content": "forged", "timestamp": "t"}))
    assert not host._store.get_identity_key_history("relaygw:" + owner_did)
    await host._handle_relay_post(_signed(attacker, {
        "content_type": "room_update", "room_id": rid, "from_webid": owner_did,
        "name": "PWNED", "topic": "owned"}))
    await host._handle_relay_post(_signed(attacker, {
        "content_type": "room_moderation", "room_id": rid, "from_webid": owner_did,
        "action": "ban", "webid": victim_did}))
    assert host._local_rooms[rid]["name"] == "Team"
    assert not host._store.is_room_banned(rid, victim_did)
    assert member_ws.send.await_count == 0


@pytest.mark.asyncio
async def test_member_gateway_cannot_claim_local_member_or_gateway(tmp_path):
    host, attacker, _fed, _owner, victim_did, member_ws, rid = _setup_owner_room(tmp_path)
    for i, who in enumerate((victim_did, host._own_gateway_did())):
        await host._handle_relay_post(_signed(attacker, {
            "content_type": "room_message", "room_id": rid, "from_webid": who,
            "message_id": f"m{i}", "content": "forged", "timestamp": "t"}))
        assert not host._store.get_identity_key_history("relaygw:" + who)
    assert member_ws.send.await_count == 0


@pytest.mark.asyncio
async def test_established_binding_for_local_identity_is_ignored(tmp_path):
    """A binding left over from before the fix must not keep authorizing it."""
    host, attacker, _fed, owner_did, _victim, _ws, rid = _setup_owner_room(tmp_path)
    host._store.record_identity_key_seen(
        "relaygw:" + owner_did, attacker.identity_pub_bytes.hex(), trusted=True)
    await host._handle_relay_post(_signed(attacker, {
        "content_type": "room_update", "room_id": rid, "from_webid": owner_did,
        "name": "PWNED", "topic": ""}))
    assert host._local_rooms[rid]["name"] == "Team"


@pytest.mark.asyncio
async def test_federated_member_first_relay_still_delivers(tmp_path):
    host, attacker, fed_member, _owner, _victim, member_ws, rid = _setup_owner_room(tmp_path)
    await host._handle_relay_post(_signed(attacker, {
        "content_type": "room_message", "room_id": rid, "from_webid": fed_member,
        "message_id": "m-ok", "content": "hello", "timestamp": "t"}))
    assert member_ws.send.await_count == 1
    assert host._store.get_identity_key_history("relaygw:" + fed_member)


@pytest.mark.asyncio
async def test_federated_member_not_seeded_from_another_known_gateway(tmp_path):
    """When we know the signer's URL, it must be the member's recorded gateway."""
    host, attacker, fed_member, _owner, _victim, member_ws, rid = _setup_owner_room(tmp_path)
    other = AgentState.generate()
    host._peer_gateway_urls[_did_of(other)] = "https://other.example"
    await host._handle_relay_post(_signed(other, {
        "content_type": "room_message", "room_id": rid, "from_webid": fed_member,
        "message_id": "m-x", "content": "forged", "timestamp": "t"}))
    assert member_ws.send.await_count == 0
    assert not host._store.get_identity_key_history("relaygw:" + fed_member)
    host._peer_gateway_urls[_did_of(attacker)] = "https://attacker.example/"
    await host._handle_relay_post(_signed(attacker, {
        "content_type": "room_message", "room_id": rid, "from_webid": fed_member,
        "message_id": "m-y", "content": "hi", "timestamp": "t"}))
    assert member_ws.send.await_count == 1


def _ban_room(tmp_path):
    gw = _gw(tmp_path)
    owner = AsyncMock()
    owner_did = _did_of(AgentState.generate())
    gw._client_webids[owner] = owner_did
    rid = "room1"
    gw._local_rooms[rid] = _room(owner_did, [owner])
    gw._store.add_room_member(rid, owner_did)
    bad = _did_of(AgentState.generate())
    gw._store.add_room_member(rid, bad)
    gw._store.add_federated_room_member(rid, bad, "https://evil.example")
    return gw, owner, rid, bad


async def _fanout_targets(gw, owner, rid):
    sent = []

    async def fake_relay_msg(gw_url, room_id, event):
        sent.append(gw_url)

    async def fake_eph(gw_url, payload):
        sent.append(gw_url)
    with patch.object(gw, "_relay_room_message", new=fake_relay_msg), \
         patch.object(gw, "_relay_ephemeral", new=fake_eph):
        await gw._handle_send_room(owner, {"room_id": rid, "content": "secret plans"})
        await gw._handle_update_room(owner, {"room_id": rid, "name": "R2", "topic": "t"})
        await asyncio.sleep(0.1)
    return sent


@pytest.mark.asyncio
async def test_banned_federated_member_gets_no_relays(tmp_path):
    gw, owner, rid, bad = _ban_room(tmp_path)
    await gw._handle_ban_member(owner, {"room_id": rid, "webid": bad, "reason": "spam"})
    assert gw._store.is_room_banned(rid, bad)
    assert gw._store.get_federated_room_members(rid) == []
    assert "https://evil.example" not in await _fanout_targets(gw, owner, rid)


@pytest.mark.asyncio
async def test_kicked_federated_member_gets_no_relays(tmp_path):
    gw, owner, rid, bad = _ban_room(tmp_path)
    await gw._handle_kick_member(owner, {"room_id": rid, "webid": bad})
    assert gw._store.get_federated_room_members(rid) == []
    assert "https://evil.example" not in await _fanout_targets(gw, owner, rid)


@pytest.mark.asyncio
async def test_relayed_ban_drops_federated_row(tmp_path):
    """A ban applied from another gateway's relay also stops our fanout."""
    gw, _owner, rid, bad = _ban_room(tmp_path)
    gw._store.ban_room_member(rid, bad, "someone")
    assert gw._store.get_federated_room_members(rid) == []


@pytest.mark.asyncio
async def test_banned_did_cannot_join_voice_over_relay(tmp_path):
    gw, _owner, rid, bad = _ban_room(tmp_path)
    gw._store.ban_room_member(rid, bad, "owner")
    # Even if a stale membership row remains, the ban wins.
    gw._store.add_room_member(rid, bad)
    status, _ = await gw._handle_voice_channel_join_relay({
        "channel_id": rid, "from_webid": bad, "origin_gateway_url": "https://evil.example"})
    assert status.startswith("403")
    assert bad not in gw._voice_channels.get(rid, {}).get("members", {})


@pytest.mark.asyncio
async def test_room_update_relay_needs_admin_to_be_member(tmp_path):
    gw = _gw(tmp_path)
    owner_did = _did_of(AgentState.generate())
    ex_admin = _did_of(AgentState.generate())
    rid = "room-adm"
    gw._local_rooms[rid] = _room(owner_did)
    gw._store.add_room_member(rid, owner_did)
    gw._store.set_room_role(rid, ex_admin, "admin")   # stale role, not a member
    status, _ = await gw._handle_room_update_relay({
        "room_id": rid, "from_webid": ex_admin, "name": "Hijacked", "topic": ""})
    assert status.startswith("403")
    assert gw._local_rooms[rid]["name"] == "Team"
    gw._store.add_room_member(rid, ex_admin)
    status, _ = await gw._handle_room_update_relay({
        "room_id": rid, "from_webid": ex_admin, "name": "Renamed", "topic": ""})
    assert status.startswith("200")


@pytest.mark.asyncio
async def test_set_member_role_requires_membership(tmp_path):
    gw = _gw(tmp_path)
    owner = AsyncMock()
    owner_did = _did_of(AgentState.generate())
    gw._client_webids[owner] = owner_did
    rid = "room-role"
    gw._local_rooms[rid] = _room(owner_did, [owner])
    gw._store.add_room_member(rid, owner_did)
    outsider = _did_of(AgentState.generate())
    await gw._handle_set_member_role(owner, {"room_id": rid, "webid": outsider, "role": "admin"})
    assert gw._store.get_room_role(rid, outsider) != "admin"
    member = _did_of(AgentState.generate())
    gw._store.add_room_member(rid, member)
    await gw._handle_set_member_role(owner, {"room_id": rid, "webid": member, "role": "admin"})
    assert gw._store.get_room_role(rid, member) == "admin"
