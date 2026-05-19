"""Unit tests for voice.py — voice signaling stub."""

import pytest
import json
import uuid
from unittest.mock import MagicMock, patch

from proxion_messenger_core.voice import (
    VoiceInvite, signal_voice_invite, receive_voice_invites,
)


@pytest.fixture
def mock_cert():
    """Mock RelationshipCertificate."""
    return MagicMock()


@pytest.fixture
def mock_pod_client():
    """Mock SolidClient."""
    client = MagicMock()
    client._resolver = MagicMock()
    client._resolver.pod_base_url = "http://localhost:3001/alice/"
    return client


def test_voice_invite_structure():
    """Test VoiceInvite dataclass."""
    invite = VoiceInvite(
        session_id="session-123",
        caller_webid="http://localhost:3001/alice/profile/card#me",
        room_id=None,
        sdp_offer="v=0\n...",
        created_at="2026-04-11T10:00:00+00:00",
    )
    
    assert invite.session_id == "session-123"
    assert invite.caller_webid == "http://localhost:3001/alice/profile/card#me"
    assert invite.room_id is None
    assert "v=0" in invite.sdp_offer


def test_signal_voice_invite_dm_call(mock_cert, mock_pod_client):
    """signal_voice_invite() writes invite to pod for DM call (no room_id)."""
    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send") as mock_send:

        mock_compose.return_value = MagicMock()
        invite = signal_voice_invite(
            mock_cert, mock_pod_client,
            sdp_offer="v=0\no=...",
            session_id="sess-001",
            caller_webid="did:key:alice",
            room_id=None,
        )

        assert invite.room_id is None
        assert invite.session_id == "sess-001"
        mock_compose.assert_called_once()
        mock_send.assert_called_once()


def test_signal_voice_invite_room_call(mock_cert, mock_pod_client):
    """signal_voice_invite() includes room_id for group calls."""
    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send"):

        mock_compose.return_value = MagicMock()
        invite = signal_voice_invite(
            mock_cert, mock_pod_client,
            sdp_offer="v=0\no=...",
            session_id="sess-002",
            caller_webid="did:key:alice",
            room_id="room-xyz",
        )

        assert invite.room_id == "room-xyz"


def test_signal_voice_invite_preserves_session_id(mock_cert, mock_pod_client):
    """signal_voice_invite() uses the caller-supplied session_id, not a generated one."""
    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send"):

        mock_compose.return_value = MagicMock()
        invite = signal_voice_invite(
            mock_cert, mock_pod_client,
            sdp_offer="v=0",
            session_id="my-fixed-session",
            caller_webid="did:key:alice",
        )

        assert invite.session_id == "my-fixed-session"


def test_signal_voice_invite_payload_excludes_room_id_for_dm():
    """signal_voice_invite() omits room_id from JSON when not a group call."""
    mock_cert = MagicMock()
    mock_client = MagicMock()

    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send"):

        mock_compose.return_value = MagicMock()
        signal_voice_invite(
            mock_cert, mock_client,
            sdp_offer="v=0",
            session_id="sess-003",
            caller_webid="did:key:alice",
            room_id=None,
        )

        content = mock_compose.call_args[1]["content"]
        payload = json.loads(content)
        assert "room_id" not in payload


def test_signal_voice_invite_payload_includes_room_id_for_group():
    """signal_voice_invite() includes room_id in JSON for group calls."""
    mock_cert = MagicMock()
    mock_client = MagicMock()

    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send"):

        mock_compose.return_value = MagicMock()
        signal_voice_invite(
            mock_cert, mock_client,
            sdp_offer="v=0",
            session_id="sess-004",
            caller_webid="did:key:alice",
            room_id="room-123",
        )

        content = mock_compose.call_args[1]["content"]
        payload = json.loads(content)
        assert payload.get("room_id") == "room-123"


def test_receive_voice_invites_filters_type(mock_cert):
    """Test receive_voice_invites() filters for type='voice_invite'."""
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        # One voice invite, one other message
        invite_msg = MagicMock()
        invite_msg.content = json.dumps({
            "type": "voice_invite",
            "session_id": "session-abc",
            "caller_webid": "http://localhost:3001/alice/profile/card#me",
            "sdp_offer": "v=0\n...",
            "created_at": "2026-04-11T10:00:00+00:00",
        })
        
        other_msg = MagicMock()
        other_msg.content = "Just a text message"
        
        mock_receive.return_value = [invite_msg, other_msg]
        
        invites = receive_voice_invites(mock_cert, mock_client, mock_agent, b"key")
        
        assert len(invites) == 1
        assert invites[0].session_id == "session-abc"


def test_receive_voice_invites_handles_missing_room_id():
    """Test receive_voice_invites() handles missing room_id (DM calls)."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg = MagicMock()
        msg.content = json.dumps({
            "type": "voice_invite",
            "session_id": "session-123",
            "caller_webid": "http://localhost:3001/alice/profile/card#me",
            "sdp_offer": "v=0",
            "created_at": "2026-04-11T10:00:00+00:00",
            # No room_id
        })
        
        mock_receive.return_value = [msg]
        
        invites = receive_voice_invites(mock_cert, mock_client, mock_agent, b"key")
        
        assert len(invites) == 1
        assert invites[0].room_id is None


def test_receive_voice_invites_skips_malformed():
    """Test receive_voice_invites() skips malformed messages."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        bad_msg = MagicMock()
        bad_msg.content = "not json"
        
        good_msg = MagicMock()
        good_msg.content = json.dumps({
            "type": "voice_invite",
            "session_id": "session-xyz",
            "caller_webid": "http://localhost:3001/alice/profile/card#me",
            "sdp_offer": "v=0",
            "created_at": "2026-04-11T10:00:00+00:00",
        })
        
        mock_receive.return_value = [bad_msg, good_msg]
        
        invites = receive_voice_invites(mock_cert, mock_client, mock_agent, b"key")
        
        # Should skip bad_msg and only get good_msg
        assert len(invites) == 1
        assert invites[0].session_id == "session-xyz"


def test_receive_voice_invites_missing_fields_skipped():
    """Test receive_voice_invites() skips messages with missing required fields."""
    mock_cert = MagicMock()
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        incomplete_msg = MagicMock()
        incomplete_msg.content = json.dumps({
            "type": "voice_invite",
            "session_id": "session-123",
            # Missing caller_webid, sdp_offer, created_at
        })
        
        mock_receive.return_value = [incomplete_msg]
        
        invites = receive_voice_invites(mock_cert, mock_client, mock_agent, b"key")
        

def test_voice_answer_dataclass_fields():
    """Test VoiceAnswer dataclass."""
    from proxion_messenger_core.voice import VoiceAnswer
    ans = VoiceAnswer(session_id="s1", sdp_answer="a1")
    assert ans.session_id == "s1"
    assert ans.sdp_answer == "a1"

def test_ice_candidate_dataclass_fields():
    """Test IceCandidate dataclass."""
    from proxion_messenger_core.voice import IceCandidate
    can = IceCandidate(session_id="s1", candidate="c1", sdp_mid="0")
    assert can.session_id == "s1"
    assert can.candidate == "c1"
    assert can.sdp_mid == "0"

def test_signal_voice_answer_sends_message(mock_cert, mock_pod_client):
    """Test signal_voice_answer() sends correct JSON message."""
    from proxion_messenger_core.voice import signal_voice_answer
    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send") as mock_send:
        
        signal_voice_answer(mock_cert, mock_pod_client, "s1", "a1")
        
        mock_compose.assert_called_once()
        content = mock_compose.call_args[1]["content"]
        payload = json.loads(content)
        assert payload["type"] == "voice_answer"
        assert payload["session_id"] == "s1"
        assert payload["sdp_answer"] == "a1"
        mock_send.assert_called_once()

def test_signal_ice_candidate_sends_message(mock_cert, mock_pod_client):
    """Test signal_ice_candidate() sends correct JSON message."""
    from proxion_messenger_core.voice import signal_ice_candidate
    with patch("proxion_messenger_core.messaging.compose") as mock_compose, \
         patch("proxion_messenger_core.messaging.send") as mock_send:
        
        signal_ice_candidate(mock_cert, mock_pod_client, "s1", "c1", sdp_mid="mid")
        
        mock_compose.assert_called_once()
        content = mock_compose.call_args[1]["content"]
        payload = json.loads(content)
        assert payload["type"] == "ice_candidate"
        assert payload["session_id"] == "s1"
        assert payload["candidate"] == "c1"
        assert payload["sdp_mid"] == "mid"
        mock_send.assert_called_once()

def test_receive_voice_answers_filters_type(mock_cert):
    """Test receive_voice_answers() filters for type='voice_answer'."""
    # This might fail if the function is not yet implemented
    from proxion_messenger_core.voice import receive_voice_answers
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        msg = MagicMock()
        msg.content = json.dumps({
            "type": "voice_answer",
            "session_id": "s1",
            "sdp_answer": "a1"
        })
        mock_receive.return_value = [msg]
        
        answers = receive_voice_answers(mock_cert, mock_client, mock_agent, b"key")
        assert len(answers) == 1
        assert answers[0].session_id == "s1"

def test_receive_ice_candidates_session_filter(mock_cert):
    """Test receive_ice_candidates() filters by session_id."""
    # This might fail if the function is not yet implemented
    from proxion_messenger_core.voice import receive_ice_candidates
    mock_client = MagicMock()
    mock_agent = MagicMock()
    
    with patch("proxion_messenger_core.messaging.receive") as mock_receive:
        c1 = MagicMock()
        c1.content = json.dumps({"type": "ice_candidate", "session_id": "s1", "candidate": "can1"})
        c2 = MagicMock()
        c2.content = json.dumps({"type": "ice_candidate", "session_id": "s2", "candidate": "can2"})
        
        mock_receive.return_value = [c1, c2]
        
        candidates = receive_ice_candidates(mock_cert, mock_client, mock_agent, b"key", session_id="s1")
        assert len(candidates) == 1
        assert candidates[0].candidate == "can1"
