"""Round 2: Centralized authorization helper tests."""
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.authz import (
    is_gateway_owner,
    is_room_member,
    is_room_owner,
    is_dm_participant,
)
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


@pytest.fixture
def agent():
    return AgentState.generate()


def test_is_gateway_owner_matches_own_did(agent):
    """is_gateway_owner returns True for the agent's own DID."""
    own_did = pub_key_to_did(agent.identity_pub_bytes)
    assert is_gateway_owner(agent, own_did)


def test_is_gateway_owner_rejects_other_did(agent):
    """is_gateway_owner returns False for a different DID."""
    assert not is_gateway_owner(agent, "did:key:someoneelse")


def test_room_member_cmd_rejected_for_non_member():
    """is_room_member returns False when websocket not in room members."""
    ws_outsider = MagicMock()
    ws_member = MagicMock()
    local_rooms = {
        "room-001": {
            "members": {ws_member},
            "creator_webid": "did:key:owner",
        }
    }
    result = is_room_member(None, local_rooms, "room-001", "did:key:outsider", websocket=ws_outsider)
    assert not result


def test_room_member_cmd_accepted_for_member():
    """is_room_member returns True when websocket is in room members."""
    ws = MagicMock()
    local_rooms = {
        "room-001": {
            "members": {ws},
            "creator_webid": "did:key:owner",
        }
    }
    result = is_room_member(None, local_rooms, "room-001", "did:key:any", websocket=ws)
    assert result


def test_room_owner_cmd_rejected_for_member_non_owner():
    """is_room_owner returns False when webid is a member but not creator."""
    ws = MagicMock()
    local_rooms = {
        "room-001": {
            "members": {ws},
            "creator_webid": "did:key:real-owner",
        }
    }
    result = is_room_owner(None, local_rooms, "room-001", "did:key:just-a-member")
    assert not result


def test_room_owner_cmd_accepted_for_creator():
    """is_room_owner returns True when webid matches creator_webid."""
    local_rooms = {
        "room-001": {
            "members": set(),
            "creator_webid": "did:key:real-owner",
        }
    }
    result = is_room_owner(None, local_rooms, "room-001", "did:key:real-owner")
    assert result


def test_dm_participant_cmd_rejected_for_unrelated_user():
    """is_dm_participant returns False when store has no threads for webid."""
    store = MagicMock()
    store.get_dm_threads.return_value = []
    result = is_dm_participant(store, "thread-xyz", "did:key:unrelated")
    assert not result


def test_dm_participant_cmd_accepted_when_thread_present():
    """is_dm_participant returns True when thread_id is in webid's threads."""
    store = MagicMock()
    store.get_dm_threads.return_value = [{"thread_id": "thread-xyz"}]
    result = is_dm_participant(store, "thread-xyz", "did:key:participant")
    assert result


def test_is_dm_participant_returns_false_without_store():
    """is_dm_participant returns False when store is None."""
    assert not is_dm_participant(None, "thread-xyz", "did:key:user")
