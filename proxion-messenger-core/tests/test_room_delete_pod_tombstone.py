"""A room deleted with a pod configured must not come back from the pod on the
next start (with its invite code still live)."""
import asyncio
import json
import time

import pytest

pytest.importorskip("websockets")
from gwharness import free_port, start_gateway
from e2e.helpers import connect_and_register

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.solid_client import SolidError


class _FakePod:
    """In-memory DPoP SolidClient stand-in: put/get/delete + one-level list."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def put(self, uri, data, content_type=None):
        self.store[uri] = data if isinstance(data, (bytes, bytearray)) else str(data).encode()

    def get(self, uri):
        if uri not in self.store:
            raise SolidError(f"404 {uri}", status_code=404)
        return self.store[uri]

    def delete(self, uri):
        self.store.pop(uri, None)

    def list(self, prefix):
        children = set()
        for k in self.store:
            if k.startswith(prefix) and k != prefix:
                rest = k[len(prefix):]
                children.add(prefix + rest.split("/", 1)[0] + ("/" if "/" in rest else ""))
        return sorted(children)


def _attach(gw, pod):
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (object(), pod)


def _new_gateway(db_path):
    return ProxionGateway(AgentState.generate(), {}, {},
                          GatewayConfig(host="127.0.0.1", db_path=str(db_path)), ReadState())


def _seed_room(pod, room_id, code, creator):
    pod.put(f"stash://pod/rooms/{room_id}/room.json", json.dumps({
        "room_id": room_id, "name": "Pod room", "code": code,
        "creator_webid": creator, "history_mode": "none", "invite_url": "",
    }).encode())
    pod.put(f"stash://pod/rooms/{room_id}/messages/m1.json",
            json.dumps({"message_id": "m1", "content": "secret"}).encode())


async def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


async def _cold_restore(tmp_path, pod, name):
    gw2 = _new_gateway(tmp_path / name)
    _attach(gw2, pod)
    await gw2._restore_rooms_from_pod()
    return gw2


def _serve(tmp_path, pod=None):
    ws_port, http_port = free_port(), free_port()
    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                                      db_path=str(tmp_path / "gw.db")), ReadState())
    if pod is not None:
        _attach(gw, pod)
    handle = start_gateway(gw, ws_port, http_port)
    return gw, handle, f"ws://127.0.0.1:{ws_port}"


@pytest.mark.parametrize("how", ["delete_room", "leave_local_room"])
@pytest.mark.asyncio
async def test_deleted_room_is_not_restored_from_pod(tmp_path, how):
    pod = _FakePod()
    gw, handle, url = _serve(tmp_path, pod)
    alice = await connect_and_register(url, "Alice", AgentState.generate())
    await alice.send(cmd="chat_room_create", name="Gone")
    created = await alice.recv_type("room_created")
    room_id = created["room_id"]
    stored_code = gw._local_rooms[room_id]["code"]
    _seed_room(pod, room_id, stored_code, alice.did)

    await alice.send(cmd=how, room_id=room_id)
    meta_uri = f"stash://pod/rooms/{room_id}/room.json"
    assert await _wait(lambda: json.loads(pod.store[meta_uri]).get("deleted") is True)
    assert await _wait(lambda: f"stash://pod/rooms/{room_id}/messages/m1.json" not in pod.store)
    assert await _wait(lambda: not gw._store.list_pending_pod_ops())
    await alice.ws.close()

    gw2 = await _cold_restore(tmp_path, pod, "cold.db")
    assert room_id not in gw2._local_rooms
    assert stored_code not in gw2._room_codes
    assert all(r["room_id"] != room_id for r in gw2._store.get_all_rooms())


@pytest.mark.asyncio
async def test_room_deleted_while_pod_offline_stays_deleted(tmp_path):
    pod = _FakePod()
    gw, handle, url = _serve(tmp_path)  # pod not connected yet
    alice = await connect_and_register(url, "Alice", AgentState.generate())
    await alice.send(cmd="chat_room_create", name="Gone")
    room_id = (await alice.recv_type("room_created"))["room_id"]
    _seed_room(pod, room_id, gw._local_rooms[room_id]["code"], alice.did)
    await alice.send(cmd="delete_room", room_id=room_id)
    assert await _wait(lambda: room_id not in gw._local_rooms)
    await alice.ws.close()
    handle.stop()

    # Restart on the same database; the pod is reachable again.
    gw2 = _new_gateway(tmp_path / "gw.db")
    _attach(gw2, pod)
    await gw2._restore_rooms_from_pod()
    assert room_id not in gw2._local_rooms
    await gw2._flush_pending_pod_ops()
    meta = json.loads(pod.store[f"stash://pod/rooms/{room_id}/room.json"])
    assert meta["deleted"] is True and "code" not in meta

    gw3 = await _cold_restore(tmp_path, pod, "cold.db")
    assert room_id not in gw3._local_rooms
