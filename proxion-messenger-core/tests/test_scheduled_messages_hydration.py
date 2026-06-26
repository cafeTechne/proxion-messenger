"""Tests: scheduler loop reads due messages from SQLite."""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=GatewayConfig(port=9990, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def test_get_due_scheduled_messages_returns_pending(gateway):
    """Scheduler reads pending messages directly from SQLite."""
    webid = "https://alice.pod/profile/card#me"
    past_time = time.time() - 60  # 60s ago
    gateway._store.save_scheduled_message({
        "id": "sched-1", "thread_id": "thread-1", "from_webid": webid,
        "content": "Due message", "send_at": past_time, "created_at": past_time,
    })
    due = gateway._store.get_due_scheduled_messages(time.time())
    assert any(m.get("id") == "sched-1" for m in due)


def test_future_scheduled_messages_not_due(gateway):
    """Messages scheduled for the future are not returned by get_due."""
    webid = "https://alice.pod/profile/card#me"
    future_time = time.time() + 3600  # 1 hour from now
    gateway._store.save_scheduled_message({
        "id": "sched-future", "thread_id": "thread-1", "from_webid": webid,
        "content": "Future message", "send_at": future_time, "created_at": future_time,
    })
    due = gateway._store.get_due_scheduled_messages(time.time())
    assert not any(m.get("id") == "sched-future" for m in due)


def test_mark_scheduled_delivered_removes_from_due(gateway):
    """After marking delivered, message no longer appears in due list."""
    webid = "https://alice.pod/profile/card#me"
    past_time = time.time() - 60
    gateway._store.save_scheduled_message({
        "id": "sched-del", "thread_id": "thread-1", "from_webid": webid,
        "content": "Will be delivered", "send_at": past_time, "created_at": past_time,
    })
    gateway._store.mark_scheduled_delivered("sched-del")
    due = gateway._store.get_due_scheduled_messages(time.time())
    assert not any(m.get("id") == "sched-del" for m in due)
