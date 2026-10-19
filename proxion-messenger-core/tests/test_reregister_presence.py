"""A socket that registers again as another identity leaves the first one.

Before the fix the socket stayed in the old identity's socket and presence
sets: that identity showed online forever (even after the tab closed) and
deliveries for it went to a socket now speaking for someone else.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


class _WS:
    def __init__(self, msgs):
        self.q = asyncio.Queue()
        for m in msgs:
            self.q.put_nowait(m)
        self.sent = []
        self.remote_address = ("127.0.0.1", 1234)
        self.request = None

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def close(self, *a):
        self.q.put_nowait(None)

    def push(self, m):
        self.q.put_nowait(m)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return json.dumps(m)


@pytest.mark.asyncio
async def test_reregister_as_another_identity_leaves_the_old_one(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))
    watcher = AsyncMock()
    watcher.remote_address = ("127.0.0.1", 1)
    gw.clients.add(watcher)
    await gw.process_command(watcher, {"cmd": "register", "did": _did(), "display_name": "W"})

    first, second = _did(), _did()
    ws = _WS([{"cmd": "register", "did": first, "display_name": "A"},
              {"cmd": "set_presence", "status": "online"},
              {"cmd": "register", "did": second, "display_name": "A2"}])
    task = asyncio.create_task(gw.handle_client(ws))
    await asyncio.sleep(0.3)

    # Still connected, now as `second` only.
    assert ws not in gw._webid_sockets.get(first, set())
    assert first not in gw._presence_by_identity
    assert gw._user_presence.get(first, {}).get("status") == "offline"
    assert ws in gw._webid_sockets.get(second, set())

    ws.push(None)
    await asyncio.wait_for(task, 5)
    await asyncio.sleep(0.05)
    assert first not in gw._presence_by_identity
    assert second not in gw._presence_by_identity
    assert gw._user_presence.get(first, {}).get("status") != "online"


@pytest.mark.asyncio
async def test_going_offline_reaches_contacts_only(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))
    leaver, friend, stranger = _did(), _did(), _did()
    gw._store.save_relationship(
        {"certificate_id": "cert-F", "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=friend, owner_webid=leaver)
    socks = {}
    for did in (friend, stranger):
        w = AsyncMock()
        w.remote_address = ("127.0.0.1", 1)
        gw.clients.add(w)
        await gw.process_command(w, {"cmd": "register", "did": did, "display_name": "x"})
        socks[did] = w
    ws = _WS([{"cmd": "register", "did": leaver, "display_name": "L"},
              {"cmd": "set_status_message", "status_message": "at the dentist"}])
    task = asyncio.create_task(gw.handle_client(ws))
    await asyncio.sleep(0.3)
    for w in socks.values():
        w.send.reset_mock()
    ws.push(None)   # tab closes
    await asyncio.wait_for(task, 5)
    await asyncio.sleep(0.2)

    def offline_for(w):
        return [json.loads(c.args[0]) for c in w.send.call_args_list
                if json.loads(c.args[0]).get("type") == "presence_update"
                and json.loads(c.args[0]).get("webid") == leaver]
    assert offline_for(socks[friend]) and offline_for(socks[friend])[-1]["status"] == "offline"
    assert offline_for(socks[stranger]) == []
