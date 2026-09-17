"""Tests: federated room member storage and relay."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def test_add_and_get_federated_room_member(store):
    """add_federated_room_member persists and get returns the member."""
    store.add_federated_room_member("room-1", "did:key:zBob", "https://bob.example.com")
    members = store.get_federated_room_members("room-1")
    assert len(members) == 1
    assert members[0]["member_did"] == "did:key:zBob"
    assert members[0]["gateway_url"] == "https://bob.example.com"


def test_remove_federated_room_member(store):
    """remove_federated_room_member removes a specific member."""
    store.add_federated_room_member("room-2", "did:key:zBob", "https://bob.example.com")
    store.add_federated_room_member("room-2", "did:key:zCarol", "https://carol.example.com")
    store.remove_federated_room_member("room-2", "did:key:zBob")
    members = store.get_federated_room_members("room-2")
    assert len(members) == 1
    assert members[0]["member_did"] == "did:key:zCarol"


def test_upsert_federated_member(store):
    """Adding the same member twice updates rather than duplicates."""
    store.add_federated_room_member("room-3", "did:key:zBob", "https://old.example.com")
    store.add_federated_room_member("room-3", "did:key:zBob", "https://new.example.com")
    members = store.get_federated_room_members("room-3")
    assert len(members) == 1
    assert members[0]["gateway_url"] == "https://new.example.com"


@pytest.mark.asyncio
async def test_announce_room_join_stores_federated_member(gateway):
    """announce_room_join records the caller's home gateway as a federated member."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    room_id = "room-fed-1"
    code = "testcode123"
    caller_webid = "did:key:zBob"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {
        "name": "Test Room", "code": code,
        "members": {ws}, "creator_webid": caller_webid,
    }
    gateway.clients.add(ws)

    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://bob.example.com",
        })

    import json
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    # R30 T1: caller also receives room_member_joined; at minimum federated_room_joined is sent
    assert any(c.get("type") == "federated_room_joined" for c in calls)
    members = gateway._store.get_federated_room_members(room_id)
    assert any(m["member_did"] == caller_webid for m in members)


def test_add_federated_room_member_respects_cap(store):
    """add_federated_room_member refuses a NEW member over max_members; re-recording
    an existing member (URL refresh) is always allowed."""
    assert store.add_federated_room_member("room-cap", "did:key:z1", "https://g1", max_members=2) is True
    assert store.add_federated_room_member("room-cap", "did:key:z2", "https://g2", max_members=2) is True
    # A third distinct member over the cap is refused and not stored.
    assert store.add_federated_room_member("room-cap", "did:key:z3", "https://g3", max_members=2) is False
    assert len(store.get_federated_room_members("room-cap")) == 2
    # Re-recording an existing member updates the URL without tripping the cap.
    assert store.add_federated_room_member("room-cap", "did:key:z1", "https://g1b", max_members=2) is True
    members = {m["member_did"]: m["gateway_url"] for m in store.get_federated_room_members("room-cap")}
    assert members["did:key:z1"] == "https://g1b"
    assert len(members) == 2


@pytest.mark.asyncio
async def test_announce_room_join_binds_callers_known_gateway(gateway):
    """The stored gateway_url is the caller's OWN known gateway, not the
    attacker-controlled announced home_gateway (F4)."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    room_id = "room-fed-bind"
    code = "bindcode123"
    caller_webid = "did:key:zBob"
    gateway._client_webids[ws] = caller_webid
    # The caller's real gateway is known from registration.
    gateway._peer_gateway_urls[caller_webid] = "https://bob-real.example.com"
    gateway._local_rooms[room_id] = {
        "name": "Bind Room", "code": code,
        "members": {ws}, "creator_webid": caller_webid,
    }
    gateway.clients.add(ws)

    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://attacker.evil.example.com",
        })

    members = gateway._store.get_federated_room_members(room_id)
    stored = {m["member_did"]: m["gateway_url"] for m in members}
    assert stored.get(caller_webid) == "https://bob-real.example.com"
    assert "https://attacker.evil.example.com" not in stored.values()


@pytest.mark.asyncio
async def test_announce_room_join_over_cap_rejected(gateway):
    """Announcing more than the cap federated members is rejected and the row
    count stays bounded (F4 unbounded-state fix)."""
    room_id = "room-fed-cap"
    code = "capcode123"
    gateway._local_rooms[room_id] = {"name": "Cap Room", "code": code, "members": set()}
    # Fill the room to a small patched cap with distinct federated dids.
    for i in range(3):
        gateway._store.add_federated_room_member(room_id, f"did:key:zPrefill{i}", f"https://g{i}.example.com")

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    caller_webid = "did:key:zNewcomer"
    gateway._client_webids[ws] = caller_webid
    gateway.clients.add(ws)

    with patch("proxion_messenger_core._gateway_rooms._MAX_ROOM_MEMBERS", 3), \
         patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://newcomer.example.com",
        })

    import json
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert any(c.get("type") == "error" and c.get("message") == "room_full" for c in calls)
    members = gateway._store.get_federated_room_members(room_id)
    assert len(members) == 3
    assert all(m["member_did"] != caller_webid for m in members)


@pytest.mark.asyncio
async def test_announce_room_join_under_cap_works(gateway):
    """A legitimate federated join under the cap is accepted."""
    room_id = "room-fed-under"
    code = "undercode123"
    gateway._local_rooms[room_id] = {"name": "Under Room", "code": code, "members": set()}
    for i in range(2):
        gateway._store.add_federated_room_member(room_id, f"did:key:zPre{i}", f"https://g{i}.example.com")

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    caller_webid = "did:key:zJoiner"
    gateway._client_webids[ws] = caller_webid
    gateway.clients.add(ws)

    with patch("proxion_messenger_core._gateway_rooms._MAX_ROOM_MEMBERS", 5), \
         patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://joiner.example.com",
        })

    import json
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert any(c.get("type") == "federated_room_joined" for c in calls)
    members = gateway._store.get_federated_room_members(room_id)
    assert len(members) == 3
    assert any(m["member_did"] == caller_webid for m in members)


@pytest.mark.asyncio
async def test_room_relay_delivers_to_local_members(gateway):
    """_handle_room_relay delivers message to local members of the target room."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    room_id = "room-fed-2"
    gateway._local_rooms[room_id] = {"name": "Test", "members": {ws}}
    gateway.clients.add(ws)
    # The relay sender must be a known member of the room (the relay no longer
    # fails open on a room with no membership records).
    gateway._store.add_federated_room_member(room_id, "did:key:zAlice", "https://alice.example.com")

    status, _ = await gateway._handle_room_relay({
        "room_id": room_id,
        "from_webid": "did:key:zAlice",
        "message_id": "msg-relay-1",
        "content": "Hello from Alice's gateway",
        "timestamp": "2026-05-24T00:00:00Z",
    })
    assert status.startswith("200")
    ws.send.assert_called_once()
    import json
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["content"] == "Hello from Alice's gateway"


@pytest.mark.asyncio
async def test_restore_rooms_from_pod_records_creator_member(gateway, monkeypatch):
    """F3a: a room restored from the pod records its creator as a member (mirroring
    _hydrate_from_store), so it is not left memberless — which the relay authz path
    would otherwise treat as 'no records'."""
    room_id = "pod-room-1"
    creator = "did:key:zCreator"

    class _FakeStore:
        def __init__(self, client):
            pass
        def list_room_ids(self):
            return [room_id]
        def read_room_meta(self, rid):
            return {"name": "Podroom", "code": "", "creator_webid": creator,
                    "history_mode": "none", "invite_url": ""}

    monkeypatch.setattr("proxion_messenger_core.pod_room_store.PodRoomStore", _FakeStore)
    monkeypatch.setattr(gateway, "_pod_client", lambda: object())

    async def _noop_pins(rid):
        return None
    monkeypatch.setattr(gateway, "_restore_room_pins_from_pod", _noop_pins)

    await gateway._restore_rooms_from_pod()

    assert room_id in gateway._local_rooms
    assert creator in gateway._store.get_room_members(room_id), "creator must be recorded as a member"


@pytest.mark.asyncio
async def test_restored_memberless_room_refuses_relay(gateway, monkeypatch):
    """F3a+b together: a pod-restored room accepts a relay from its recorded
    creator but refuses one from an unknown sender (no fail-open)."""
    room_id = "pod-room-2"
    creator = "did:key:zCreator2"

    class _FakeStore:
        def __init__(self, client):
            pass
        def list_room_ids(self):
            return [room_id]
        def read_room_meta(self, rid):
            return {"name": "Podroom2", "code": "", "creator_webid": creator,
                    "history_mode": "none", "invite_url": ""}

    monkeypatch.setattr("proxion_messenger_core.pod_room_store.PodRoomStore", _FakeStore)
    monkeypatch.setattr(gateway, "_pod_client", lambda: object())

    async def _noop_pins(rid):
        return None
    monkeypatch.setattr(gateway, "_restore_room_pins_from_pod", _noop_pins)

    await gateway._restore_rooms_from_pod()

    stranger_status, _ = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zStranger",
        "message_id": "m-stranger", "content": "x", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert stranger_status.startswith("403")

    ok_status, _ = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": creator,
        "message_id": "m-creator", "content": "hi", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert ok_status.startswith("200")
