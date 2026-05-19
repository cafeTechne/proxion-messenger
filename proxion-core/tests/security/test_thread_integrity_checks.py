"""Tests for R7 thread integrity checks on get_local_history and get_message."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def store(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    return LocalStore(str(tmp_path / "integrity.db"))


@pytest.fixture
def gateway(tmp_path, store):
    import os
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "gw.db")),
    )
    return gw


def make_ws(webid="did:key:z6MkTest"):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("127.0.0.1", 9000)
    return ws


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestThreadIntegrityChecks:
    def test_schema_has_thread_integrity_table(self, store):
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thread_integrity_state'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_integrity_state_upsert_and_retrieve(self, store):
        import time
        store.upsert_thread_integrity_state("t1", 10, "hash10", time.time())
        s = store.get_thread_integrity_state("t1")
        assert s["last_seq_num"] == 10
        assert s["last_prev_hash"] == "hash10"

    def test_integrity_state_updated_on_history_fetch(self, gateway):
        """After fetching history, thread integrity state is updated."""
        identity = "did:key:z6MkTestA"
        ws = make_ws(identity)
        gateway._client_webids[ws] = identity
        gateway._session_meta[ws] = {"ip_addr": "127.0.0.1", "user_agent_hash": ""}
        room_id = "room-integrity-test"
        gateway._local_rooms[room_id] = {
            "name": "Test", "code": "code1", "members": {ws},
            "invite_url": "", "history_mode": "none", "creator_webid": identity,
        }
        # Save messages with seq_nums
        import time
        gw_store = gateway._store
        gw_store.save_message("msg1", room_id, "room", identity, None, "hello", "2024-01-01T00:00:00Z",
                               seq_num=1, prev_hash="")
        gw_store.save_message("msg2", room_id, "room", identity, None, "world", "2024-01-01T00:01:00Z",
                               seq_num=2, prev_hash="hash1")
        run(gateway._handle_get_local_history(ws, {"thread_id": room_id}))
        state = gw_store.get_thread_integrity_state(room_id)
        assert state is not None
        assert state["last_seq_num"] >= 0

    def test_history_flags_seq_num_gap(self, gateway):
        """Non-monotonic seq_num triggers integrity_warning in response."""
        identity = "did:key:z6MkTestB"
        ws = make_ws(identity)
        gateway._client_webids[ws] = identity
        gateway._session_meta[ws] = {"ip_addr": "127.0.0.1", "user_agent_hash": ""}
        room_id = "room-seq-gap"
        gateway._local_rooms[room_id] = {
            "name": "Test", "code": "code2", "members": {ws},
            "invite_url": "", "history_mode": "none", "creator_webid": identity,
        }
        gw_store = gateway._store
        # seq_num goes 5 -> 3 (gap — not monotonic)
        gw_store.save_message("msg-a", room_id, "room", identity, None, "a", "2024-01-01T00:00:00Z",
                               seq_num=5, prev_hash="")
        gw_store.save_message("msg-b", room_id, "room", identity, None, "b", "2024-01-01T00:01:00Z",
                               seq_num=3, prev_hash="")
        run(gateway._handle_get_local_history(ws, {"thread_id": room_id}))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        history_resp = [c for c in calls if c.get("type") == "local_history"]
        assert len(history_resp) >= 1
        resp = history_resp[-1]
        assert "integrity_warning" in resp
        assert resp["integrity_warning"]["type"] == "seq_num"

    def test_integrity_break_emits_security_event(self, gateway):
        """A seq_num gap saves a security event."""
        identity = "did:key:z6MkTestC"
        ws = make_ws(identity)
        gateway._client_webids[ws] = identity
        gateway._session_meta[ws] = {"ip_addr": "127.0.0.1", "user_agent_hash": ""}
        room_id = "room-sec-event"
        gateway._local_rooms[room_id] = {
            "name": "Test", "code": "code3", "members": {ws},
            "invite_url": "", "history_mode": "none", "creator_webid": identity,
        }
        gw_store = gateway._store
        gw_store.save_message("msg-x", room_id, "room", identity, None, "x", "2024-01-01T00:00:00Z",
                               seq_num=10, prev_hash="")
        gw_store.save_message("msg-y", room_id, "room", identity, None, "y", "2024-01-01T00:01:00Z",
                               seq_num=5, prev_hash="")
        run(gateway._handle_get_local_history(ws, {"thread_id": room_id}))
        events = gw_store.get_security_events(event_type="thread_integrity_break")
        assert len(events) >= 1
