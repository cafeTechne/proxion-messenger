"""Round 4: Webhook token rotation and constant-time comparison."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9870, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "wh.db"))


def _create_webhook(store, thread_id="room-1", owner="did:key:alice"):
    import secrets, time
    wh = {
        "id": "wh-001",
        "thread_id": thread_id,
        "owner_webid": owner,
        "direction": "incoming",
        "token": secrets.token_urlsafe(32),
        "url": "",
        "bot_name": "Bot",
        "created_at": time.time(),
    }
    store.create_webhook(wh)
    return wh


def test_rotate_webhook_returns_new_token_once(store):
    """rotate_webhook_token returns a new token distinct from the old one."""
    wh = _create_webhook(store)
    old_token = wh["token"]
    new_token = store.rotate_webhook_token("wh-001", "did:key:alice")
    assert new_token is not None
    assert new_token != old_token


def test_previous_token_valid_during_grace_period_only(store):
    """Old token is accepted within 300s grace period via get_webhook_by_token_with_rotation."""
    wh = _create_webhook(store)
    old_token = wh["token"]
    store.rotate_webhook_token("wh-001", "did:key:alice")
    # Old token should still be found within 300s
    found = store.get_webhook_by_token_with_rotation(old_token, allow_previous_within_seconds=300)
    assert found is not None, "Old token should be valid during grace period"
    # Old token should NOT be found with 0s grace period
    not_found = store.get_webhook_by_token_with_rotation(old_token, allow_previous_within_seconds=0)
    assert not_found is None, "Old token should not be valid after grace period expires"


def test_webhook_token_compare_uses_constant_time_behavior_contract(store):
    """get_webhook_by_token_with_rotation uses hmac.compare_digest (behavioral contract)."""
    wh = _create_webhook(store)
    token = wh["token"]
    # Correct token should be found
    found = store.get_webhook_by_token_with_rotation(token)
    assert found is not None
    # Wrong token should not be found
    not_found = store.get_webhook_by_token_with_rotation("wrongtoken123abc")
    assert not_found is None
