"""Reading room history from the pod: parallel GETs, no refetch of cached
messages, and the register-time backfill runs once per room per process."""
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.pod_room_store import PodRoomStore

ROOM = "room-abc"


class SlowPod:
    """Fake SolidClient: every GET takes `delay` seconds."""

    def __init__(self, n, delay=0.05):
        self.delay = delay
        self.files = {
            f"stash://pod/rooms/{ROOM}/messages/m{i}.json": {
                "message_id": f"m{i}", "content": f"c{i}",
                "timestamp": f"2026-01-01T00:00:{i:02d}+00:00", "from_webid": "did:a",
            }
            for i in range(n)
        }
        self.gets = []
        self.lists = 0
        self._lock = threading.Lock()

    def list(self, uri):
        self.lists += 1
        return list(self.files)

    def get(self, uri):
        with self._lock:
            self.gets.append(uri)
        time.sleep(self.delay)
        return json.dumps(self.files[uri]).encode()


def test_read_messages_fetches_files_in_parallel():
    pod = SlowPod(30, delay=0.05)
    t = time.perf_counter()
    msgs = PodRoomStore(pod).read_messages(ROOM)
    elapsed = time.perf_counter() - t
    assert [m["message_id"] for m in msgs] == [f"m{i}" for i in range(30)]
    # Sequential would be 1.5 s.
    assert elapsed < 0.75, elapsed


def test_read_messages_skips_files_already_cached():
    pod = SlowPod(10, delay=0)
    msgs = PodRoomStore(pod).read_messages(ROOM, skip_message=lambda mid: mid != "m7")
    assert [m["message_id"] for m in msgs] == ["m7"]
    assert pod.gets == [f"stash://pod/rooms/{ROOM}/messages/m7.json"]


@pytest.mark.asyncio
async def test_backfill_once_per_room_and_only_uncached(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))
    gw._store = LocalStore(str(tmp_path / "g.db"))
    gw._store.save_message("m0", ROOM, "room", "did:a", None, "c0", "2026-01-01T00:00:00+00:00")
    pod = SlowPod(5, delay=0)
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (MagicMock(), pod)

    await gw._backfill_rooms_from_pod([ROOM])
    assert len(gw._store.get_messages(ROOM, limit=50)) == 5
    assert f"stash://pod/rooms/{ROOM}/messages/m0.json" not in pod.gets
    assert len(pod.gets) == 4

    # A second register (another tab, a reconnect) does not re-read the room.
    await gw._backfill_rooms_from_pod([ROOM])
    assert pod.lists == 1 and len(pod.gets) == 4
